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

    expectation_css = '''
.expectationBar{display:none;margin:13px 0;border:1px solid var(--line);border-radius:14px;background:#0d1a2b;overflow:hidden}
.expectationBar.up{border-color:#743047;background:linear-gradient(90deg,rgba(91,25,44,.62),#0d1a2b)}
.expectationBar.down{border-color:#2e7147;background:linear-gradient(90deg,rgba(20,83,45,.55),#0d1a2b)}
.expectationBar.cancel{border-color:#8a681d;background:linear-gradient(90deg,rgba(94,71,17,.55),#0d1a2b)}
.expectationBar a{display:flex;align-items:center;gap:8px;padding:11px 14px;color:var(--text);text-decoration:none;white-space:nowrap;overflow:auto;font-size:13px}
.expectationBar strong{font-size:13px;letter-spacing:.01em}.expLabel{font-size:10px;font-weight:950;border:1px solid currentColor;border-radius:999px;padding:4px 7px;opacity:.9}.expSource{color:var(--muted);font-size:11px}
'''
    html = html.replace('</style>', expectation_css + '</style>', 1)

    expectation_bar = '<div id="priceExpectation" class="expectationBar" aria-live="polite"></div>'
    html = html.replace('<div class="filters">', expectation_bar + '<div class="filters">', 1)

    expectation_js = r'''
<script>
async function refreshPriceExpectation(){
  const bar=document.getElementById('priceExpectation');
  if(!bar)return;
  try{
    const r=await fetch('/api/price-expectation?t='+Date.now(),{cache:'no-store'});
    const d=await r.json();
    if(!r.ok||!d.found){bar.style.display='none';return;}
    const cls=d.status==='up'?'up':d.status==='down'?'down':'cancel';
    bar.className='expectationBar '+cls;
    bar.innerHTML='';
    const a=document.createElement('a');
    a.href=d.url||'#';a.target='_blank';a.rel='noopener';a.title='Haber kaynağını aç';
    const badge=document.createElement('span');badge.className='expLabel';badge.textContent=String(d.status||'').startsWith('cancel')?'GÜNCELLEME':'BEKLENTİ';
    const text=document.createElement('strong');text.textContent=d.line||'';
    const src=document.createElement('span');src.className='expSource';src.textContent='· '+(d.source||'Haber kaynağı')+' · RESMÎ DEĞİL';
    a.appendChild(badge);a.appendChild(text);a.appendChild(src);bar.appendChild(a);bar.style.display='block';
  }catch(e){bar.style.display='none';}
}
setTimeout(refreshPriceExpectation,0);
const expectationScanButton=document.getElementById('scanBtn');
if(expectationScanButton)expectationScanButton.addEventListener('click',()=>setTimeout(refreshPriceExpectation,300));
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
        return JSONResponse(status_code=500, content={'error': str(exc)[:300], 'found': False}, headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api/prices')
def live_prices():
    try:
        # One-year EPDK history is refreshed by the scheduled job and read from prices.json.
        # Live button stays fast by checking only current/short-period sources.
        return JSONResponse(
            content=scan_prices(history_days=2, include_year=False),
            headers={'Cache-Control': 'no-store, max-age=0'},
        )
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})
