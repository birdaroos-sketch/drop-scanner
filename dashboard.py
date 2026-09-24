DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Drop Scanner</title>
<style>
  :root{--bg:#0a0a0a;--surface:#111;--surface2:#1a1a1a;--border:#222;--accent:#f5c518;--accent2:#ff4444;--green:#22c55e;--blue:#3b82f6;--text:#e8e8e8;--muted:#555;--muted2:#888;--radius:6px;--font:'SF Mono','Fira Code',ui-monospace,monospace;--sans:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;line-height:1.5;min-height:100vh}
  .header{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 20px;display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:100;flex-wrap:wrap}
  .logo{font-family:var(--font);font-size:13px;font-weight:700;color:var(--accent);letter-spacing:.05em}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none}
  .dot.live{background:var(--green);animation:pulse 2s infinite}
  .dot.paused{background:var(--accent)}
  .dot.err{background:var(--accent2)}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(34,197,94,.4)}70%{box-shadow:0 0 0 6px rgba(34,197,94,0)}100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}}
  .status{font-family:var(--font);font-size:11px;color:var(--muted2);flex:1;min-width:140px}
  .actions{display:flex;gap:6px;flex-wrap:wrap}
  .btn{background:var(--accent);color:#000;border:none;border-radius:var(--radius);font-family:var(--font);font-size:11px;font-weight:700;padding:7px 12px;cursor:pointer;white-space:nowrap}
  .btn:hover{filter:brightness(1.08)}
  .btn.ghost{background:var(--surface2);color:var(--text);border:1px solid var(--border)}
  .btn.ghost:hover{border-color:var(--muted2)}
  .btn.danger{background:transparent;color:var(--accent2);border:1px solid rgba(255,68,68,.35)}
  .btn.danger:hover{background:rgba(255,68,68,.1)}
  .btn:disabled{opacity:.4;cursor:not-allowed}
  .wrap{padding:20px;display:flex;flex-direction:column;gap:16px;max-width:1400px;margin:0 auto}
  .stats{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}
  @media(max-width:800px){.stats{grid-template-columns:repeat(3,1fr)}}
  @media(max-width:480px){.stats{grid-template-columns:repeat(2,1fr)}}
  .stat{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:12px 14px}
  .sv{font-family:var(--font);font-size:19px;font-weight:700;line-height:1}
  .sv.accent{color:var(--accent)}.sv.green{color:var(--green)}.sv.red{color:var(--accent2)}.sv.blue{color:var(--blue)}
  .sl{font-family:var(--font);font-size:10px;color:var(--muted2);margin-top:5px;letter-spacing:.06em;text-transform:uppercase}
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
  .panel-head{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;cursor:pointer;user-select:none}
  .panel-head:hover{background:var(--surface2)}
  .panel-title{font-family:var(--font);font-size:11px;color:var(--accent);letter-spacing:.08em;text-transform:uppercase}
  .chev{font-family:var(--font);color:var(--muted2);transition:transform .15s}
  .panel.open .chev{transform:rotate(90deg)}
  .panel-body{display:none;padding:0 16px 16px;border-top:1px solid var(--border)}
  .panel.open .panel-body{display:block}
  .field{margin-top:14px}
  .field label{display:block;font-family:var(--font);font-size:10px;color:var(--muted2);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
  .field .hint{color:var(--muted);text-transform:none;letter-spacing:0}
  input[type=text],input[type=number]{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-family:var(--font);font-size:12px;padding:8px 10px}
  input:focus{outline:none;border-color:var(--accent)}
  .kwbox{display:flex;flex-wrap:wrap;gap:6px;align-items:center;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:8px}
  .kwbox input{border:none;background:transparent;flex:1;min-width:120px;padding:2px}
  .chip{display:inline-flex;align-items:center;gap:6px;background:rgba(245,197,24,.1);color:var(--accent);border:1px solid rgba(245,197,24,.3);border-radius:3px;font-family:var(--font);font-size:11px;padding:3px 8px}
  .chip b{cursor:pointer;font-weight:700;opacity:.7}
  .chip b:hover{opacity:1}
  .row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media(max-width:560px){.row2{grid-template-columns:1fr}}
  .save-row{display:flex;gap:8px;align-items:center;margin-top:16px;flex-wrap:wrap}
  .saved{font-family:var(--font);font-size:11px;color:var(--green);opacity:0;transition:opacity .2s}
  .saved.show{opacity:1}
  .bar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
  .title{font-family:var(--font);font-size:11px;color:var(--muted2);letter-spacing:.08em;text-transform:uppercase}
  .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .filters{display:flex;gap:6px;flex-wrap:wrap}
  .fb{background:var(--surface);border:1px solid var(--border);border-radius:3px;color:var(--muted2);font-family:var(--font);font-size:10px;padding:5px 10px;cursor:pointer}
  .fb.active{background:rgba(245,197,24,.12);border-color:rgba(245,197,24,.4);color:var(--accent)}
  .search{background:var(--surface);border:1px solid var(--border);border-radius:3px;color:var(--text);font-family:var(--font);font-size:11px;padding:5px 10px;min-width:160px}
  .search:focus{outline:none;border-color:var(--accent)}
  .table-wrap{overflow:auto;border:1px solid var(--border);border-radius:var(--radius);background:var(--surface)}
  table{width:100%;border-collapse:collapse;font-size:12px}
  thead{background:var(--surface2);position:sticky;top:0}
  th{font-family:var(--font);font-size:10px;color:var(--muted2);text-transform:uppercase;letter-spacing:.08em;padding:10px 14px;text-align:left;white-space:nowrap;border-bottom:1px solid var(--border);cursor:pointer;user-select:none}
  th:hover{color:var(--text)}
  th .arr{color:var(--accent)}
  td{padding:10px 14px;border-bottom:1px solid #161616;vertical-align:middle;max-width:300px}
  tr:hover td{background:rgba(255,255,255,.02)}
  .sku{font-family:var(--font);font-size:11px;color:var(--accent);white-space:nowrap}
  .ttl a{color:var(--text);text-decoration:none}.ttl a:hover{color:var(--accent)}
  .reasons{margin-top:4px;display:flex;flex-wrap:wrap;gap:3px}
  .rtag{font-family:var(--font);font-size:9px;color:var(--muted2);background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:2px;padding:0 4px}
  .price{font-family:var(--font);color:var(--green);white-space:nowrap}
  .badge{display:inline-flex;padding:2px 7px;border-radius:3px;font-family:var(--font);font-size:10px;white-space:nowrap;font-weight:600}
  .b-hidden{background:rgba(255,68,68,.12);color:var(--accent2);border:1px solid rgba(255,68,68,.2)}
  .b-coming{background:rgba(245,197,24,.12);color:var(--accent);border:1px solid rgba(245,197,24,.2)}
  .b-live{background:rgba(34,197,94,.12);color:var(--green);border:1px solid rgba(34,197,94,.2)}
  .b-def{background:rgba(255,255,255,.05);color:var(--muted2);border:1px solid var(--border)}
  .kw{background:rgba(245,197,24,.08);color:rgba(245,197,24,.7);font-family:var(--font);font-size:10px;padding:1px 5px;border-radius:2px;margin-right:3px}
  .del{color:var(--muted);cursor:pointer;font-family:var(--font);font-weight:700;padding:2px 6px;border-radius:3px}
  .del:hover{color:var(--accent2);background:rgba(255,68,68,.1)}
  .empty{padding:60px;text-align:center;color:var(--muted);font-family:var(--font);font-size:12px}
  .mono{font-family:var(--font);font-size:11px;color:var(--muted2);white-space:nowrap}
  .toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(20px);background:var(--surface2);border:1px solid var(--border);border-radius:var(--radius);padding:10px 18px;font-family:var(--font);font-size:12px;opacity:0;pointer-events:none;transition:all .2s;z-index:200}
  .toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
  .toast.ok{border-color:rgba(34,197,94,.4);color:var(--green)}
  .toast.err{border-color:rgba(255,68,68,.4);color:var(--accent2)}
</style>
</head>
<body>
<div class="header">
  <div class="logo">◈ DROP SCANNER</div>
  <div class="dot" id="dot"></div>
  <div class="status" id="status">connecting…</div>
  <div class="actions">
    <button class="btn ghost" id="pauseBtn">PAUSE</button>
    <button class="btn" id="scanNow">SCAN NOW</button>
  </div>
</div>

<div class="wrap">
  <div class="stats">
    <div class="stat"><div class="sv" id="sScans">—</div><div class="sl">Scans run</div></div>
    <div class="stat"><div class="sv accent" id="sTotal">—</div><div class="sl">Tracked</div></div>
    <div class="stat"><div class="sv red" id="sHidden">—</div><div class="sl">Hidden</div></div>
    <div class="stat"><div class="sv blue" id="sInterval">—</div><div class="sl">Interval</div></div>
    <div class="stat"><div class="sv green" id="sNext">—</div><div class="sl">Next scan</div></div>
    <div class="stat"><div class="sv" id="sLast">—</div><div class="sl">Last scan (AEST)</div></div>
  </div>

  <div class="panel" id="settingsPanel">
    <div class="panel-head" id="settingsHead">
      <div class="panel-title">⚙ Settings</div>
      <div class="chev">▶</div>
    </div>
    <div class="panel-body">
      <div class="field">
        <label>Keywords <span class="hint">— type and press Enter to add, click × to remove</span></label>
        <div class="kwbox" id="kwbox">
          <input type="text" id="kwInput" placeholder="add keyword…" autocomplete="off">
        </div>
      </div>
      <div class="row2">
        <div class="field">
          <label>Scan interval <span class="hint">— seconds (min 15)</span></label>
          <input type="number" id="intervalInput" min="15" step="5">
        </div>
        <div class="field">
          <label>Max results <span class="hint">— per keyword query</span></label>
          <input type="number" id="maxInput" min="1" max="1000" step="1">
        </div>
      </div>
      <div class="field">
        <label>Discord webhook <span class="hint" id="whHint"></span></label>
        <input type="text" id="webhookInput" placeholder="https://discord.com/api/webhooks/…" autocomplete="off">
      </div>
      <div class="save-row">
        <button class="btn" id="saveBtn">SAVE SETTINGS</button>
        <button class="btn ghost" id="testBtn">TEST DISCORD</button>
        <span class="saved" id="savedMsg">✓ saved</span>
      </div>
    </div>
  </div>

  <div class="bar">
    <div class="title">Results</div>
    <div class="toolbar">
      <input class="search" id="searchBox" placeholder="search sku / title…" autocomplete="off">
      <div class="filters">
        <button class="fb active" data-f="all">All</button>
        <button class="fb" data-f="hidden">Hidden</button>
        <button class="fb" data-f="coming">Coming Soon</button>
      </div>
      <button class="btn ghost" id="exportBtn">EXPORT CSV</button>
      <button class="btn danger" id="clearBtn">CLEAR ALL</button>
    </div>
  </div>

  <div class="table-wrap">
    <table>
      <thead><tr id="headRow">
        <th data-k="sku">SKU</th><th data-k="title">Title</th><th data-k="price">Price</th>
        <th data-k="status">Status</th><th data-k="release_date">Release</th>
        <th data-k="limit_per">Limit</th><th data-k="matched">Keywords</th>
        <th data-k="first_seen">First seen (AEST)</th><th></th>
      </tr></thead>
      <tbody id="body"><tr><td colspan="9"><div class="empty">loading…</div></td></tr></tbody>
    </table>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const token = new URLSearchParams(location.search).get('token') || '';
const tq = token ? ('?token=' + encodeURIComponent(token)) : '';
function url(path, extra){ let u = path + tq; if(extra) u += (tq?'&':'?') + extra; return u; }

let filter = 'all';
let search = '';
let sortKey = 'last_seen';
let sortDir = -1;
let rowsCache = [];
let keywords = [];
let nextScanTs = null;

function toast(msg, kind){
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = 'toast show ' + (kind||'');
  setTimeout(()=>{ t.className = 'toast ' + (kind||''); }, 2200);
}

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

// ── keyword chips ──────────────────────────────────────────────────────────
function renderChips(){
  const box = document.getElementById('kwbox');
  box.querySelectorAll('.chip').forEach(c=>c.remove());
  const input = document.getElementById('kwInput');
  keywords.forEach((k,i)=>{
    const el = document.createElement('span');
    el.className = 'chip';
    el.innerHTML = k.replace(/</g,'&lt;') + ' <b data-i="'+i+'">×</b>';
    box.insertBefore(el, input);
  });
  box.querySelectorAll('.chip b').forEach(b=>b.addEventListener('click',()=>{
    keywords.splice(+b.dataset.i,1); renderChips();
  }));
}
document.getElementById('kwInput').addEventListener('keydown',e=>{
  if(e.key==='Enter'||e.key===','){
    e.preventDefault();
    const v = e.target.value.trim().toLowerCase();
    if(v && !keywords.includes(v)){ keywords.push(v); }
    e.target.value=''; renderChips();
  }else if(e.key==='Backspace' && !e.target.value && keywords.length){
    keywords.pop(); renderChips();
  }
});

// ── settings panel ─────────────────────────────────────────────────────────
document.getElementById('settingsHead').addEventListener('click',()=>{
  document.getElementById('settingsPanel').classList.toggle('open');
});

async function loadConfig(){
  try{
    const c = await fetch(url('/api/config')).then(r=>r.json());
    keywords = (c.keywords||[]).slice();
    renderChips();
    document.getElementById('intervalInput').value = c.interval;
    document.getElementById('maxInput').value = c.max_results;
    document.getElementById('whHint').textContent = c.webhook_set ? ('— set ('+c.webhook_hint+')') : '— none set';
  }catch(e){}
}

document.getElementById('saveBtn').addEventListener('click',async()=>{
  // stash any half-typed keyword
  const pend = document.getElementById('kwInput').value.trim().toLowerCase();
  if(pend && !keywords.includes(pend)){ keywords.push(pend); document.getElementById('kwInput').value=''; renderChips(); }
  const body = {
    keywords,
    interval: +document.getElementById('intervalInput').value,
    max_results: +document.getElementById('maxInput').value,
  };
  const wh = document.getElementById('webhookInput').value.trim();
  if(wh) body.webhook = wh;
  try{
    const r = await fetch(url('/api/config'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok) throw new Error((await r.json()).detail || r.status);
    document.getElementById('webhookInput').value='';
    const m=document.getElementById('savedMsg'); m.classList.add('show'); setTimeout(()=>m.classList.remove('show'),1800);
    toast('settings saved','ok');
    await loadConfig(); await tick();
  }catch(e){ toast('save failed: '+e.message,'err'); }
});

document.getElementById('testBtn').addEventListener('click',async()=>{
  // If they typed a new webhook but haven't saved, save it first.
  const wh = document.getElementById('webhookInput').value.trim();
  if(wh){
    await fetch(url('/api/config'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({webhook:wh})});
    document.getElementById('webhookInput').value=''; await loadConfig();
  }
  try{
    const r = await fetch(url('/api/test-discord'),{method:'POST'});
    if(!r.ok) throw new Error((await r.json()).detail || r.status);
    toast('test sent to Discord','ok');
  }catch(e){ toast('test failed: '+e.message,'err'); }
});

// ── controls ───────────────────────────────────────────────────────────────
document.getElementById('pauseBtn').addEventListener('click',async()=>{
  const btn=document.getElementById('pauseBtn');
  const action = btn.dataset.paused==='1' ? 'resume' : 'pause';
  try{
    await fetch(url('/api/control'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
    toast(action==='pause'?'scanning paused':'scanning resumed','ok');
    await tick();
  }catch(e){ toast('failed','err'); }
});

document.getElementById('scanNow').addEventListener('click',async()=>{
  const btn=document.getElementById('scanNow'); btn.disabled=true; btn.textContent='SCANNING…';
  try{ await fetch(url('/api/control'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'scan'})}); toast('scan complete','ok'); }
  catch(e){ toast('scan failed','err'); }
  btn.disabled=false; btn.textContent='SCAN NOW';
  await tick();
});

let clearArmed=false;
document.getElementById('clearBtn').addEventListener('click',async()=>{
  const btn=document.getElementById('clearBtn');
  if(!clearArmed){ clearArmed=true; btn.textContent='CONFIRM CLEAR'; setTimeout(()=>{clearArmed=false;btn.textContent='CLEAR ALL';},3000); return; }
  clearArmed=false; btn.textContent='CLEAR ALL';
  try{ await fetch(url('/api/results'),{method:'DELETE'}); toast('all tracked SKUs cleared','ok'); await tick(); }
  catch(e){ toast('clear failed','err'); }
});

document.getElementById('exportBtn').addEventListener('click',()=>{
  window.open(url('/api/export.csv','filter='+filter),'_blank');
});

async function deleteRow(sku){
  try{ await fetch(url('/api/results/'+encodeURIComponent(sku)),{method:'DELETE'}); toast('removed '+sku,'ok'); await loadResults(); }
  catch(e){ toast('delete failed','err'); }
}

// ── search + sort ──────────────────────────────────────────────────────────
document.getElementById('searchBox').addEventListener('input',e=>{ search=e.target.value.toLowerCase(); renderRows(); });
document.getElementById('headRow').querySelectorAll('th[data-k]').forEach(th=>th.addEventListener('click',()=>{
  const k=th.dataset.k;
  if(sortKey===k) sortDir*=-1; else { sortKey=k; sortDir=1; }
  renderRows();
}));

document.querySelectorAll('.fb').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('.fb').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); filter=b.dataset.f; loadResults();
}));

