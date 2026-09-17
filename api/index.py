from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from source_overrides import scan_sector_now
from price_scan import scan_prices
from price_expectation import scan_price_expectation
from deposit_scan import scan_deposit_now

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / 'live.html'
PRICE_DASHBOARD = ROOT / 'prices.html'
DEPOSIT_DASHBOARD = ROOT / 'deposit.html'
PRICE_DATA = ROOT / 'prices.json'
EXPECTATION_DATA = ROOT / 'price_expectation.json'

app = FastAPI(title='Petrol Piyasası Takip')


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

    # Make the annual series unmistakable: gasoline orange, diesel electric blue.
    html = html.replace("line(g,'#5eead4')+line(d,'#7dd3fc')", "line(g,'#f59e0b')+line(d,'#38bdf8')")
    html = html.replace('style="background:#5eead4"></i>Benzin 95', 'style="background:#f59e0b"></i>Benzin 95')
    html = html.replace('style="background:#7dd3fc"></i>Motorin', 'style="background:#38bdf8"></i>Motorin')

    expectation_css = '''
.expectationBox{margin:13px 0;border:1px solid var(--line);border-radius:14px;background:#0d1a2b;overflow:hidden}
.expectationTitle{padding:8px 13px;border-bottom:1px solid var(--line);font-size:11px;color:var(--muted);font-weight:900;letter-spacing:.05em;text-transform:uppercase}
.expectationRow{display:flex;align-items:center;gap:9px;padding:10px 13px;border-bottom:1px solid rgba(38,59,90,.55);font-size:13px;min-height:42px}
.expectationRow:last-child{border-bottom:0}.expectationRow a{color:inherit;text-decoration:none;font-weight:900}.expectationRow.up{background:linear-gradient(90deg,rgba(91,25,44,.5),transparent)}.expectationRow.down{background:linear-gradient(90deg,rgba(20,83,45,.45),transparent)}.expectationRow.realized{background:linear-gradient(90deg,rgba(30,90,68,.5),transparent)}.expectationRow.cancel{background:linear-gradient(90deg,rgba(94,71,17,.45),transparent)}.expectationRow.none{color:#cbd7e6}.expLabel{font-size:10px;font-weight:950;border:1px solid currentColor;border-radius:999px;padding:4px 7px;white-space:nowrap}.expSource{margin-left:auto;color:var(--muted);font-size:11px;white-space:nowrap}@media(max-width:700px){.expectationRow{align-items:flex-start;flex-wrap:wrap}.expSource{margin-left:0;width:100%}}
'''
    html = html.replace('</style>', expectation_css + '</style>', 1)

    expectation_box = '''<section id="priceExpectation" class="expectationBox" aria-live="polite"><div class="expectationTitle">Güncel zam / indirim durumu</div><div id="expectationRows"><div class="expectationRow none">Motorin ve benzin beklentisi kontrol ediliyor…</div></div></section>'''
    html = html.replace('<div class="filters">', expectation_box + '<div class="filters">', 1)

    expectation_js = r'''
<script>
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
async function refreshPriceExpectation(){
  const box=document.getElementById('expectationRows');
  if(!box)return;
  try{
    const r=await fetch('/api/price-expectation?t='+Date.now(),{cache:'no-store'});
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
      src.textContent=item.source?'· '+item.source+(item.official?'':' · RESMÎ DEĞİL'):'· 12:00 günlük kontrol';
      row.appendChild(src);box.appendChild(row);
    }
    if(!items.length)box.innerHTML='<div class="expectationRow none">Motorin ve benzin için ZAM/İNDİRİM BEKLENTİSİ BULUNMUYOR</div>';
  }catch(e){
    box.innerHTML='<div class="expectationRow none">Beklenti bilgisi geçici olarak alınamadı.</div>';
  }
}
setTimeout(refreshPriceExpectation,0);
const expectationScanButton=document.getElementById('scanBtn');
if(expectationScanButton)expectationScanButton.addEventListener('click',()=>setTimeout(refreshPriceExpectation,500));
setInterval(refreshPriceExpectation,600000);
</script>
'''
    html = html.replace('</body>', expectation_js + '</body>', 1)
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


@app.get('/prices.json')
def prices_data_file():
    return FileResponse(PRICE_DATA, media_type='application/json', headers={'Cache-Control': 'no-store, max-age=0'})


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
        return JSONResponse(content=scan_sector_now(), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api/deposit')
def live_deposit_scan():
    try:
        return JSONResponse(content=scan_deposit_now(), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api/price-expectation')
def price_expectation():
    try:
        return JSONResponse(content=scan_price_expectation(), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        if EXPECTATION_DATA.exists():
            try:
                import json
                return JSONResponse(content=json.loads(EXPECTATION_DATA.read_text(encoding='utf-8')), headers={'Cache-Control': 'no-store, max-age=0'})
            except Exception:
                pass
        return JSONResponse(status_code=500, content={'error': str(exc)[:300], 'items': []}, headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api/prices')
def live_prices():
    try:
        return JSONResponse(
            content=scan_prices(history_days=2, include_year=False),
            headers={'Cache-Control': 'no-store, max-age=0'},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})
