"""
Drop Scanner â always-on hidden SKU monitor for Railway.

Architecture:
  - Background asyncio loop polls Algolia every SCAN_INTERVAL seconds (server-side,
    no CSP/CORS limits â that was the whole problem with the browser artifact).
  - Results persisted in SQLite so they survive restarts/redeploys.
  - Discord webhook fires when a NEW hidden SKU appears or a tracked SKU changes
    status (e.g. Embargo -> ComingSoon -> Available = the drop signal).
  - Dashboard served from the same origin, so its fetch() to /api/* has no CORS.

Config via environment variables (set these in Railway):
  ALGOLIA_APP_ID     required   e.g. VTVKM5URPX
  ALGOLIA_API_KEY    required   e.g. a0c0108d737ad5ab54a0e2da900bf040
  ALGOLIA_INDEX      optional   default shopify_products_families
  KEYWORDS           optional   comma-separated, default "pokemon tcg,delta reign"
  SCAN_INTERVAL      optional   seconds, default 300 (5 min)
  MAX_RESULTS        optional   per keyword query, default 200
  DISCORD_WEBHOOK    optional   full webhook URL for alerts
  DASHBOARD_TOKEN    optional   if set, dashboard + API require ?token=... to view
"""

import asyncio
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

# ââ config âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

ALGOLIA_APP_ID  = os.environ.get("ALGOLIA_APP_ID", "").strip()
ALGOLIA_API_KEY = os.environ.get("ALGOLIA_API_KEY", "").strip()
ALGOLIA_INDEX   = os.environ.get("ALGOLIA_INDEX", "shopify_products_families").strip()
KEYWORDS        = [k.strip().lower() for k in os.environ.get("KEYWORDS", "pokemon tcg,delta reign").split(",") if k.strip()]
SCAN_INTERVAL   = int(os.environ.get("SCAN_INTERVAL", "300"))
MAX_RESULTS     = int(os.environ.get("MAX_RESULTS", "200"))
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()
PORT            = int(os.environ.get("PORT", "8000"))

# Railway gives a persistent volume mount at /data if you attach one; fall back to cwd.
DB_DIR  = Path("/data") if Path("/data").is_dir() else Path(".")
DB_PATH = DB_DIR / "scanner.db"

ATTRS = "sku,title,price,published_at,release_date,isPublic,availability,product,tags,handle"

# ââ db âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

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


# ââ algolia fetch ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

async def fetch_algolia(client: httpx.AsyncClient, query: str) -> list[dict]:
    url = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}"
    params = {"query": query, "hitsPerPage": MAX_RESULTS, "attributesToRetrieve": ATTRS}
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

    matched = KEYWORDS[:] if not KEYWORDS else [
        k for k in KEYWORDS
        if k in " ".join([
            str(hit.get("title", "")), str(hit.get("sku", "")),
            overall, str(hit.get("tags", "")), json.dumps(reasons)
        ]).lower()
    ]

    return {
        "sku":          str(hit.get("sku") or hit.get("objectID") or "â"),
        "title":        hit.get("title") or "â",
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


# ââ discord ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

async def discord_alert(client: httpx.AsyncClient, title: str, lines: list[str]) -> None:
    if not DISCORD_WEBHOOK:
        return
    content = f"**{title}**\n" + "\n".join(lines)
    try:
        await client.post(DISCORD_WEBHOOK, json={"content": content[:1900]}, timeout=10)
    except Exception as e:
        print(f"[discord] alert failed: {e}", flush=True)


# ââ scan loop ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

scan_state = {
    "running": False,
    "scans": 0,
    "last_scan": None,
    "last_error": None,
    "total_products": 0,
}


async def run_one_scan(client: httpx.AsyncClient) -> None:
    queries = KEYWORDS if KEYWORDS else [""]
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
            if KEYWORDS and not json.loads(rec["matched"]):
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
        await discord_alert(client, f"ð {len(new_hidden)} new hidden SKU(s)", [
            f"`{r['sku']}` {r['title'][:60]} â **{r['status']}**"
            + (f" Â· release {r['release_date']}" if r['release_date'] else "")
            + (f" Â· limit {r['limit_per']}" if r['limit_per'] else "")
            for r in new_hidden[:10]
        ])
    for rec, prev in status_changes:
        await discord_alert(client, "ð Status change", [
            f"`{rec['sku']}` {rec['title'][:60]}",
            f"{prev} â **{rec['status']}**"
            + (f" Â· https://www.jbhifi.com.au/products/{rec['handle']}" if rec['handle'] else ""),
        ])

    scan_state["scans"] += 1
    scan_state["last_scan"] = now
    scan_state["last_error"] = None
    scan_state["total_products"] = len(merged)
    set_meta("last_scan", now)
    set_meta("scan_count", str(scan_state["scans"]))
    print(f"[scan] #{scan_state['scans']} â {len(merged)} matched, "
          f"{len(new_hidden)} new hidden, {len(status_changes)} status changes", flush=True)


async def scan_loop() -> None:
    scan_state["running"] = True
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await run_one_scan(client)
            except Exception as e:
                print(f"[loop] scan failed: {e}", flush=True)
            await asyncio.sleep(SCAN_INTERVAL)


# ââ app ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scan_state["scans"] = int(get_meta("scan_count", "0") or "0")
    if not ALGOLIA_APP_ID or not ALGOLIA_API_KEY:
        print("[boot] WARNING: ALGOLIA_APP_ID / ALGOLIA_API_KEY not set â scan loop idle", flush=True)
        yield
        return
    task = asyncio.create_task(scan_loop())
    print(f"[boot] scan loop started â every {SCAN_INTERVAL}s, keywords={KEYWORDS}", flush=True)
    yield
    task.cancel()


app = FastAPI(title="Drop Scanner", lifespan=lifespan)


def check_token(request: Request) -> None:
    if not DASHBOARD_TOKEN:
        return
    supplied = request.query_params.get("token", "")
    if supplied != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing ?token=")


@app.get("/api/status")
async def api_status(request: Request):
    check_token(request)
    return {
        "running": scan_state["running"],
        "scans": scan_state["scans"],
        "last_scan": scan_state["last_scan"] or get_meta("last_scan"),
        "last_error": scan_state["last_error"],
        "total_products": scan_state["total_products"],
        "interval": SCAN_INTERVAL,
        "keywords": KEYWORDS,
        "index": ALGOLIA_INDEX,
    }


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


@app.get("/api/scan-now")
async def api_scan_now(request: Request):
    check_token(request)
    if not ALGOLIA_APP_ID or not ALGOLIA_API_KEY:
        raise HTTPException(status_code=400, detail="Algolia creds not configured")
    async with httpx.AsyncClient() as client:
        await run_one_scan(client)
    return {"ok": True, "scans": scan_state["scans"]}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


from dashboard import DASHBOARD_HTML  # noqa: E402


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
