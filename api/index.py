from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from live_scan import scan_now

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / 'live.html'

app = FastAPI(title='Petrol Piyasası Takip')


@app.get('/')
def dashboard_root():
    return FileResponse(DASHBOARD, media_type='text/html; charset=utf-8')


@app.get('/live.html')
def dashboard_file():
    return FileResponse(DASHBOARD, media_type='text/html; charset=utf-8')


@app.get('/api')
def api_root():
    return {'status': 'ready', 'service': 'Petrol Piyasasi Takip live scan'}


@app.get('/api/scan')
def live_scan():
    try:
        return JSONResponse(content=scan_now(), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})