// ── data ───────────────────────────────────────────────────────────────────
async function loadStatus(){
  try{
    const s = await fetch(url('/api/status')).then(r=>{ if(!r.ok) throw new Error(r.status); return r.json(); });
    document.getElementById('sScans').textContent = s.scans;
    document.getElementById('sInterval').textContent = s.interval>=60 ? Math.round(s.interval/60)+'m' : s.interval+'s';
    document.getElementById('sLast').textContent = aest(s.last_scan);
    nextScanTs = s.next_scan ? new Date(s.next_scan).getTime() : null;
    const pauseBtn=document.getElementById('pauseBtn');
    if(s.paused){
      pauseBtn.textContent='RESUME'; pauseBtn.dataset.paused='1';
      document.getElementById('dot').className='dot paused';
      document.getElementById('status').textContent='paused · '+ (s.keywords||[]).join(', ');
      document.getElementById('sNext').textContent='paused';
    }else if(s.last_error){
      document.getElementById('dot').className='dot err';
      document.getElementById('status').textContent='error: '+s.last_error;
      pauseBtn.textContent='PAUSE'; pauseBtn.dataset.paused='0';
    }else{
      pauseBtn.textContent='PAUSE'; pauseBtn.dataset.paused='0';
      document.getElementById('dot').className='dot live';
      document.getElementById('status').textContent='live · '+(s.keywords||[]).join(', ')+' · every '+(s.interval>=60?Math.round(s.interval/60)+'min':s.interval+'s');
    }
  }catch(e){
    document.getElementById('dot').className='dot err';
    document.getElementById('status').textContent='cannot reach backend'+(token?'':' (need ?token= ?)');
  }
}

