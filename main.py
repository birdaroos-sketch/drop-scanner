"""
Drop Scanner — always-on hidden SKU monitor for Railway.

Architecture:
  - Background asyncio loop polls Algolia every config["interval"] seconds
    (server-side, no CSP/CORS limits — that was the whole problem with the
    browser artifact).
  - Results persisted in SQLite so they survive restarts/redeploys.
  - Runtime config (keywords, interval, max_results, webhook, paused) is stored
    in SQLite too, so edits from the dashboard survive restarts. Env vars only
    seed the defaults on first boot with an empty DB.
  - Discord webhook fires when a NEW hidden SKU appears or a tracked SKU changes
    status (e.g. Embargo -> ComingSoon -> Available = the drop signal).
  - Dashboard served from the same origin, so its fetch() to /api/* has no CORS.

Env vars (seed first-boot defaults; after that the dashboard is the source of truth):
  ALGOLIA_APP_ID     required   e.g. VTVKM5URPX
  ALGOLIA_API_KEY    required   e.g. a0c0108d737ad5ab54a0e2da900bf040
  ALGOLIA_INDEX      optional   default shopify_products_families
  KEYWORDS           optional   comma-separated, default "pokemon tcg,delta reign"
  SCAN_INTERVAL      optional   seconds, default 300 (5 min)
  MAX_RESULTS        optional   per keyword query, default 200
  DISCORD_WEBHOOK    optional   full webhook URL for alerts
  DASHBOARD_TOKEN    optional   if set, dashboard + all write endpoints require ?token=...
"""

import asyncio
import csv
import io
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

# ── static config (never editable at runtime) ───────────────────────────────

ALGOLIA_APP_ID  = os.environ.get("ALGOLIA_APP_ID", "").strip()
ALGOLIA_API_KEY = os.environ.get("ALGOLIA_API_KEY", "").strip()
ALGOLIA_INDEX   = os.environ.get("ALGOLIA_INDEX", "shopify_products_families").strip()
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()
PORT            = int(os.environ.get("PORT", "8000"))

# Railway gives a persistent volume mount at /data if you attach one; fall back to cwd.
DB_DIR  = Path("/data") if Path("/data").is_dir() else Path(".")
DB_PATH = DB_DIR / "scanner.db"

ATTRS = "sku,title,price,published_at,release_date,isPublic,availability,product,tags,handle"

# Bounds so a bad dashboard input can't wedge the loop.
MIN_INTERVAL = 15
MAX_INTERVAL = 86_400
MIN_RESULTS  = 1
MAX_RESULTS_CAP = 1000

# ── runtime config (editable from the dashboard, persisted in DB) ────────────

def _env_defaults() -> dict:
    return {
        "keywords":     [k.strip().lower() for k in os.environ.get("KEYWORDS", "pokemon tcg,delta reign").split(",") if k.strip()],
        "interval":     int(os.environ.get("SCAN_INTERVAL", "300")),
        "max_results":  int(os.environ.get("MAX_RESULTS", "200")),
        "webhook":      os.environ.get("DISCORD_WEBHOOK", "").strip(),
        "paused":       False,
    }

config: dict = _env_defaults()


def clamp_config(c: dict) -> dict:
    c["interval"]    = max(MIN_INTERVAL, min(MAX_INTERVAL, int(c.get("interval", 300))))
    c["max_results"] = max(MIN_RESULTS, min(MAX_RESULTS_CAP, int(c.get("max_results", 200))))
    c["keywords"]    = [k.strip().lower() for k in c.get("keywords", []) if k.strip()]
    c["paused"]      = bool(c.get("paused", False))
    c["webhook"]     = (c.get("webhook") or "").strip()
    return c


def save_config() -> None:
    set_meta("config", json.dumps(config))


def load_config() -> None:
    """Overlay any saved config from the DB onto the env-seeded defaults."""
    raw = get_meta("config", "")
    if raw:
        try:
            saved = json.loads(raw)
            config.update({k: saved[k] for k in ("keywords", "interval", "max_results", "webhook", "paused") if k in saved})
        except Exception as e:
            print(f"[config] failed to load saved config: {e}", flush=True)
    clamp_config(config)


# ── db ───────────────────────────────────────────────────────────────────────

