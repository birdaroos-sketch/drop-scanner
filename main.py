"""
Drop Scanner — always-on hidden SKU monitor for Railway.

Architecture:
  - Background asyncio loop polls Algolia every config["interval"] seconds
    (server-side, no CSP/CORS limits — that was the whole problem with the
    browser artifact).
  - Results persisted in SQLite so they survive restarts/redeploys.
  - Runtime config (keywords, neg_keywords, interval, max_results, webhook,
    paused, manual_only) is stored in SQLite too, so edits from the dashboard
    survive restarts. Env vars only seed the defaults on first boot with an
    empty DB.
  - Discord webhook fires when a NEW hidden SKU appears or a tracked SKU changes
    status (e.g. Embargo -> ComingSoon -> Available = the drop signal).
  - Dashboard lives in web/ (static). Served here at / and also deployable to Vercel,
    where web/config.js points it at this API (CORS enabled via CORS_ORIGINS).

Env vars (seed first-boot defaults; after that the dashboard is the source of truth):
  ALGOLIA_APP_ID     required   e.g. VTVKM5URPX
  ALGOLIA_API_KEY    required   e.g. a0c0108d737ad5ab54a0e2da900bf040
  ALGOLIA_INDEX      optional   default shopify_products_families
  KEYWORDS           optional   comma-separated, default "pokemon tcg,delta reign"
  SCAN_INTERVAL      optional   seconds, default 300 (5 min)
  MAX_RESULTS        optional   per keyword query, default 200
  DISCORD_WEBHOOK    optional   full webhook URL for alerts
  DASHBOARD_TOKEN    optional   if set, dashboard + all write endpoints require ?token=...
  CORS_ORIGINS       optional   comma-separated origins allowed to call the API, default *
"""

import asyncio
import csv
import io
import json
import os
import re
import sqlite3
import time
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

# ── static config (never editable at runtime) ───────────────────────────────

ALGOLIA_APP_ID  = os.environ.get("ALGOLIA_APP_ID", "").strip()
ALGOLIA_API_KEY = os.environ.get("ALGOLIA_API_KEY", "").strip()
ALGOLIA_INDEX   = os.environ.get("ALGOLIA_INDEX", "shopify_products_families").strip()
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()
CORS_ORIGINS    = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
WEB_DIR         = Path(__file__).parent / "web"
PORT            = int(os.environ.get("PORT", "8000"))

# Railway gives a persistent volume mount at /data if you attach one; fall back to cwd.
DB_DIR  = Path("/data") if Path("/data").is_dir() else Path(".")
DB_PATH = DB_DIR / "scanner.db"

ATTRS = ("sku,title,price,published_at,release_date,isPublic,availability,product,tags,handle,"
         "image,images,product_image,featured_image,image_url,imageUrl,thumbnail,media")

# ── BIG W search API (first-party, not Algolia) ──────────────────────────────
BIGW_SEARCH_URL = "https://api.bigw.com.au/search/v1/search"
BIGW_BASE       = "https://www.bigw.com.au"
BIGW_UA         = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
BIGW_ATTRS      = [
    "identifiers",
    "information.name", "information.brand", "information.media.image", "information.vendors",
    "attributes.listingStatus", "attributes.maxQuantity", "attributes.condition",
    "prices.NAT", "fulfilment.preorder", "fulfilment.delivery",
    "fulfilment.collection", "fulfilment.logisticType",
]
# listingStatus values that mean "normally purchasable" (anything else => hidden)
BIGW_LISTED_OK  = {"LISTEDONLINEANDSTORE", "LISTEDONLINEONLY", "LISTEDINSTOREONLY"}

def _norm(s: str) -> str:
    """Strip accents and replace & with 'and' so keyword matching is accent/symbol-agnostic."""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.replace("&", " and ").replace("  ", " ")


# Bounds so a bad dashboard input can't wedge the loop.
MIN_INTERVAL = 15
MAX_INTERVAL = 86_400
MIN_RESULTS  = 1
MAX_RESULTS_CAP = 1000