function tickCountdown(){
  const el=document.getElementById('sNext');
  const pauseBtn=document.getElementById('pauseBtn');
  if(pauseBtn.dataset.paused==='1'){ el.textContent='paused'; return; }
  if(!nextScanTs){ el.textContent='—'; return; }
  const secs=Math.max(0,Math.round((nextScanTs-Date.now())/1000));
  const m=Math.floor(secs/60), s=secs%60;
  el.textContent = m>0 ? (m+'m '+String(s).padStart(2,'0')+'s') : (s+'s');
}

async function loadResults(){
  try{
    const rows = await fetch(url('/api/results','filter='+filter)).then(r=>{ if(!r.ok) throw new Error(r.status); return r.json(); });
    rowsCache = rows;
    document.getElementById('sTotal').textContent = rows.length;
    document.getElementById('sHidden').textContent = rows.filter(x=>x.is_hidden).length;
    renderRows();
  }catch(e){
    document.getElementById('body').innerHTML='<tr><td colspan="9"><div class="empty">failed to load: '+e.message+'</div></td></tr>';
  }
}

function renderRows(){
  let rows = rowsCache.slice();
  if(search){
    rows = rows.filter(r => (r.sku||'').toLowerCase().includes(search) || (r.title||'').toLowerCase().includes(search));
  }
  rows.sort((a,b)=>{
    let x=a[sortKey], y=b[sortKey];
    if(sortKey==='matched') { x=(x||[]).join(); y=(y||[]).join(); }
    if(x==null) x=''; if(y==null) y='';
    if(typeof x==='number' && typeof y==='number') return (x-y)*sortDir;
    return String(x).localeCompare(String(y))*sortDir;
  });
  // sort arrows
  document.querySelectorAll('#headRow th[data-k]').forEach(th=>{
    const base=th.textContent.replace(/[▲▼]\s*$/,'').trim();
    th.innerHTML = base + (th.dataset.k===sortKey ? ' <span class="arr">'+(sortDir>0?'▲':'▼')+'</span>' : '');
  });
  if(!rows.length){
    document.getElementById('body').innerHTML='<tr><td colspan="9"><div class="empty">'+(search?'no matches':'no results yet')+'</div></td></tr>';
    return;
  }
  document.getElementById('body').innerHTML = rows.map(r=>{
    const link = r.handle ? '<a href="https://www.jbhifi.com.au/products/'+r.handle+'" target="_blank" rel="noopener">'+esc(r.title)+'</a>' : esc(r.title);
    const kws = (r.matched||[]).map(k=>'<span class="kw">'+esc(k)+'</span>').join('');
    const reasons = (r.reasons||[]).length ? '<div class="reasons">'+r.reasons.map(x=>'<span class="rtag">'+esc(x)+'</span>').join('')+'</div>' : '';
    return '<tr>'
      +'<td class="sku">'+esc(r.sku)+'</td>'
      +'<td class="ttl">'+link+reasons+'</td>'
      +'<td class="price">'+(r.price?'$'+r.price:'—')+'</td>'
      +'<td>'+badge(r)+'</td>'
      +'<td class="mono">'+(r.release_date||'—')+'</td>'
      +'<td class="mono" style="color:'+(r.limit_per?'var(--accent2)':'var(--muted)')+'">'+(r.limit_per||'—')+'</td>'
      +'<td>'+kws+'</td>'
      +'<td class="mono">'+aest(r.first_seen)+'</td>'
      +'<td><span class="del" data-sku="'+esc(r.sku)+'">×</span></td>'
      +'</tr>';
  }).join('');
  document.querySelectorAll('.del').forEach(d=>d.addEventListener('click',()=>deleteRow(d.dataset.sku)));
}

function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function tick(){ await loadStatus(); await loadResults(); }

loadConfig();
tick();
setInterval(tick, 30000);
setInterval(tickCountdown, 1000);
</script>
</body>
</html>
"""
