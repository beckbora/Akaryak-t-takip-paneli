from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from source_overrides import scan_sector_now
from price_scan import scan_prices

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / 'live.html'
PRICE_DASHBOARD = ROOT / 'prices.html'
PRICE_DATA = ROOT / 'prices.json'

app = FastAPI(title='Petrol Piyasası Takip')


def sector_html():
    html = DASHBOARD.read_text(encoding='utf-8')
    nav = '''<nav style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px"><a href="/" style="color:#cafff8;text-decoration:none;border:1px solid #20796f;background:#103a37;border-radius:999px;padding:9px 13px;font-size:13px;font-weight:850">Mevzuat &amp; Sektör Radar</a><a href="/fiyatlar" style="color:#cbd7e6;text-decoration:none;border:1px solid #263b5a;background:#0c1929;border-radius:999px;padding:9px 13px;font-size:13px;font-weight:850">⛽ Fiyat Radar</a></nav>'''
    marker = '<header class="top">'
    if marker in html:
        html = html.replace(marker, nav + marker, 1)
    return html


@app.get('/')
def dashboard_root():
    return HTMLResponse(sector_html(), headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/live.html')
def dashboard_file():
    return HTMLResponse(sector_html(), headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/fiyatlar')
def prices_dashboard():
    return FileResponse(PRICE_DASHBOARD, media_type='text/html; charset=utf-8', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/prices.html')
def prices_dashboard_file():
    return FileResponse(PRICE_DASHBOARD, media_type='text/html; charset=utf-8', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/prices.json')
def prices_data_file():
    return FileResponse(PRICE_DATA, media_type='application/json', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api')
def api_root():
    return {'status': 'ready', 'service': 'Petrol Piyasasi Takip live scan'}


@app.get('/api/scan')
def live_scan():
    try:
        return JSONResponse(content=scan_sector_now(), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})


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
