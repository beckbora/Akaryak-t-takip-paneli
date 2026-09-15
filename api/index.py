from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from live_scan import scan_now
from price_scan import scan_prices

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / 'live.html'
PRICE_DASHBOARD = ROOT / 'prices.html'
PRICE_DATA = ROOT / 'prices.json'

app = FastAPI(title='Petrol Piyasası Takip')


@app.get('/')
def dashboard_root():
    return FileResponse(DASHBOARD, media_type='text/html; charset=utf-8')


@app.get('/live.html')
def dashboard_file():
    return FileResponse(DASHBOARD, media_type='text/html; charset=utf-8')


@app.get('/fiyatlar')
def prices_dashboard():
    return FileResponse(PRICE_DASHBOARD, media_type='text/html; charset=utf-8')


@app.get('/prices.html')
def prices_dashboard_file():
    return FileResponse(PRICE_DASHBOARD, media_type='text/html; charset=utf-8')


@app.get('/prices.json')
def prices_data_file():
    return FileResponse(PRICE_DATA, media_type='application/json', headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api')
def api_root():
    return {'status': 'ready', 'service': 'Petrol Piyasasi Takip live scan'}


@app.get('/api/scan')
def live_scan():
    try:
        return JSONResponse(content=scan_now(), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})


@app.get('/api/prices')
def live_prices():
    try:
        return JSONResponse(content=scan_prices(), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})
