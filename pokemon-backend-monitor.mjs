#!/usr/bin/env node
// pokemon-backend-monitor.mjs
// Watches JB Hi-Fi + Kmart AU backends for ANY change on a set of SKUs and posts a Discord
// webhook whenever a watched field moves — price, was-price, availability, buy/pre-order,
// delivery & click-collect status, stock, purchase limit, barcode, release date, title, link,
// reviews, description, images, and (Kmart) storefront-indexed / catalogue title.
// Pure Node 18+, no deps, no browser. Read-only. Polls a KNOWN list of SKUs (not enumeration).
//   JB    -> Algolia product index (public search key from JB's storefront)
//   Kmart -> getProductAvailability GraphQL GET (stock) + search/v1 (price/existence)
//            + Yotpo (catalogue title) + Constructor (storefront indexed)
//
// Run:  DISCORD_WEBHOOK="https://discord.com/api/webhooks/ID/TOKEN" node pokemon-backend-monitor.mjs
// Env:  INTERVAL_MS (default 120000) · KMART_POSTCODE (default 2000) · STATE_FILE · ONCE=1 (single tick)

import { readFileSync, writeFileSync, existsSync } from "fs";
import { fileURLToPath } from "url";

// ---------------- config ----------------
const WEBHOOK = process.env.DISCORD_WEBHOOK || "";
const INTERVAL_MS = Number(process.env.INTERVAL_MS || 120_000);
const KMART_POSTCODE = process.env.KMART_POSTCODE || "2000";
const STATE_FILE = process.env.STATE_FILE || (existsSync("/data") ? "/data/.monitor-state.json" : fileURLToPath(new URL("./.monitor-state.json", import.meta.url)));
const STOCK_PROBE_QTY = 10000; // read the HOME_DELIVERY ceiling, not a qty-capped number

const JB_SKUS = [
  "916270", // Ascended Heroes Focused Fighters Premium Collection
  "879703", "879778", "900731", // Ascended Heroes ETB / Booster Bundle / Tin
  "900807", "900814", "900803", "900809", "900810", "900804", "900805",
  "900806", "900815", "900816", "900817", "900818", "900819", // 30th Celebration line
  "780158", // Surging Sparks Booster
];

const KMART_SKUS = {
  "43860685": "Ascended Heroes Focused Fighters Premium Collection",
  "43814121": "Ascended Heroes Tin",
  "43718733": "Ascended Heroes Booster Bundle",
};

// ---------------- JB (Algolia) ----------------
const ALGOLIA_APP = "VTVKM5URPX";
const ALGOLIA_KEY = "1d989f0839a992bbece9099e1b091f07";
const ALGOLIA_INDEX = "shopify_products_families";
const ALGOLIA_DSN = `https://${ALGOLIA_APP.toLowerCase()}-dsn.algolia.net`;
const g = (o, path) => path.split(".").reduce((x, k) => (x == null ? x : x[k]), o);

async function jbSnapshot() {
  const res = await fetch(`${ALGOLIA_DSN}/1/indexes/*/objects`, {
    method: "POST",
    headers: { "x-algolia-application-id": ALGOLIA_APP, "x-algolia-api-key": ALGOLIA_KEY, "content-type": "application/json" },
    body: JSON.stringify({ requests: JB_SKUS.map((s) => ({ indexName: ALGOLIA_INDEX, objectID: s })) }),
  });
  if (!res.ok) throw new Error(`JB Algolia ${res.status}`);
  const out = {};
  for (const r of (await res.json()).results || []) {
    if (!r || !r.sku) continue;
    const imgs = g(r, "display.images") || [];
    out[`JB:${r.sku}`] = {
      _label: `JB · ${r.title || r.sku}`,
      _url: r.handle ? `https://www.jbhifi.com.au/products/${r.handle}` : `https://www.jbhifi.com.au/search?query=${r.sku}`,
      title: r.title ?? null,
      link: r.handle ? `https://www.jbhifi.com.au/products/${r.handle}` : null,
      price: r.price ?? null,
      wasPrice: r.compare_at_price || null,
      availableNow: g(r, "availability.availableNow"),
      canBuyOnline: !!g(r, "availability.canBuyOnline"),
      canPreOrder: !!g(r, "availability.canPreOrder"),
      overallStatus: g(r, "availability.overallStatus"),
      availabilityStatement: g(r, "availability.availabilityStatement"),
      deliveryStatus: g(r, "availability.deliveryStatus"),
      clickCollectStatus: g(r, "availability.clickNCollectStatus"),
      cashCarryStatus: g(r, "availability.cashNCarryStatus"),
      limitPerOrder: g(r, "product.limitPerOrder") ?? null,
      releaseDate: g(r, "product.releaseDate") ?? null,
      barcode: r.barcode ?? null,
      category: r.category ?? null,
      reviews: g(r, "product.numberOfProductReviews") ?? null,
      rating: g(r, "product.averageProductReviewRating") ?? null,
      description: g(r, "product.description") ?? null,
      images: imgs.length,
      firstImage: imgs[0]?.ImageUrl ?? null,
    };
  }
  return out;
}