# ── runtime config (editable from the dashboard, persisted in DB) ────────────

def _envbool(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if v in ("1", "true", "yes"):  return True
    if v in ("0", "false", "no"):  return False
    return default


def _env_defaults() -> dict:
    return {
        "keywords":     [k.strip().lower() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()],
        "neg_keywords": [k.strip().lower() for k in os.environ.get("NEG_KEYWORDS", "").split(",") if k.strip()],
        "interval":     int(os.environ.get("SCAN_INTERVAL", "300")),
        "max_results":  int(os.environ.get("MAX_RESULTS", "200")),
        "webhook":      os.environ.get("DISCORD_WEBHOOK", "").strip(),
        "paused":       False,
        "manual_only":  _envbool("MANUAL_ONLY", False),
        "jbhifi_on":    _envbool("JBHIFI_ON", True),
        "bigw_on":      _envbool("BIGW_ON", True),
    }

config: dict = _env_defaults()


def clamp_config(c: dict) -> dict:
    c["interval"]     = max(MIN_INTERVAL, min(MAX_INTERVAL, int(c.get("interval", 300))))
    c["max_results"]  = max(MIN_RESULTS, min(MAX_RESULTS_CAP, int(c.get("max_results", 200))))
    c["keywords"]     = [_norm(k).strip().lower() for k in c.get("keywords", []) if k.strip()]
    c["neg_keywords"] = [_norm(k).strip().lower() for k in c.get("neg_keywords", []) if k.strip()]
    c["paused"]       = bool(c.get("paused", False))
    c["manual_only"]  = bool(c.get("manual_only", False))
    c["jbhifi_on"]    = bool(c.get("jbhifi_on", True))
    c["bigw_on"]      = bool(c.get("bigw_on", True))
    c["webhook"]      = (c.get("webhook") or "").strip()
    return c


def save_config() -> None:
    set_meta("config", json.dumps(config))


def load_config() -> None:
    """Overlay any saved config from the DB onto the env-seeded defaults."""
    raw = get_meta("config", "")
    if raw:
        try:
            saved = json.loads(raw)
            config.update({k: saved[k] for k in (
                "keywords", "neg_keywords", "interval", "max_results",
                "webhook", "paused", "manual_only", "jbhifi_on", "bigw_on",
            ) if k in saved})
        except Exception as e:
            print(f"[config] failed to load saved config: {e}", flush=True)
    clamp_config(config)


# ── db ───────────────────────────────────────────────────────────────────────

def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _create_products(c: sqlite3.Connection) -> None:
    c.execute("""
        CREATE TABLE products (
            id            TEXT PRIMARY KEY,   -- "<source>:<sku>"
            source        TEXT,               -- "jbhifi" | "bigw"
            sku           TEXT,
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
            last_status   TEXT,
            image         TEXT
        )
    """)


def init_db() -> None:
    with db_conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        exists = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
        ).fetchone()
        if not exists:
            _create_products(c)
            c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('bigw_marketplace_purged_v2','1')")
            return
        cols = [r[1] for r in c.execute("PRAGMA table_info(products)")]
        if "id" not in cols:
            # migrate old jbhifi-only table (sku PRIMARY KEY, no source) → new schema
            c.execute("ALTER TABLE products RENAME TO products_old")
            _create_products(c)
            c.execute("""
                INSERT OR IGNORE INTO products
                    (id, source, sku, title, price, status, release_date, limit_per,
                     is_hidden, is_coming, reasons, matched, handle,
                     first_seen, last_seen, last_status)
                SELECT 'jbhifi:'||sku, 'jbhifi', sku, title, price, status, release_date,
                       limit_per, is_hidden, is_coming, reasons, matched, handle,
                       first_seen, last_seen, last_status
                FROM products_old
            """)
            c.execute("DROP TABLE products_old")
            print("[db] migrated products table to multi-source schema", flush=True)
        if "image" not in [r[1] for r in c.execute("PRAGMA table_info(products)")]:
            c.execute("ALTER TABLE products ADD COLUMN image TEXT")
    # One-time cleanup: remove all BIG W rows so marketplace items don't persist.
    # They will be re-discovered on the next scan with the marketplace filter active.
    if get_meta("bigw_marketplace_purged_v2", "") != "1":
        with db_conn() as c2:
            deleted = c2.execute("DELETE FROM products WHERE source='bigw'").rowcount
            if deleted:
                print(f"[db] purged {deleted} BIG W rows (marketplace filter migration)", flush=True)
        set_meta("bigw_marketplace_purged_v2", "1")


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