def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS products (
                sku           TEXT PRIMARY KEY,
                title         TEXT,
                price         REAL,
                status        TEXT,
                release_date  TEXT,
                limit_per     INTEGER,
                is_hidden     INTEGER,
                is_coming     INTEGER,
                reasons       TEXT,
                matched       TEXT,
                handle        TEXT,
                first_seen    TEXT,
                last_seen     TEXT,
                last_status   TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY, value TEXT
            )
        """)


def set_meta(key: str, value: str) -> None:
    with db_conn() as c:
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def get_meta(key: str, default: str = "") -> str:
    with db_conn() as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


# ── algolia fetch ──────────────────────────────────────────────────────────

async def fetch_algolia(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}"
    params = {"query": query, "hitsPerPage": config["max_results"], "attributesToRetrieve": ATTRS}
    headers = {
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
    }
    r = await client.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json().get("hits", [])


def classify(hit: dict) -> dict:
    """Turn a raw Algolia hit into a normalized product record with hidden flags."""
    avail   = hit.get("availability") or {}
    flags   = [f.get("Name", "") for f in (hit.get("product") or {}).get("productFlags", [])]
    overall = avail.get("overallStatus", "") or ""
    now_ts  = datetime.now(timezone.utc).timestamp()
    keywords = config["keywords"]

    reasons = []
    if hit.get("isPublic") is False:            reasons.append("isPublic=false")
    if avail.get("displayProduct") is False:    reasons.append("displayProduct=false")
    if overall and overall not in ("Available", "InStock"): reasons.append(overall)
    if "Embargo" in flags:                      reasons.append("Embargo")
    if "Hide in ProductApp" in flags:           reasons.append("HideInApp")
    if not hit.get("published_at") and hit.get("release_date"): reasons.append("unpublished")

    rd = hit.get("release_date")
    rd_str = datetime.fromtimestamp(rd, timezone.utc).strftime("%Y-%m-%d") if isinstance(rd, (int, float)) else None
    is_coming = bool(rd and isinstance(rd, (int, float)) and rd > now_ts)
    if is_coming:
        reasons.append(f"release:{rd_str}")

    matched = keywords[:] if not keywords else [
        k for k in keywords
        if k in " ".join([
            str(hit.get("title", "")), str(hit.get("sku", "")),
            overall, str(hit.get("tags", "")), json.dumps(reasons)
        ]).lower()
    ]

    return {
        "sku":          str(hit.get("sku") or hit.get("objectID") or "—"),
        "title":        hit.get("title") or "—",
        "price":        hit.get("price"),
        "status":       overall or ("Hidden" if hit.get("isPublic") is False else "Unknown"),
        "release_date": rd_str or "",
        "limit_per":    (hit.get("product") or {}).get("limitPerOrder"),
        "is_hidden":    1 if reasons else 0,
        "is_coming":    1 if is_coming else 0,
        "reasons":      json.dumps(reasons),
        "matched":      json.dumps(matched),
        "handle":       hit.get("handle") or "",
    }


# ── discord ────────────────────────────────────────────────────────────────

async def discord_send(client: httpx.AsyncClient, title: str, lines: list[str]) -> None:
    if not config["webhook"]:
        return
    content = f"**{title}**\n" + "\n".join(lines)
    try:
        await client.post(config["webhook"], json={"content": content[:1900]}, timeout=10)
    except Exception as e:
        print(f"[discord] alert failed: {e}", flush=True)


# ── scan loop ──────────────────────────────────────────────────────────────

scan_state = {
    "running": False,
    "scans": 0,
    "last_scan": None,
    "last_error": None,
    "total_products": 0,
    "next_scan": None,   # ISO timestamp of the next scheduled scan
}

scan_now_event = asyncio.Event()   # set() to trigger an immediate scan


async def run_one_scan(client: httpx.AsyncClient) -> None:
    keywords = config["keywords"]
    queries = keywords if keywords else [""]
    merged: dict[str, dict] = {}

    for q in queries:
        try:
            hits = await fetch_algolia(client, q)
        except Exception as e:
            scan_state["last_error"] = f"{type(e).__name__}: {e}"
            print(f"[scan] fetch error for '{q}': {e}", flush=True)
            raise
        for h in hits:
            rec = classify(h)
            if keywords and not json.loads(rec["matched"]):
                continue
            merged[rec["sku"]] = rec

    now = datetime.now(timezone.utc).isoformat()
    new_hidden: list[dict] = []
    status_changes: list[tuple[dict, str]] = []

    with db_conn() as c:
        for sku, rec in merged.items():
            existing = c.execute("SELECT sku,last_status FROM products WHERE sku=?", (sku,)).fetchone()
            if existing is None:
                c.execute("""
                    INSERT INTO products
                    (sku,title,price,status,release_date,limit_per,is_hidden,is_coming,
                     reasons,matched,handle,first_seen,last_seen,last_status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    rec["sku"], rec["title"], rec["price"], rec["status"], rec["release_date"],
                    rec["limit_per"], rec["is_hidden"], rec["is_coming"], rec["reasons"],
                    rec["matched"], rec["handle"], now, now, rec["status"],
                ))
                if rec["is_hidden"]:
                    new_hidden.append(rec)
            else:
                prev_status = existing["last_status"]
                if prev_status != rec["status"]:
                    status_changes.append((rec, prev_status))
                c.execute("""
                    UPDATE products SET
                      title=?,price=?,status=?,release_date=?,limit_per=?,is_hidden=?,
                      is_coming=?,reasons=?,matched=?,handle=?,last_seen=?,last_status=?
                    WHERE sku=?
                """, (
                    rec["title"], rec["price"], rec["status"], rec["release_date"], rec["limit_per"],
                    rec["is_hidden"], rec["is_coming"], rec["reasons"], rec["matched"],
                    rec["handle"], now, rec["status"], rec["sku"],
                ))

    if new_hidden:
        await discord_send(client, f"🆕 {len(new_hidden)} new hidden SKU(s)", [
            f"`{r['sku']}` {r['title'][:60]} — **{r['status']}**"
            + (f" · release {r['release_date']}" if r['release_date'] else "")
            + (f" · limit {r['limit_per']}" if r['limit_per'] else "")
            for r in new_hidden[:10]
        ])
    for rec, prev in status_changes:
        await discord_send(client, "🔔 Status change", [
            f"`{rec['sku']}` {rec['title'][:60]}",
            f"{prev} → **{rec['status']}**"
            + (f" · https://www.jbhifi.com.au/products/{rec['handle']}" if rec['handle'] else ""),
        ])

    scan_state["scans"] += 1
    scan_state["last_scan"] = now
    scan_state["last_error"] = None
    scan_state["total_products"] = len(merged)
    set_meta("last_scan", now)
    set_meta("scan_count", str(scan_state["scans"]))
    print(f"[scan] #{scan_state['scans']} — {len(merged)} matched, "
          f"{len(new_hidden)} new hidden, {len(status_changes)} status changes", flush=True)