// ---------------- Kmart ----------------
const KMART_VISITOR = "00000000-0000-4000-8000-000000000000";
const CIO_KEY = "key_GZTqlLr41FS2p7AY";
const AVAIL_QUERY = `query getProductAvailability($input: ProductAvailabilityQueryInput!) {
  getProductAvailability(input: $input) {
    postcode country
    availability {
      HOME_DELIVERY { keycode poolName stock { available } }
      CLICK_AND_COLLECT { keycode stock { totalAvailable } }
    }
  }
}`;

async function kmartStock(keycode) {
  const variables = { input: { country: "AU", postcode: KMART_POSTCODE, products: [{ keycode, quantity: STOCK_PROBE_QTY, isNationalInventory: true, isClickAndCollectOnly: false, isPreOrderActive: false, isExcludedFromFreeShipping: false, isOnlineExclusiveOnly: false, isFlatRateItem: false }], fulfilmentMethods: ["HOME_DELIVERY", "CLICK_AND_COLLECT"], limit: 3, amendNearestInStockCnc: true } };
  const u = "https://api.kmart.com.au/gateway/graphql?" + new URLSearchParams({ query: AVAIL_QUERY, operationName: "getProductAvailability", variables: JSON.stringify(variables) });
  try {
    const r = await fetch(u, { headers: { "Content-Type": "application/json", Accept: "application/json" } });
    if (!r.ok) return { hd: null, cnc: null };
    const av = (await r.json())?.data?.getProductAvailability?.availability || {};
    return { hd: (av.HOME_DELIVERY || [])[0]?.stock?.available ?? null, cnc: (av.CLICK_AND_COLLECT || [])[0]?.stock?.totalAvailable ?? null };
  } catch { return { hd: null, cnc: null }; }
}

async function kmartPrice(keycode) {
  try {
    const r = await fetch(`https://api.kmart.com.au/search/v1/products/${keycode}?visitor_id=${KMART_VISITOR}&customer_selected_colour=`, { headers: { Accept: "application/json" } });
    if (r.status === 404) return "notfound";
    if (r.status !== 200) return `http${r.status}`;
    const v = ((await r.json())?.response?.variations || [])[0];
    return v?.data?.price != null ? `$${v.data.price}` : "found(unpriced)";
  } catch { return "error"; }
}

async function kmartTitle(keycode) {
  try {
    const r = await fetch(`https://api-cdn.yotpo.com/v1/widget/SzVy7lws56AxMwq47Ufx69XAHnIA3zd0fBJNcbBG/products/${keycode}/reviews.json?per_page=1&page=1`, { headers: { Accept: "application/json" } });
    if (!r.ok) return null;
    return ((await r.json())?.response?.products || [])[0]?.name ?? null;
  } catch { return null; }
}

// storefront go-live: does Constructor's live search surface this exact sku?
async function kmartIndexed(keycode, title) {
  if (!title) return false;
  try {
    const p = new URLSearchParams({ c: "ciojs-client-2.77.1", key: CIO_KEY, i: KMART_VISITOR, s: "1", num_results_per_page: "30", page: "1" });
    const r = await fetch("https://ac.cnstrc.com/search/" + encodeURIComponent(title) + "?" + p, { headers: { Accept: "application/json" } });
    if (!r.ok) return false;
    const hits = ((await r.json())?.response?.results || []);
    return hits.some((x) => String(g(x, "data.variation_id")) === keycode);
  } catch { return false; }
}

async function kmartSnapshot() {
  const out = {};
  for (const [sku, label] of Object.entries(KMART_SKUS)) {
    const [stock, price, title] = await Promise.all([kmartStock(sku), kmartPrice(sku), kmartTitle(sku)]);
    const indexed = await kmartIndexed(sku, title);
    out[`KM:${sku}`] = {
      _label: `Kmart · ${label}`,
      _url: `https://www.kmart.com.au/product/${sku}/`,
      catalogueTitle: title,
      priceService: price,
      homeDeliveryStock: stock.hd,
      clickCollectStock: stock.cnc,
      storefrontIndexed: indexed,
    };
  }
  return out;
}

