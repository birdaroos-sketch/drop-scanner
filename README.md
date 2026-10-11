# pokemon-backend-monitor (was drop-scanner)

24/7 watcher for JB Hi-Fi + Kmart AU product backends. Pings a Discord webhook on ANY field
change (price, stock, availability, buy/pre-order, limit, title, link, barcode, release date,
reviews, storefront-indexed) across a watched set of SKUs. Pure Node 18+, no deps. `npm start`.
State persists to the Railway /data volume automatically.