async def scan_loop() -> None:
    scan_state["running"] = True
    async with httpx.AsyncClient() as client:
        while True:
            if not config["paused"]:
                try:
                    await run_one_scan(client)
                except Exception as e:
                    print(f"[loop] scan failed: {e}", flush=True)

            # Sleep in 1s steps up to config["interval"], so interval changes,
            # pause toggles, and manual "scan now" take effect promptly.
            interval = config["interval"]
            nxt = time.time() + interval
            scan_state["next_scan"] = (
                None if config["paused"]
                else datetime.fromtimestamp(nxt, timezone.utc).isoformat()
            )
            waited = 0
            while waited < interval:
                if scan_now_event.is_set():
                    scan_now_event.clear()
                    break
                await asyncio.sleep(1)
                waited += 1
                # Re-read interval each second so shortening it applies live.
                interval = config["interval"]


# ── app ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_config()
    scan_state["scans"] = int(get_meta("scan_count", "0") or "0")
    if not ALGOLIA_APP_ID or not ALGOLIA_API_KEY:
        print("[boot] WARNING: ALGOLIA_APP_ID / ALGOLIA_API_KEY not set — scan loop idle", flush=True)
        yield
        return
    task = asyncio.create_task(scan_loop())
    print(f"[boot] scan loop started — every {config['interval']}s, keywords={config['keywords']}, paused={config['paused']}", flush=True)
    yield
    task.cancel()


app = FastAPI(title="Drop Scanner", lifespan=lifespan)


def check_token(request: Request) -> None:
    if not DASHBOARD_TOKEN:
        return
    supplied = request.query_params.get("token", "")
    if supplied != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing ?token=")


def public_config() -> dict:
    # Never expose the full webhook URL — just whether one is set + a masked tail.
    wh = config["webhook"]
    return {
        "keywords":     config["keywords"],
        "interval":     config["interval"],
        "max_results":  config["max_results"],
        "paused":       config["paused"],
        "webhook_set":  bool(wh),
        "webhook_hint": ("…" + wh[-6:]) if wh else "",
    }


@app.get("/api/status")
async def api_status(request: Request):
    check_token(request)
    return {
        "running": scan_state["running"],
        "paused": config["paused"],
        "scans": scan_state["scans"],
        "last_scan": scan_state["last_scan"] or get_meta("last_scan"),
        "next_scan": scan_state["next_scan"],
        "last_error": scan_state["last_error"],
        "total_products": scan_state["total_products"],
        "interval": config["interval"],
        "max_results": config["max_results"],
        "keywords": config["keywords"],
        "index": ALGOLIA_INDEX,
    }


@app.get("/api/config")
async def api_get_config(request: Request):
    check_token(request)
    return public_config()