// ---------------- diff + alert ----------------
const fmt = (v) => (v === undefined ? "—" : v === null ? "null" : typeof v === "string" && v.length > 80 ? v.slice(0, 77) + "…" : String(v));
function diffItem(prev, cur) {
  const out = [];
  for (const k of Object.keys(cur)) {
    if (k.startsWith("_")) continue;
    if (JSON.stringify(prev ? prev[k] : undefined) !== JSON.stringify(cur[k])) out.push(`• ${k}: ${fmt(prev && prev[k])} → ${fmt(cur[k])}`);
  }
  return out;
}
function diffStruct(prev, cur) {
  const out = [];
  for (const k of Object.keys(cur)) {
    if (k.startsWith("_")) continue;
    if (JSON.stringify(prev ? prev[k] : undefined) !== JSON.stringify(cur[k])) out.push({ f: k, from: fmt(prev && prev[k]), to: fmt(cur[k]) });
  }
  return out;
}

async function send(content) {
  if (!WEBHOOK) return;
  await fetch(WEBHOOK, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ content, allowed_mentions: { parse: [] } }) }).catch((e) => console.log("webhook error:", e.message));
}
async function postDiscord(lines) {
  console.log(lines.join("\n"));
  let buf = "";
  for (const ln of lines) { if ((buf + "\n" + ln).length > 1800) { await send(buf); buf = ""; } buf += (buf ? "\n" : "") + ln; }
  if (buf) await send(buf);
}

const loadState = () => { try { return existsSync(STATE_FILE) ? JSON.parse(readFileSync(STATE_FILE, "utf8")) : {}; } catch { return {}; } };
const saveState = (s) => { try { writeFileSync(STATE_FILE, JSON.stringify(s, null, 2)); } catch (e) { console.log("state save error:", e.message); } };

let state = loadState();
const firstRun = Object.keys(state).length === 0;

// ---------------- monitors + API shape ----------------
const MONITORS = [
  { id: "jb-mega",   name: "JB — Mega Ascended Heroes", src: "JB", kw: ["ascended heroes","focused fighters","premium collection","mega evolutions 2.5"], skus: ["916270","879703","879778","900731"], fields: ["price","availability","canBuyOnline","limitPerOrder","title","releaseDate"] },
  { id: "jb-30th",   name: "JB — 30th Celebration",     src: "JB", kw: ["30th celebration","elite trainer box","booster bundle","ultra premium"], skus: ["900807","900814","900803","900809","900810","900804","900805","900806","900815","900816","900817","900818","900819"], fields: ["price","availability","canBuyOnline","overallStatus","releaseDate"] },
  { id: "jb-sparks", name: "JB — Surging Sparks",       src: "JB", kw: ["surging sparks","scarlet & violet","booster"], skus: ["780158"], fields: ["price","availability","canBuyOnline"] },
  { id: "km-ah",     name: "Kmart — Ascended Heroes",   src: "KM", kw: ["ascended heroes","pokemon tcg","booster","tin"], skus: ["43718733","43814121","43860685"], fields: ["priceService","homeDeliveryStock","clickCollectStock","storefrontIndexed","catalogueTitle"] },
];
const monId = (src, sku) => (MONITORS.find((m) => m.src === src && m.skus.includes(sku)) || {}).id || (src === "KM" ? "km-ah" : "jb-mega");
const shorten = (t) => (t || "").replace(/^Pok[eé]mon TCG - (Mega Evolutions 2\.5:? )?/i, "").replace(/\s*\(?card game\)?$/i, "").trim();

function toProduct(key, v) {
  const [src, sku] = key.split(":");
  if (src === "JB") {
    return { key, src: "JB", sku, nm: shorten(v.title || v._label), price: v.price ?? null, was: v.wasPrice ?? null,
      status: v.overallStatus ?? "?", avail: v.availableNow ?? "", buy: !!v.canBuyOnline, pre: !!v.canPreOrder,
      rel: v.releaseDate ? String(v.releaseDate).slice(0, 10) : null, cat: v.category ?? null, img: v.firstImage ?? null, mon: monId("JB", sku) };
  }
  const price = typeof v.priceService === "string" && v.priceService.startsWith("$") ? Number(v.priceService.slice(1)) : null;
  const idx = !!v.storefrontIndexed, hd = v.homeDeliveryStock ?? 0, cnc = v.clickCollectStock ?? 0;
  return { key, src: "KM", sku, nm: (v.catalogueTitle || (v._label || "").replace(/^Kmart · /, "")),
    price, was: null, status: v.priceService === "notfound" ? "NotFound" : "NoLongerAvailable",
    avail: idx ? `Indexed · ${hd} stock` : "Not yet indexed", buy: hd > 0, pre: false, rel: null, cat: "TCG", img: null,
    mon: monId("KM", sku), hd, cnc, idx };
}
const snapshotProducts = () => Object.entries(state).filter(([k]) => k.includes(":")).map(([k, v]) => toProduct(k, v));