def pick_image(hit: dict) -> str:
    """Find a product image URL in a raw Algolia hit, whatever field it lives under."""
    def first_url(v):
        if isinstance(v, str) and v.startswith("http"):
            return v
        if isinstance(v, list) and v:
            f = v[0]
            if isinstance(f, str) and f.startswith("http"):
                return f
            if isinstance(f, dict):
                for k in ("url", "src", "link", "image", "originalSrc"):
                    if isinstance(f.get(k), str) and f[k].startswith("http"):
                        return f[k]
        if isinstance(v, dict):
            for k in ("url", "src", "link"):
                if isinstance(v.get(k), str) and v[k].startswith("http"):
                    return v[k]
        return ""

    for key in ("image", "product_image", "featured_image", "image_url", "imageUrl",
                "thumbnail", "images", "media"):
        u = first_url(hit.get(key))
        if u:
            return u
    prod = hit.get("product") or {}
    for key in ("image", "featured_image", "image_url", "imageUrl", "images", "media"):
        u = first_url(prod.get(key))
        if u:
            return u
    return ""


def match_keywords(title: str) -> tuple[list, list, bool]:
    """Apply the shared keyword GROUP filter (OR across chips, AND within a chip)
    to a product title. Returns (matched_groups, neg_groups, kw_ok)."""
    kw_text = _norm(str(title or "")).lower()

    def _group_matches(group: str) -> bool:
        terms = group.split()
        return bool(terms) and all(t in kw_text for t in terms)

    keywords     = config["keywords"]
    neg_keywords = config.get("neg_keywords", [])
    matched      = [g for g in keywords if _group_matches(g)]
    neg_matched  = [g for g in neg_keywords if _group_matches(g)]
    kw_ok        = (not keywords) or bool(matched)   # empty list => match all
    return matched, neg_matched, kw_ok


def classify(hit: dict) -> dict:
    """Turn a raw Algolia (JB Hi-Fi) hit into a normalized product record."""
    avail    = hit.get("availability") or {}
    flags    = [f.get("Name", "") for f in (hit.get("product") or {}).get("productFlags", [])]
    overall  = avail.get("overallStatus", "") or ""
    now_ts   = datetime.now(timezone.utc).timestamp()

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

    matched, neg_matched, kw_ok = match_keywords(hit.get("title", ""))

    return {
        "source":       "jbhifi",
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
        "neg_matched":  json.dumps(neg_matched),
        "kw_ok":        kw_ok,
        "handle":       hit.get("handle") or "",   # bare slug; URL built as jbhifi
        "image":        pick_image(hit),
    }


# ── BIG W fetch + classify ───────────────────────────────────────────────────

