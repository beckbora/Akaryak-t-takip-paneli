from fastapi import FastAPI
from fastapi.responses import JSONResponse

from live_scan import scan_now

app = FastAPI()


@app.get('/api')
def api_root():
    return {'status': 'ready', 'service': 'Akaryakit Takip live scan'}


@app.get('/api/scan')
def live_scan():
    try:
        return JSONResponse(content=scan_now(), headers={'Cache-Control': 'no-store, max-age=0'})
    except Exception as exc:
        return JSONResponse(status_code=500, content={'error': str(exc)[:300]}, headers={'Cache-Control': 'no-store, max-age=0'})
