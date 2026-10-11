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
import { createServer } from "http";

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

// tiny status server so Railway sees a bound port; also a live status page.
const status = { service: "pokemon-backend-monitor", startedAt: new Date().toISOString(), lastTick: null, watched: JB_SKUS.length + Object.keys(KMART_SKUS).length, lastChanges: 0, webhook: WEBHOOK ? "set" : "missing" };
createServer((req, res) => {
  if (req.url === "/state") { res.writeHead(200, { "content-type": "application/json" }); return res.end(JSON.stringify(state, null, 2)); }
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(status, null, 2));
}).listen(process.env.PORT || 3000, () => console.log(`status server on :${process.env.PORT || 3000}`));

async function tick() {
  const stamp = new Date().toISOString().replace("T", " ").slice(0, 19);
  let snap = {};
  try { const [jb, km] = await Promise.all([jbSnapshot(), kmartSnapshot()]); snap = { ...jb, ...km }; }
  catch (e) { console.log(`[${stamp}] poll error: ${e.message}`); return; }

  const alerts = [];
  for (const [key, cur] of Object.entries(snap)) {
    const prev = state[key];
    const changes = diffItem(prev, cur);
    if (prev && changes.length) alerts.push(`🔔 **${cur._label}** (${key.split(":")[1]})\n${changes.join("\n")}\n${cur._url}`);
    state[key] = cur;
  }
  saveState(state);

  status.lastTick = stamp; status.lastChanges = alerts.length;
  if (alerts.length) await postDiscord([`⚡ ${alerts.length} change(s) @ ${stamp}`, ...alerts]);
  else console.log(`[${stamp}] ${Object.keys(snap).length} watched · no changes`);
}

console.log(`Pokemon backend monitor — JB:${JB_SKUS.length} Kmart:${Object.keys(KMART_SKUS).length} · every ${Math.round(INTERVAL_MS / 1000)}s · ${WEBHOOK ? "Discord ON" : "console only"}`);
if (firstRun) {
  console.log("First run: capturing baseline (no change alerts this tick).");
  await tick();
  await send(`✅ Pokemon backend monitor live — watching ${JB_SKUS.length} JB + ${Object.keys(KMART_SKUS).length} Kmart SKUs every ${Math.round(INTERVAL_MS / 1000)}s. Baseline captured; you'll get pinged on ANY field change (price, stock, availability, limit, title, link, barcode, release date, reviews, etc).`);
} else { await tick(); }
if (process.env.ONCE) process.exit(0);
setInterval(tick, INTERVAL_MS);