async def fetch_bigw(client: httpx.AsyncClient, query: str) -> list[dict]:
    if not query:
        return []
    payload = {
        "format": "1", "clientId": "web", "sessionId": "drop-scanner",
        "page": 0, "perPage": min(config["max_results"], 100),
        "sort": "relevance", "text": query,
        "filter": {"inStock": False},   # include out-of-stock so we can catch restocks
        "include": {
            "facets": False, "additionalFacets": [], "suggestions": False,
            "productAttributes": BIGW_ATTRS,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Origin": BIGW_BASE, "Referer": BIGW_BASE + "/", "User-Agent": BIGW_UA,
    }
    r = await client.post(BIGW_SEARCH_URL, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    return ((data.get("organic") or {}).get("results")) or []


def _bigw_slug(name: str) -> str:
    s = _norm(name).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "product"


def classify_bigw(hit: dict) -> dict | None:
    """Normalize a BIG W search result into the same record shape as classify().
    Returns None for marketplace (third-party seller) listings."""
    info   = hit.get("information") or {}
    attrs  = hit.get("attributes") or {}
    ful    = hit.get("fulfilment") or {}
    ident  = hit.get("identifiers") or {}

    # Marketplace (third-party) sellers have vendor ids like "IMP-<uuid>"; BIG W's own stock uses numeric ids.
    if any(str(v.get("id") or "").upper().startswith("IMP-") for v in (info.get("vendors") or [])):
        return None
    name   = info.get("name") or "—"
    sku    = str(ident.get("articleId") or ident.get("mpn") or "—")

    cents  = (((hit.get("prices") or {}).get("NAT") or {}).get("price") or {}).get("cents")
    price  = round(cents / 100, 2) if isinstance(cents, (int, float)) else None

    in_stock = bool(hit.get("stock"))
    listing  = attrs.get("listingStatus") or ""
    preorder = bool(ful.get("preorder"))

    reasons = []
    if preorder:                         reasons.append("Preorder")
    if not in_stock:                     reasons.append("OutOfStock")
    if listing == "LISTEDONLINEONLY":    reasons.append("OnlineOnly")
    if listing and listing not in BIGW_LISTED_OK: reasons.append(listing)

    if preorder:     status = "PreOrder"
    elif in_stock:   status = "InStock"
    else:            status = "OutOfStock"

    media = (info.get("media") or {}).get("image") or {}
    img   = media.get("medium") or media.get("large") or media.get("small") or ""
    image = (BIGW_BASE + img) if img.startswith("/") else img
    url   = f"{BIGW_BASE}/product/{_bigw_slug(name)}/p/{sku}"

    matched, neg_matched, kw_ok = match_keywords(name)
    # "hidden" for BIG W = not normally purchasable right now (out of stock,
    # preorder, or an unusual listing status the user is waiting on).
    is_hidden = 1 if (preorder or not in_stock or (listing and listing not in BIGW_LISTED_OK)) else 0

    return {
        "source":       "bigw",
        "sku":          sku,
        "title":        name,
        "price":        price,
        "status":       status,
        "release_date": "",
        "limit_per":    attrs.get("maxQuantity"),
        "is_hidden":    is_hidden,
        "is_coming":    1 if preorder else 0,
        "reasons":      json.dumps(reasons),
        "matched":      json.dumps(matched),
        "neg_matched":  json.dumps(neg_matched),
        "kw_ok":        kw_ok,
        "handle":       url,          # full URL; build_embed detects http:// and uses as-is
        "image":        image,
    }


# ── discord ────────────────────────────────────────────────────────────────

COLOR_HIDDEN = 0x8B5CF6   # violet — new hidden SKU
COLOR_DROP   = 0x22C55E   # green — flipped to available (the drop)
COLOR_CHANGE = 0xF5C518   # amber — other status change

_STATUS_LABELS = {
    "InStock": "In Stock", "NoLongerAvailable": "No Longer Available",
    "ComingSoon": "Coming Soon", "PreOrder": "Pre-Order", "Available": "Available",
    "OutOfStock": "Out of Stock",
}


def friendly_status(status: str) -> str:
    if not status:
        return "Unknown"
    if status in _STATUS_LABELS:
        return _STATUS_LABELS[status]
    return re.sub(r"(?<!^)(?=[A-Z])", " ", status)


def keyword_label(rec: dict) -> str:
    try:
        kws = json.loads(rec.get("matched") or "[]")
    except Exception:
        kws = []
    if not kws:
        return ""
    return kws[0].title().replace("Tcg", "TCG") + " "


def aest_now_str() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Australia/Melbourne")).strftime("%H:%M:%S")
    except Exception:
        return datetime.now(timezone.utc).strftime("%H:%M:%S") + " UTC"


def retailer_name(rec: dict) -> str:
    return "BIG W" if rec.get("source") == "bigw" else "JB Hi-Fi"


def product_url(rec: dict):
    handle = rec.get("handle") or ""
    if not handle:
        return None
    if handle.startswith("http"):        # BIG W stores a full URL
        return handle
    return f"https://www.jbhifi.com.au/products/{handle}"


def build_embed(rec: dict, color: int, footer_time: str) -> dict:
    retailer = retailer_name(rec)
    url = product_url(rec)
    embed = {
        "author": {"name": retailer},
        "title": (rec.get("title") or "—")[:250],
        "color": color,
        "fields": [
            {"name": "📦 Stock",      "value": friendly_status(rec.get("status")) or "—", "inline": True},
            {"name": "👁️ Visibility", "value": "🙈 HIDDEN" if rec.get("is_hidden") else "👀 Visible", "inline": True},
            {"name": "💰 Price",      "value": (f"${rec['price']}" if rec.get("price") else "—"), "inline": True},
            {"name": "🏷️ SKU",        "value": f"`{rec.get('sku', '—')}`", "inline": True},
            {"name": "🏬 Retailer",   "value": retailer, "inline": True},
            {"name": "🔢 Limit",      "value": (str(rec["limit_per"]) if rec.get("limit_per") else "—"), "inline": True},
        ],
        "footer": {"text": f"Drop Scanner · {retailer} watch [{footer_time}]"},
    }
    if url:
        embed["url"] = url
        embed["description"] = f"🛒 [Open on {retailer}]({url})"
    if rec.get("image"):
        embed["thumbnail"] = {"url": rec["image"]}
    return embed


async def discord_post(client: httpx.AsyncClient, content: str, embeds: list[dict]) -> None:
    if not config["webhook"]:
        return
    payload = {"username": "Drop Scanner", "content": content[:1900]}
    if embeds:
        payload["embeds"] = embeds[:10]
    try:
        await client.post(config["webhook"], json=payload, timeout=10)
    except Exception as e:
        print(f"[discord] alert failed: {e}", flush=True)


async def discord_send(client: httpx.AsyncClient, title: str, lines: list[str]) -> None:
    """Plain-text alert, kept for the /api/test-discord endpoint."""
    if not config["webhook"]:
        return
    content = f"**{title}**\n" + "\n".join(lines)
    try:
        await client.post(config["webhook"], json={"username": "Drop Scanner", "content": content[:1900]}, timeout=10)
    except Exception as e:
        print(f"[discord] alert failed: {e}", flush=True)


# ── scan loop ──────────────────────────────────────────────────────────────

scan_state = {
    "running": False,
    "scans": 0,
    "last_scan": None,
    "last_error": None,
    "total_products": 0,
    "next_scan": None,
}

scan_now_event = asyncio.Event()


def active_sources() -> list[str]:
    srcs = []
    if config.get("jbhifi_on", True) and ALGOLIA_APP_ID and ALGOLIA_API_KEY:
        srcs.append("jbhifi")
    if config.get("bigw_on", True):
        srcs.append("bigw")
    return srcs


async def run_one_scan(client: httpx.AsyncClient) -> None:
    keywords = config["keywords"]
    # Query each unique term across all groups for broad recall; the group
    # AND/OR filter (via kw_ok) then narrows to real matches.
    terms   = sorted({t for g in keywords for t in g.split()})
    queries = terms if terms else [""]
    sources = active_sources()
    merged: dict[str, dict] = {}   # keyed by id = "<source>:<sku>"
    errors: list[str] = []

    def keep(rec: dict) -> bool:
        return rec["kw_ok"] and not json.loads(rec["neg_matched"])

    for q in queries:
        if "jbhifi" in sources:
            try:
                for h in await fetch_algolia(client, q):
                    rec = classify(h)
                    if keep(rec):
                        merged[f"jbhifi:{rec['sku']}"] = rec
            except Exception as e:
                msg = f"jbhifi '{q}': {type(e).__name__}: {e}"
                errors.append(msg); print(f"[scan] {msg}", flush=True)
        if "bigw" in sources:
            try:
                for h in await fetch_bigw(client, q):
                    rec = classify_bigw(h)
                    if rec is not None and keep(rec):
                        merged[f"bigw:{rec['sku']}"] = rec
            except Exception as e:
                msg = f"bigw '{q}': {type(e).__name__}: {e}"
                errors.append(msg); print(f"[scan] {msg}", flush=True)

    now = datetime.now(timezone.utc).isoformat()
    new_hidden: list[dict] = []
    status_changes: list[tuple[dict, str]] = []

    with db_conn() as c:
        for pid, rec in merged.items():
            existing = c.execute("SELECT last_status FROM products WHERE id=?", (pid,)).fetchone()
            if existing is None:
                c.execute("""
                    INSERT INTO products
                    (id,source,sku,title,price,status,release_date,limit_per,is_hidden,is_coming,
                     reasons,matched,handle,first_seen,last_seen,last_status,image)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    pid, rec["source"], rec["sku"], rec["title"], rec["price"], rec["status"],
                    rec["release_date"], rec["limit_per"], rec["is_hidden"], rec["is_coming"],
                    rec["reasons"], rec["matched"], rec["handle"], now, now, rec["status"], rec.get("image") or "",
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
                      is_coming=?,reasons=?,matched=?,handle=?,last_seen=?,last_status=?,
                      image=COALESCE(NULLIF(?, ''), image)
                    WHERE id=?
                """, (
                    rec["title"], rec["price"], rec["status"], rec["release_date"], rec["limit_per"],
                    rec["is_hidden"], rec["is_coming"], rec["reasons"], rec["matched"],
                    rec["handle"], now, rec["status"], rec.get("image") or "", pid,
                ))

    footer_time = aest_now_str()

    for rec in new_hidden[:10]:
        await discord_post(
            client,
            f"🆕 **New hidden {keyword_label(rec)}SKU**",
            [build_embed(rec, COLOR_HIDDEN, footer_time)],
        )
        await asyncio.sleep(0.35)
    if len(new_hidden) > 10:
        await discord_post(client, f"🆕 **+{len(new_hidden) - 10} more new hidden SKUs** (first 10 shown above)", [])

    for rec, prev in status_changes:
        now_status = (rec["status"] or "").lower()
        is_drop = now_status in ("available", "instock")
        embed = build_embed(rec, COLOR_DROP if is_drop else COLOR_CHANGE, footer_time)
        embed["fields"].insert(0, {
            "name": "🔀 Change",
            "value": f"{friendly_status(prev)} → **{friendly_status(rec['status'])}**",
            "inline": False,
        })
        head = (f"🟢 **DROP — {rec['title'][:80]} is now available!**"
                if is_drop else "🔔 **Status change**")
        await discord_post(client, head, [embed])
        await asyncio.sleep(0.35)

    if not new_hidden and not status_changes:
        kw_str  = ", ".join(config["keywords"]) if config["keywords"] else "all"
        src_str = "+".join(s.upper() for s in sources) if sources else "none"
        await discord_post(
            client,
            f"🔍 **Scan #{scan_state['scans'] + 1} — nothing new** · {len(merged)} products checked "
            f"· retailers: {src_str} · keywords: {kw_str} · [{footer_time} AEST]",
            [],
        )

    scan_state["scans"] += 1
    scan_state["last_scan"] = now
    scan_state["last_error"] = "; ".join(errors) if errors else None
    scan_state["total_products"] = len(merged)
    set_meta("last_scan", now)
    set_meta("scan_count", str(scan_state["scans"]))
    print(f"[scan] #{scan_state['scans']} — {len(merged)} matched across {sources}, "
          f"{len(new_hidden)} new hidden, {len(status_changes)} status changes", flush=True)


async def scan_loop() -> None:
    scan_state["running"] = True
    async with httpx.AsyncClient() as client:
        while True:
            # Auto-scan only when not paused AND not in manual-only mode.
            if not config["paused"] and not config["manual_only"]:
                try:
                    await run_one_scan(client)
                except Exception as e:
                    print(f"[loop] scan failed: {e}", flush=True)

            interval = config["interval"]
            nxt = time.time() + interval
            scan_state["next_scan"] = (
                None if config["paused"] or config["manual_only"]
                else datetime.fromtimestamp(nxt, timezone.utc).isoformat()
            )

            # Sleep in 1s steps; scan_now_event wakes us for a manual trigger.
            waited = 0
            while waited < interval:
                if scan_now_event.is_set():
                    scan_now_event.clear()
                    if not config["paused"]:
                        try:
                            await run_one_scan(client)
                        except Exception as e:
                            print(f"[loop] manual scan failed: {e}", flush=True)
                    break
                await asyncio.sleep(1)
                waited += 1
                interval = config["interval"]


# ── app ────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_config()
    scan_state["scans"] = int(get_meta("scan_count", "0") or "0")
    jbhifi_ok = bool(ALGOLIA_APP_ID and ALGOLIA_API_KEY)
    if not jbhifi_ok and not config.get("bigw_on", True):
        print("[boot] WARNING: no source available (JB Hi-Fi creds missing, BIG W off) — scan loop idle", flush=True)
        yield
        return
    task = asyncio.create_task(scan_loop())
    print(f"[boot] scan loop started — every {config['interval']}s, sources={active_sources()}, "
          f"keywords={config['keywords']}, neg_keywords={config['neg_keywords']}, "
          f"paused={config['paused']}, manual_only={config['manual_only']}", flush=True)
    yield
    task.cancel()


app = FastAPI(title="Drop Scanner", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS,
                   allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])


def check_token(request: Request) -> None:
    if not DASHBOARD_TOKEN:
        return
    supplied = request.query_params.get("token", "")
    if supplied != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="bad or missing ?token=")