@app.post("/api/config")
async def api_set_config(request: Request):
    check_token(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be JSON")

    if "keywords" in body:
        kw = body["keywords"]
        if isinstance(kw, str):
            kw = [k for k in kw.split(",")]
        config["keywords"] = [str(k).strip().lower() for k in kw if str(k).strip()]
    if "interval" in body:
        try:    config["interval"] = int(body["interval"])
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail="interval must be a number (seconds)")
    if "max_results" in body:
        try:    config["max_results"] = int(body["max_results"])
        except (TypeError, ValueError): raise HTTPException(status_code=400, detail="max_results must be a number")
    if "webhook" in body:
        config["webhook"] = str(body["webhook"] or "").strip()
    if "paused" in body:
        config["paused"] = bool(body["paused"])

    clamp_config(config)
    save_config()
    return {"ok": True, "config": public_config()}


@app.post("/api/control")
async def api_control(request: Request):
    check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = (body.get("action") or request.query_params.get("action") or "").lower()

    if action == "pause":
        config["paused"] = True; save_config()
        scan_state["next_scan"] = None
        return {"ok": True, "paused": True}
    if action == "resume":
        config["paused"] = False; save_config()
        scan_now_event.set()
        return {"ok": True, "paused": False}
    if action in ("scan", "scan_now"):
        if not ALGOLIA_APP_ID or not ALGOLIA_API_KEY:
            raise HTTPException(status_code=400, detail="Algolia creds not configured")
        async with httpx.AsyncClient() as client:
            await run_one_scan(client)
        return {"ok": True, "scans": scan_state["scans"]}
    raise HTTPException(status_code=400, detail="action must be pause | resume | scan")


@app.post("/api/scan-now")
async def api_scan_now(request: Request):
    check_token(request)
    if not ALGOLIA_APP_ID or not ALGOLIA_API_KEY:
        raise HTTPException(status_code=400, detail="Algolia creds not configured")
    async with httpx.AsyncClient() as client:
        await run_one_scan(client)
    return {"ok": True, "scans": scan_state["scans"]}


# Keep the old GET /api/scan-now working (the previous dashboard called it).
@app.get("/api/scan-now")
async def api_scan_now_get(request: Request):
    return await api_scan_now(request)


@app.post("/api/test-discord")
async def api_test_discord(request: Request):
    check_token(request)
    if not config["webhook"]:
        raise HTTPException(status_code=400, detail="no webhook configured")
    async with httpx.AsyncClient() as client:
        await discord_send(client, "✅ Drop Scanner test", ["Webhook is wired up and working."])
    return {"ok": True}


@app.get("/api/results")
async def api_results(request: Request, filter: str = Query("all")):
    check_token(request)
    where = ""
    if filter == "hidden":  where = "WHERE is_hidden=1"
    elif filter == "coming": where = "WHERE is_coming=1"
    with db_conn() as c:
        rows = c.execute(
            f"SELECT * FROM products {where} ORDER BY last_seen DESC, release_date ASC"
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "sku": r["sku"], "title": r["title"], "price": r["price"],
            "status": r["status"], "release_date": r["release_date"],
            "limit_per": r["limit_per"], "is_hidden": bool(r["is_hidden"]),
            "is_coming": bool(r["is_coming"]),
            "reasons": json.loads(r["reasons"] or "[]"),
            "matched": json.loads(r["matched"] or "[]"),
            "handle": r["handle"], "first_seen": r["first_seen"], "last_seen": r["last_seen"],
        })
    return JSONResponse(out)


@app.delete("/api/results")
async def api_clear_results(request: Request):
    check_token(request)
    with db_conn() as c:
        c.execute("DELETE FROM products")
    scan_state["total_products"] = 0
    return {"ok": True, "cleared": True}


@app.delete("/api/results/{sku}")
async def api_delete_result(request: Request, sku: str):
    check_token(request)
    with db_conn() as c:
        cur = c.execute("DELETE FROM products WHERE sku=?", (sku,))
        deleted = cur.rowcount
    return {"ok": True, "deleted": deleted}


@app.get("/api/export.csv")
async def api_export_csv(request: Request, filter: str = Query("all")):
    check_token(request)
    where = ""
    if filter == "hidden":  where = "WHERE is_hidden=1"
    elif filter == "coming": where = "WHERE is_coming=1"
    with db_conn() as c:
        rows = c.execute(f"SELECT * FROM products {where} ORDER BY last_seen DESC").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sku", "title", "price", "status", "release_date", "limit_per",
                "is_hidden", "is_coming", "reasons", "matched", "handle",
                "first_seen", "last_seen"])
    for r in rows:
        w.writerow([
            r["sku"], r["title"], r["price"], r["status"], r["release_date"], r["limit_per"],
            r["is_hidden"], r["is_coming"],
            " | ".join(json.loads(r["reasons"] or "[]")),
            " | ".join(json.loads(r["matched"] or "[]")),
            r["handle"], r["first_seen"], r["last_seen"],
        ])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=drop-scanner.csv"})


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


from dashboard import DASHBOARD_HTML  # noqa: E402


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