// ---------------- tick ----------------
let lastTick = null, lastChanges = [], ticking = false;
async function tick() {
  if (ticking) return { ran: 0, changes: [] };
  ticking = true;
  const stamp = new Date().toISOString().replace("T", " ").slice(0, 19);
  let snap = {};
  try { const [jb, km] = await Promise.all([jbSnapshot(), kmartSnapshot()]); snap = { ...jb, ...km }; }
  catch (e) { console.log(`[${stamp}] poll error: ${e.message}`); ticking = false; return { ran: 0, changes: [], error: e.message }; }

  const alerts = [], changes = [];
  for (const [key, cur] of Object.entries(snap)) {
    const prev = state[key];
    const lines = diffItem(prev, cur);
    if (prev && lines.length) {
      alerts.push(`🔔 **${cur._label}** (${key.split(":")[1]})\n${lines.join("\n")}\n${cur._url}`);
      const p = toProduct(key, cur);
      changes.push({ key, nm: p.nm, sku: p.sku, src: p.src, mon: p.mon, diff: diffStruct(prev, cur), ts: Date.now() });
    }
    state[key] = cur;
  }
  saveState(state);
  lastTick = Date.now();
  if (changes.length) lastChanges = [...changes, ...lastChanges].slice(0, 100);

  if (alerts.length) await postDiscord([`⚡ ${alerts.length} change(s) @ ${stamp}`, ...alerts]);
  else console.log(`[${stamp}] ${Object.keys(snap).length} watched · no changes`);
  ticking = false;
  return { ran: Object.keys(snap).length, changes };
}

// ---------------- HTTP server (dashboard + API) ----------------
import { createServer } from "http";
const DASH = fileURLToPath(new URL("./dashboard.html", import.meta.url));
const PORT = Number(process.env.PORT || 8080);
const CORS = { "access-control-allow-origin": "*", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET,POST,OPTIONS" };
const json = (res, code, obj) => { res.writeHead(code, { "content-type": "application/json", ...CORS }); res.end(JSON.stringify(obj)); };
function apiSnapshot() {
  return {
    products: snapshotProducts(),
    monitors: MONITORS.map((m) => ({ id: m.id, name: m.name, src: m.src, kw: m.kw, fields: m.fields, interval: Math.round(INTERVAL_MS / 1000), auto: true, skuCount: m.skus.length, lastRun: lastTick })),
    changes: lastChanges,
    meta: { interval: Math.round(INTERVAL_MS / 1000), lastTick, nextTick: lastTick ? lastTick + INTERVAL_MS : null, discord: !!WEBHOOK, watching: Object.keys(state).length, postcode: KMART_POSTCODE, now: Date.now() },
  };
}
createServer(async (req, res) => {
  const u = new URL(req.url, "http://x");
  if (req.method === "OPTIONS") { res.writeHead(204, CORS); return res.end(); }
  try {
    if (u.pathname === "/healthz") return json(res, 200, { ok: true, watching: Object.keys(state).length, lastTick });
    if (u.pathname === "/api/snapshot") return json(res, 200, apiSnapshot());
    if (u.pathname === "/api/run" && req.method === "POST") { const r = await tick(); return json(res, 200, { ...apiSnapshot(), ran: r.ran, justChanged: r.changes }); }
    if (u.pathname === "/" || u.pathname === "/index.html" || u.pathname === "/dashboard.html") {
      let html; try { html = readFileSync(DASH, "utf8"); } catch { return json(res, 500, { error: "dashboard missing" }); }
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" }); return res.end(html);
    }
    return json(res, 404, { error: "not found" });
  } catch (e) { return json(res, 500, { error: e.message }); }
}).listen(PORT, "0.0.0.0", () => console.log(`HTTP console on :${PORT} · dashboard + /api/snapshot + POST /api/run`));

// ---------------- boot ----------------
console.log(`Pokemon backend monitor — JB:${JB_SKUS.length} Kmart:${Object.keys(KMART_SKUS).length} · every ${Math.round(INTERVAL_MS / 1000)}s · ${WEBHOOK ? "Discord ON" : "console only"}`);
if (firstRun) {
  console.log("First run: capturing baseline (no change alerts this tick).");
  await tick();
  await send(`✅ Pokemon backend monitor live — watching ${JB_SKUS.length} JB + ${Object.keys(KMART_SKUS).length} Kmart SKUs every ${Math.round(INTERVAL_MS / 1000)}s. Baseline captured; you'll get pinged on ANY field change.`);
} else { await tick(); }
if (process.env.ONCE) process.exit(0);
setInterval(tick, INTERVAL_MS);