def public_config() -> dict:
    wh = config["webhook"]
    return {
        "keywords":     config["keywords"],
        "neg_keywords": config["neg_keywords"],
        "interval":     config["interval"],
        "max_results":  config["max_results"],
        "paused":       config["paused"],
        "manual_only":  config["manual_only"],
        "jbhifi_on":    config["jbhifi_on"],
        "bigw_on":      config["bigw_on"],
        "jbhifi_ready": bool(ALGOLIA_APP_ID and ALGOLIA_API_KEY),
        "webhook_set":  bool(wh),
        "webhook_hint": ("…" + wh[-6:]) if wh else "",
    }


@app.get("/api/status")
async def api_status(request: Request):
    check_token(request)
    return {
        "running":        scan_state["running"],
        "paused":         config["paused"],
        "manual_only":    config["manual_only"],
        "scans":          scan_state["scans"],
        "last_scan":      scan_state["last_scan"] or get_meta("last_scan"),
        "next_scan":      scan_state["next_scan"],
        "last_error":     scan_state["last_error"],
        "total_products": scan_state["total_products"],
        "interval":       config["interval"],
        "max_results":    config["max_results"],
        "keywords":       config["keywords"],
        "neg_keywords":   config["neg_keywords"],
        "jbhifi_on":      config["jbhifi_on"],
        "bigw_on":        config["bigw_on"],
        "sources":        active_sources(),
        "index":          ALGOLIA_INDEX,
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
    if "neg_keywords" in body:
        nk = body["neg_keywords"]
        if isinstance(nk, str):
            nk = [k for k in nk.split(",")]
        config["neg_keywords"] = [str(k).strip().lower() for k in nk if str(k).strip()]
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
    if "manual_only" in body:
        config["manual_only"] = bool(body["manual_only"])
        if config["manual_only"]:
            scan_state["next_scan"] = None
    if "jbhifi_on" in body:
        config["jbhifi_on"] = bool(body["jbhifi_on"])
    if "bigw_on" in body:
        config["bigw_on"] = bool(body["bigw_on"])

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
        if not config["manual_only"]:
            scan_now_event.set()
        return {"ok": True, "paused": False}
    if action in ("scan", "scan_now"):
        if not active_sources():
            raise HTTPException(status_code=400, detail="no retailer enabled (turn on JB Hi-Fi or BIG W)")
        scan_now_event.set()
        return {"ok": True, "scans": scan_state["scans"]}
    raise HTTPException(status_code=400, detail="action must be pause | resume | scan")


@app.post("/api/scan-now")
async def api_scan_now(request: Request):
    check_token(request)
    if not active_sources():
        raise HTTPException(status_code=400, detail="no retailer enabled (turn on JB Hi-Fi or BIG W)")
    scan_now_event.set()
    return {"ok": True, "scans": scan_state["scans"]}


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


def _results_where(filter: str, source: str) -> tuple[str, list]:
    clauses, params = [], []
    if filter == "hidden":   clauses.append("is_hidden=1")
    elif filter == "coming": clauses.append("is_coming=1")
    if source in ("jbhifi", "bigw"):
        clauses.append("source=?"); params.append(source)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@app.get("/api/results")
async def api_results(request: Request, filter: str = Query("all"), source: str = Query("all")):
    check_token(request)
    where, params = _results_where(filter, source)
    with db_conn() as c:
        rows = c.execute(
            f"SELECT * FROM products {where} ORDER BY last_seen DESC, release_date ASC", params
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "source": r["source"],
            "sku": r["sku"], "title": r["title"], "price": r["price"],
            "status": r["status"], "release_date": r["release_date"],
            "limit_per": r["limit_per"], "is_hidden": bool(r["is_hidden"]),
            "is_coming": bool(r["is_coming"]),
            "reasons": json.loads(r["reasons"] or "[]"),
            "matched": json.loads(r["matched"] or "[]"),
            "handle": r["handle"], "url": product_url(dict(r)), "image": r["image"] or "",
            "first_seen": r["first_seen"], "last_seen": r["last_seen"],
        })
    return JSONResponse(out)


