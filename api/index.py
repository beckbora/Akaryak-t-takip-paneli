import json
import time
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / 'data.json'
DASHBOARD = ROOT / 'live.html'
PRICE_DASHBOARD = ROOT / 'prices.html'
DEPOSIT_DASHBOARD = ROOT / 'deposit.html'
PRICE_DATA = ROOT / 'prices.json'
EXPECTATION_DATA = ROOT / 'price_expectation.json'
PWA_MANIFEST = ROOT / 'manifest.webmanifest'
PWA_SW = ROOT / 'sw.js'
PWA_IOS = ROOT / 'ios-pwa.js'
SNAPSHOT_BASE = 'https://beckbora.github.io/Akaryak-t-takip-paneli'
SNAPSHOT_TTL_SECONDS = 45
_SNAPSHOT_CACHE = {}

app = FastAPI(title='Petrol Piyasası Takip')


def _snapshot_json(filename, local_path):
    now = time.monotonic()
    cached = _SNAPSHOT_CACHE.get(filename)
    if cached and now - cached['at'] < SNAPSHOT_TTL_SECONDS:
        return cached['data'], cached['source']
    try:
        bucket = int(time.time() // 60)
        r = requests.get(
            f'{SNAPSHOT_BASE}/{filename}?v={bucket}',
            headers={'User-Agent': 'PetrolPiyasasiTakip/1.0'},
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError('snapshot is not an object')
        source = 'github-pages'
    except Exception:
        try:
            data = json.loads(local_path.read_text(encoding='utf-8'))
            source = 'vercel-bundled-fallback'
        except Exception:
            data = {}
            source = 'unavailable'
    _SNAPSHOT_CACHE[filename] = {'at': now, 'data': data, 'source': source}
    return data, source


def _snapshot_response(filename, local_path):
    data, source = _snapshot_json(filename, local_path)
    return JSONResponse(
        content=data,
        headers={
            'Cache-Control': 'no-store, max-age=0',
            'X-Snapshot-Source': source,
        },
    )


def sector_html():
    html = DASHBOARD.read_text(encoding='utf-8')
    html = html.replace(
        'EPDK Petrol ve LPG piyasası duyuruları, mevzuat değişiklikleri, Kurul kararları ve denetim kararları dahil; Resmî Gazete, GİB / YN ÖKC, Darphane / UTTS, PÜİS, TABGİS, PETDER, LPG Derneği ve TOBB kaynaklarını canlı tarar.',
        'EPDK Petrol ve LPG piyasası duyuruları, mevzuat değişiklikleri, Kurul kararları ve denetim kararları dahil; Resmî Gazete, GİB / YN ÖKC, Darphane / UTTS, PÜİS, TABGİS, PETDER ve LPG Derneği kaynaklarını canlı tarar.',
    )
    nav = '''<nav style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px"><a href="/" style="color:#cafff8;text-decoration:none;border:1px solid #20796f;background:#103a37;border-radius:999px;padding:9px 13px;font-size:13px;font-weight:850">Mevzuat &amp; Sektör Radar</a><a href="/fiyatlar" style="color:#cbd7e6;text-decoration:none;border:1px solid #263b5a;background:#0c1929;border-radius:999px;padding:9px 13px;font-size:13px;font-weight:850">⛽ Fiyat Radar</a><a href="/depozito" style="color:#cbd7e6;text-decoration:none;border:1px solid #263b5a;background:#0c1929;border-radius:999px;padding:9px 13px;font-size:13px;font-weight:850">♻️ DOA / DBYS</a></nav>'''
    marker = '<header class="top">'
    if marker in html:
        html = html.replace(marker, nav + marker, 1)
    return html


def prices_html():
    html = PRICE_DASHBOARD.read_text(encoding='utf-8')
    old_nav = '<nav class="nav"><a href="/">Mevzuat & Sektör Radar</a><a class="active" href="/fiyatlar">⛽ Fiyat Radar</a></nav>'
    new_nav = '<nav class="nav"><a href="/">Mevzuat &amp; Sektör Radar</a><a class="active" href="/fiyatlar">⛽ Fiyat Radar</a><a href="/depozito">♻️ DOA / DBYS</a></nav>'
    html = html.replace(old_nav, new_nav, 1)

    pwa_head = '''<link rel="manifest" href="/manifest.webmanifest"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Fiyat Radar">'''
    html = html.replace('</head>', pwa_head + '</head>', 1)

    html = html.replace("line(g,'#5eead4')+line(d,'#7dd3fc')", "line(g,'#f59e0b')+line(d,'#38bdf8')")
    html = html.replace('style="background:#5eead4"></i>Benzin 95', 'style="background:#f59e0b"></i>Benzin 95')
    html = html.replace('style="background:#7dd3fc"></i>Motorin', 'style="background:#38bdf8"></i>Motorin')

    html = html.replace(
        '<button id="scanBtn" class="btn">⚡ Fiyatları Şimdi Tara</button></header>',
        '<div class="notifyActions"><button id="scanBtn" class="btn">⚡ Fiyatları Şimdi Tara</button><button id="notifyBtn" class="btn notifyBtn" type="button">🔕 Bildirimler Kapalı</button></div></header>',
        1,
    )

    expectation_css = '''
.expectationBox{margin:13px 0;border:1px solid var(--line);border-radius:14px;background:#0d1a2b;overflow:hidden}
.expectationTitle{padding:8px 13px;border-bottom:1px solid var(--line);font-size:11px;color:var(--muted);font-weight:900;letter-spacing:.05em;text-transform:uppercase}
.expectationRow{display:flex;align-items:center;gap:9px;padding:10px 13px;border-bottom:1px solid rgba(38,59,90,.55);font-size:13px;min-height:42px}
.expectationRow:last-child{border-bottom:0}.expectationRow a{color:inherit;text-decoration:none;font-weight:900}.expectationRow.up{background:linear-gradient(90deg,rgba(91,25,44,.5),transparent)}.expectationRow.down{background:linear-gradient(90deg,rgba(20,83,45,.45),transparent)}.expectationRow.realized{background:linear-gradient(90deg,rgba(30,90,68,.5),transparent)}.expectationRow.cancel{background:linear-gradient(90deg,rgba(94,71,17,.45),transparent)}.expectationRow.none{color:#cbd7e6}.expLabel{font-size:10px;font-weight:950;border:1px solid currentColor;border-radius:999px;padding:4px 7px;white-space:nowrap}.expSource{margin-left:auto;color:var(--muted);font-size:11px;white-space:nowrap}
.notifyActions{display:flex;gap:9px;flex-wrap:wrap}.notifyBtn{border-color:#52657c;background:#132033;color:#d9e4f1}.notifyBtn.enabled{border-color:#238579;background:#0e3937;color:#c9fff8}.notifyBtn.blocked{border-color:#8c263b;background:#411522;color:#fecdd3}
@media(max-width:700px){.expectationRow{align-items:flex-start;flex-wrap:wrap}.expSource{margin-left:0;width:100%}.notifyActions{width:100%}.notifyActions .btn{flex:1}}
'''
    html = html.replace('</style>', expectation_css + '</style>', 1)

    expectation_box = '''<section id="priceExpectation" class="expectationBox" aria-live="polite"><div class="expectationTitle">Güncel zam / indirim durumu</div><div id="expectationRows"><div class="expectationRow none">Motorin ve benzin beklentisi kontrol ediliyor…</div></div></section>'''
    html = html.replace('<div class="filters">', expectation_box + '<div class="filters">', 1)

    expectation_js = r'''
<script>
const FUEL_NOTIFY_PREF='fuel_notifications_enabled';
const FUEL_NOTIFY_STATE='fuel_notification_state_v1';
window.__latestFuelExpectationItems=[];
function notificationsEnabled(){return localStorage.getItem(FUEL_NOTIFY_PREF)==='1';}
function readNotifyState(){try{return JSON.parse(localStorage.getItem(FUEL_NOTIFY_STATE)||'{}')}catch(e){return {}}}
function signatureFor(item){return [item.status||'',item.amount??'',item.line||''].join('|');}
function baselineNotifyState(items){const state={};for(const item of items||[]){if(item.fuel_key)state[item.fuel_key]=signatureFor(item)}localStorage.setItem(FUEL_NOTIFY_STATE,JSON.stringify(state));}
function updateNotifyButton(){
  const b=document.getElementById('notifyBtn');if(!b)return;
  if(!('Notification' in window)){b.textContent='🔕 Bildirim Desteklenmiyor';b.className='btn notifyBtn blocked';b.disabled=true;return;}
  if(Notification.permission==='denied'){b.textContent='🚫 Bildirim Tarayıcıda Engelli';b.className='btn notifyBtn blocked';return;}
  const on=notificationsEnabled()&&Notification.permission==='granted';
  b.textContent=on?'🔔 Bildirimler Açık':'🔕 Bildirimler Kapalı';
  b.className='btn notifyBtn '+(on?'enabled':'');
}
async function toggleFuelNotifications(){
  if(!('Notification' in window))return;
  if(notificationsEnabled()&&Notification.permission==='granted'){
    localStorage.setItem(FUEL_NOTIFY_PREF,'0');updateNotifyButton();return;
  }
  if(Notification.permission==='denied'){updateNotifyButton();return;}
  let permission=Notification.permission;
  if(permission==='default')permission=await Notification.requestPermission();
  if(permission==='granted'){
    localStorage.setItem(FUEL_NOTIFY_PREF,'1');
    baselineNotifyState(window.__latestFuelExpectationItems||[]);
    try{new Notification('Petrol Piyasası Takip',{body:'Akaryakıt fiyat bildirimleri açıldı.',tag:'fuel-notifications-enabled'});}catch(e){}
  }else{localStorage.setItem(FUEL_NOTIFY_PREF,'0');}
  updateNotifyButton();
}
function maybeNotifyFuelChanges(items){
  window.__latestFuelExpectationItems=items||[];
  if(!notificationsEnabled()||!('Notification' in window)||Notification.permission!=='granted')return;
  const prior=readNotifyState(),next={...prior};
  for(const item of items||[]){
    if(!item.fuel_key)continue;
    const sig=signatureFor(item),old=prior[item.fuel_key];
    if(old&&old!==sig&&item.status!=='none'){
      try{new Notification('Petrol Piyasası Takip',{body:item.line||'Akaryakıt fiyat durumu güncellendi.',tag:'fuel-'+item.fuel_key});}catch(e){}
    }
    next[item.fuel_key]=sig;
  }
  localStorage.setItem(FUEL_NOTIFY_STATE,JSON.stringify(next));
}
function expectationClass(status){
  if(status==='up')return 'up';
  if(status==='down')return 'down';
  if(String(status).startsWith('realized'))return 'realized';
  if(String(status).startsWith('cancel'))return 'cancel';
  return 'none';
}
function expectationLabel(status){
  if(status==='up'||status==='down')return 'BEKLENTİ';
  if(String(status).startsWith('realized'))return 'GERÇEKLEŞTİ';
  if(String(status).startsWith('cancel'))return 'GÜNCELLEME';
  return 'GÜNCEL DURUM';
}
async function refreshPriceExpectation(forceLive=false){
  const box=document.getElementById('expectationRows');
  if(!box)return;
  try{
    const r=await fetch('/api/price-expectation?t='+Date.now()+(forceLive?'&live=1':''),{cache:'no-store'});
    const d=await r.json();
    if(!r.ok)throw new Error(d.error||'Beklenti taraması başarısız');
    const items=Array.isArray(d.items)?d.items:[];
    box.innerHTML='';
    for(const item of items){
      const row=document.createElement('div');row.className='expectationRow '+expectationClass(item.status);
      const badge=document.createElement('span');badge.className='expLabel';badge.textContent=expectationLabel(item.status);
      row.appendChild(badge);
      if(item.url){
        const a=document.createElement('a');a.href=item.url;a.target='_blank';a.rel='noopener';a.textContent=item.line||'';row.appendChild(a);
      }else{
        const text=document.createElement('strong');text.textContent=item.line||'';row.appendChild(text);
      }
      const src=document.createElement('span');src.className='expSource';
      const confirmations=Number(item.confirmation_count||0);
      const confirmText=confirmations>1?' · '+confirmations+' kaynak doğruladı':'';
      const timing=item.effective_date?' · geçerlilik '+new Date(item.effective_date+'T12:00:00').toLocaleDateString('tr-TR',{day:'2-digit',month:'short'}):'';
      src.textContent=item.source?'· '+item.source+confirmText+timing+(item.official?'':' · haber beklentisi'):'· 12:00 günlük kontrol';
      row.appendChild(src);box.appendChild(row);
    }
    if(!items.length)box.innerHTML='<div class="expectationRow none">Motorin ve benzin için ZAM/İNDİRİM BEKLENTİSİ BULUNMUYOR</div>';
    maybeNotifyFuelChanges(items);
  }catch(e){
    box.innerHTML='<div class="expectationRow none">Beklenti bilgisi geçici olarak alınamadı.</div>';
  }
}
const notificationButton=document.getElementById('notifyBtn');
if(notificationButton)notificationButton.addEventListener('click',toggleFuelNotifications);
updateNotifyButton();
setTimeout(refreshPriceExpectation,0);
const expectationScanButton=document.getElementById('scanBtn');
if(expectationScanButton)expectationScanButton.addEventListener('click',()=>setTimeout(()=>refreshPriceExpectation(true),500));
setInterval(refreshPriceExpectation,600000);
</script>
'''
    html = html.replace('</body>', expectation_js + '</body>', 1)
    html = html.replace('</body>', '<script src="/ios-pwa.js?v=3"></script></body>', 1)
    return html


@app.get('/')
def dashboard_root():
    return HTMLResponse(sector_html(), headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/live.html')
def dashboard_file():
    return HTMLResponse(sector_html(), headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/fiyatlar')
def prices_dashboard():
    return HTMLResponse(prices_html(), headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/prices.html')
def prices_dashboard_file():
    return HTMLResponse(prices_html(), headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/depozito')
def deposit_dashboard():
    return FileResponse(DEPOSIT_DASHBOARD, media_type='text/html; charset=utf-8', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/deposit.html')
def deposit_dashboard_file():
    return FileResponse(DEPOSIT_DASHBOARD, media_type='text/html; charset=utf-8', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/data.json')
def sector_data_file():
    return _snapshot_response('data.json', DATA_FILE)


@app.get('/prices.json')
def prices_data_file():
    return _snapshot_response('prices.json', PRICE_DATA)


@app.get('/price_expectation.json')
def expectation_data_file():
    return _snapshot_response('price_expectation.json', EXPECTATION_DATA)


@app.get('/manifest.webmanifest')
def pwa_manifest():
    return FileResponse(PWA_MANIFEST, media_type='application/manifest+json', headers={'Cache-Control': 'no-cache'})


@app.get('/sw.js')
def pwa_service_worker():
    return FileResponse(PWA_SW, media_type='application/javascript', headers={'Cache-Control': 'no-cache', 'Service-Worker-Allowed': '/'})


@app.get('/ios-pwa.js')
def pwa_ios_script():
    return FileResponse(PWA_IOS, media_type='application/javascript', headers={'Cache-Control': 'no-cache'})


@app.get('/api')
def api_root():
    return {
        'status': 'ready',
        'service': 'Petrol Piyasasi Takip live scan',
        'modules': ['sector', 'prices', 'price_expectation', 'deposit'],
    }


@app.get('/api/scan')
def live_scan():
    try:
        from source_overrides import scan_sector_now
        saved, _ = _snapshot_json('data.json', DATA_FILE)
        return JSONResponse(content=scan_sector_now(saved=saved), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api/deposit')
def live_deposit_scan():
    try:
        from deposit_scan import scan_deposit_now
        return JSONResponse(content=scan_deposit_now(), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api/price-expectation')
def price_expectation(live: int = 0):
    saved_expectation, snapshot_source = _snapshot_json('price_expectation.json', EXPECTATION_DATA)
    saved_prices, _ = _snapshot_json('prices.json', PRICE_DATA)

    if not live and saved_expectation:
        return JSONResponse(
            content=saved_expectation,
            headers={'Cache-Control': 'no-store, max-age=0', 'X-Snapshot-Source': snapshot_source},
        )

    try:
        from price_expectation import scan_price_expectation
        from price_expectation_state import enrich_with_pump_realization
        scanned = scan_price_expectation(saved=saved_expectation, price_data=saved_prices)
        data = enrich_with_pump_realization(scanned, price_data=saved_prices)
        return JSONResponse(content=data, headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        if saved_expectation:
            return JSONResponse(
                content=saved_expectation,
                headers={'Cache-Control': 'no-store, max-age=0', 'X-Snapshot-Source': snapshot_source},
            )
        return JSONResponse(
            status_code=500,
            content={'error': str(exc)[:300], 'items': []},
            headers={'Cache-Control': 'no-store, max-age=0'},
        )


@app.get('/api/prices')
def live_prices():
    try:
        from price_scan import scan_prices
        return JSONResponse(
            content=scan_prices(
                history_days=2,
                include_year=False,
                saved=_snapshot_json('prices.json', PRICE_DATA)[0],
            ),
            headers={'Cache-Control': 'no-store, max-age=0'},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})
