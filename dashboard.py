DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Drop Scanner</title>
<style>
  :root{--bg:#0a0a0a;--surface:#111;--surface2:#1a1a1a;--border:#222;--accent:#f5c518;--accent2:#ff4444;--green:#22c55e;--text:#e8e8e8;--muted:#555;--muted2:#888;--radius:6px;--font:'SF Mono','Fira Code',monospace;--sans:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;line-height:1.5;min-height:100vh}
  .header{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 20px;display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:100}
  .logo{font-family:var(--font);font-size:13px;font-weight:700;color:var(--accent);letter-spacing:.05em}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
  .dot.live{background:var(--green);animation:pulse 2s infinite}
  .dot.err{background:var(--accent2)}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,.4)}70%{box-shadow:0 0 0 6px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
  .status{font-family:var(--font);font-size:11px;color:var(--muted2);flex:1}
  .btn{background:var(--accent);color:#000;border:none;border-radius:var(--radius);font-family:var(--font);font-size:11px;font-weight:700;padding:7px 14px;cursor:pointer}
  .btn:hover{background:#e6b800}
  .wrap{padding:20px;display:flex;flex-direction:column;gap:16px;max-width:1400px;margin:0 auto}
  .stats{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}
  @media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}}
  .stat{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:12px 14px}
  .sv{font-family:var(--font);font-size:20px;font-weight:700;line-height:1}
  .sv.accent{color:var(--accent)}.sv.green{color:var(--green)}.sv.red{color:var(--accent2)}
  .sl{font-family:var(--font);font-size:10px;color:var(--muted2);margin-top:4px;letter-spacing:.06em;text-transform:uppercase}
  .bar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
  .title{font-family:var(--font);font-size:11px;color:var(--muted2);letter-spacing:.08em;text-transform:uppercase}
  .filters{display:flex;gap:6px;flex-wrap:wrap}
  .fb{background:var(--surface);border:1px solid var(--border);border-radius:3px;color:var(--muted2);font-family:var(--font);font-size:10px;padding:4px 10px;cursor:pointer}
  .fb.active{background:rgba(245,197,24,.12);border-color:rgba(245,197,24,.4);color:var(--accent)}
  .table-wrap{overflow:auto;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface)}
  table{width:100%;border-collapse:collapse;font-size:12px}
  thead{background:var(--surface2);position:sticky;top:0}
  th{font-family:var(--font);font-size:10px;color:var(--muted2);text-transform:uppercase;letter-spacing:.08em;padding:10px 14px;text-align:left;white-space:nowrap;border-bottom:1px solid var(--border)}
  td{padding:10px 14px;border-bottom:1px solid #161616;vertical-align:middle;max-width:280px}
  tr:hover td{background:rgba(255,255,255,.02)}
  .sku{font-family:var(--font);font-size:11px;color:var(--accent);white-space:nowrap}
  .ttl a{color:var(--text);text-decoration:none}.ttl a:hover{color:var(--accent)}
  .price{font-family:var(--font);color:var(--green);white-space:nowrap}
  .badge{display:inline-flex;padding:2px 7px;border-radius:3px;font-family:var(--font);font-size:10px;white-space:nowrap;font-weight:600}
  .b-hidden{background:rgba(255,68,68,.12);color:var(--accent2);border:1px solid rgba(255,68,68,.2)}
  .b-coming{background:rgba(245,197,24,.12);color:var(--accent);border:1px solid rgba(245,197,24,.2)}
  .b-live{background:rgba(34,197,94,.12);color:var(--green);border:1px solid rgba(34,197,94,.2)}
  .b-def{background:rgba(255,255,255,.05);color:var(--muted2);border:1px solid var(--border)}
  .kw{background:rgba(245,197,24,.08);color:rgba(245,197,24,.7);font-family:var(--font);font-size:10px;padding:1px 5px;border-radius:2px;margin-right:3px}
  .empty{padding:60px;text-align:center;color:var(--muted);font-family:var(--font);font-size:12px}
  .mono{font-family:var(--font);font-size:11px;color:var(--muted2);white-space:nowrap}
</style>
</head>
<body>
<div class="header">
  <div class="logo">◈ DROP SCANNER</div>
  <div class="dot" id="dot"></div>
  <div class="status" id="status">connecting…</div>
  <button class="btn" id="scanNow">SCAN NOW</button>
</div>

<div class="wrap">
  <div class="stats">
    <div class="stat"><div class="sv" id="sScans">—</div><div class="sl">Scans run</div></div>
    <div class="stat"><div class="sv accent" id="sTotal">—</div><div class="sl">Tracked</div></div>
    <div class="stat"><div class="sv red" id="sHidden">—</div><div class="sl">Hidden</div></div>
    <div class="stat"><div class="sv" id="sInterval">—</div><div class="sl">Interval</div></div>
    <div class="stat"><div class="sv" id="sLast">—</div><div class="sl">Last scan (AEST)</div></div>
  </div>

  <div class="bar">
    <div class="title">Results</div>
    <div class="filters">
      <button class="fb active" data-f="all">All</button>
      <button class="fb" data-f="hidden">Hidden</button>
      <button class="fb" data-f="coming">Coming Soon</button>
    </div>
  </div>

  <div class="table-wrap">
    <table>
      <thead><tr>
        <th>SKU</th><th>Title</th><th>Price</th><th>Status</th>
        <th>Release</th><th>Limit</th><th>Keywords</th><th>First seen (AEST)</th>
      </tr></thead>
      <tbody id="body"><tr><td colspan="8"><div class="empty">loading…</div></td></tr></tbody>
    </table>
  </div>
</div>

<script>
const token = new URLSearchParams(location.search).get('token') || '';
const tq = token ? ('?token=' + encodeURIComponent(token)) : '';
let filter = 'all';

function aest(iso){
  if(!iso) return '—';
  try{ return new Date(iso).toLocaleString('en-AU',{timeZone:'Australia/Melbourne',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}); }
  catch(e){ return iso; }
}

function badge(r){
  if((r.reasons||[]).includes('Embargo')) return '<span class="badge b-hidden">EMBARGO</span>';
  const s=(r.status||'').toLowerCase();
  if(r.is_coming||s.includes('comingsoon')) return '<span class="badge b-coming">COMING SOON</span>';
  if(r.is_hidden||s.includes('nolonger')||s==='hidden') return '<span class="badge b-hidden">HIDDEN</span>';
  if(s==='available'||s==='instock') return '<span class="badge b-live">LIVE</span>';
  return '<span class="badge b-def">'+(r.status||'—')+'</span>';
}

async function loadStatus(){
  try{
    const r = await fetch('/api/status'+tq);
    if(!r.ok) throw new Error(r.status);
    const s = await r.json();
    document.getElementById('sScans').textContent = s.scans;
    document.getElementById('sInterval').textContent = Math.round(s.interval/60)+'m';
    document.getElementById('sLast').textContent = aest(s.last_scan);
    if(s.last_error){
      document.getElementById('dot').className='dot err';
      document.getElementById('status').textContent='error: '+s.last_error;
    }else{
      document.getElementById('dot').className='dot live';
      document.getElementById('status').textContent='live · '+s.keywords.join(', ')+' · every '+Math.round(s.interval/60)+'min';
    }
  }catch(e){
    document.getElementById('dot').className='dot err';
    document.getElementById('status').textContent='cannot reach backend'+(token?'':' (need ?token= ?)');
  }
}

async function loadResults(){
  try{
    const r = await fetch('/api/results'+(tq?tq+'&':'?')+'filter='+filter);
    if(!r.ok) throw new Error(r.status);
    const rows = await r.json();
    document.getElementById('sTotal').textContent = rows.length;
    document.getElementById('sHidden').textContent = rows.filter(x=>x.is_hidden).length;

    if(!rows.length){
      document.getElementById('body').innerHTML='<tr><td colspan="8"><div class="empty">no results yet</div></td></tr>';
      return;
    }
    document.getElementById('body').innerHTML = rows.map(r=>{
      const link = r.handle ? '<a href="https://www.jbhifi.com.au/products/'+r.handle+'" target="_blank">'+r.title+'</a>' : r.title;
      const kws = (r.matched||[]).map(k=>'<span class="kw">'+k+'</span>').join('');
      return '<tr>'
        +'<td class="sku">'+r.sku+'</td>'
        +'<td class="ttl">'+link+'</td>'
        +'<td class="price">'+(r.price?'$'+r.price:'—')+'</td>'
        +'<td>'+badge(r)+'</td>'
        +'<td class="mono">'+(r.release_date||'—')+'</td>'
        +'<td class="mono" style="color:'+(r.limit_per?'var(--accent2)':'var(--muted)')+'">'+(r.limit_per||'—')+'</td>'
        +'<td>'+kws+'</td>'
        +'<td class="mono">'+aest(r.first_seen)+'</td>'
        +'</tr>';
    }).join('');
  }catch(e){
    document.getElementById('body').innerHTML='<tr><td colspan="8"><div class="empty">failed to load: '+e.message+'</div></td></tr>';
  }
}

document.querySelectorAll('.fb').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.fb').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); filter=b.dataset.f; loadResults();
}));

document.getElementById('scanNow').addEventListener('click',async()=>{
  document.getElementById('status').textContent='scanning now…';
  try{ await fetch('/api/scan-now'+tq); }catch(e){}
  await loadStatus(); await loadResults();
});

async function tick(){ await loadStatus(); await loadResults(); }
tick();
setInterval(tick, 30000);
</script>
</body>
</html>
"""