@app.delete("/api/results")
async def api_clear_results(request: Request):
    check_token(request)
    with db_conn() as c:
        c.execute("DELETE FROM products")
    scan_state["total_products"] = 0
    return {"ok": True, "cleared": True}


@app.delete("/api/results/{pid:path}")
async def api_delete_result(request: Request, pid: str):
    check_token(request)
    with db_conn() as c:
        # pid is the composite id "<source>:<sku>"; fall back to bare sku for old links
        cur = c.execute("DELETE FROM products WHERE id=? OR sku=?", (pid, pid))
        deleted = cur.rowcount
    return {"ok": True, "deleted": deleted}


@app.get("/api/export.csv")
async def api_export_csv(request: Request, filter: str = Query("all"), source: str = Query("all")):
    check_token(request)
    where, params = _results_where(filter, source)
    with db_conn() as c:
        rows = c.execute(f"SELECT * FROM products {where} ORDER BY last_seen DESC", params).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["source", "sku", "title", "price", "status", "release_date", "limit_per",
                "is_hidden", "is_coming", "reasons", "matched", "url",
                "first_seen", "last_seen"])
    for r in rows:
        w.writerow([
            r["source"], r["sku"], r["title"], r["price"], r["status"], r["release_date"], r["limit_per"],
            r["is_hidden"], r["is_coming"],
            " | ".join(json.loads(r["reasons"] or "[]")),
            " | ".join(json.loads(r["matched"] or "[]")),
            product_url(dict(r)) or "", r["first_seen"], r["last_seen"],
        ])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=drop-scanner.csv"})


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/config.js")
async def dashboard_config():
    # Same-origin when served from here; web/config.js (Railway URL) is only for the Vercel copy.
    return PlainTextResponse('window.API_BASE = "";\n', media_type="application/javascript")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
