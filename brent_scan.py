import json
from datetime import datetime, timezone

import requests


BARREL_LITERS = 158.987294928
VAT_RATE = 0.20
YAHOO_URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PetrolPiyasasiTakip/1.0)",
    "Accept": "application/json,text/plain,*/*",
}


def _num(value):
    try:
        value = float(value)
        return value if value == value else None
    except Exception:
        return None


def _fetch_quote(symbol):
    last_error = None
    for template in YAHOO_URLS:
        try:
            r = requests.get(
                template.format(symbol=requests.utils.quote(symbol, safe="")),
                params={"interval": "5m", "range": "5d", "includePrePost": "false"},
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
            result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                raise ValueError("Yahoo Finance sonucu boş")
            meta = result.get("meta") or {}
            current = _num(meta.get("regularMarketPrice"))
            previous = _num(meta.get("previousClose")) or _num(meta.get("chartPreviousClose"))
            if current is None:
                closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
                valid = [_num(x) for x in closes]
                valid = [x for x in valid if x is not None]
                current = valid[-1] if valid else None
            if previous is None:
                previous = current
            if current is None:
                raise ValueError("Güncel fiyat bulunamadı")
            return {"symbol": symbol, "price": current, "previous_close": previous, "currency": meta.get("currency"), "market_time": meta.get("regularMarketTime")}
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error)[:240] if last_error else "Yahoo Finance erişilemedi")


def _pressure_label(impact):
    magnitude = abs(impact)
    if magnitude < 0.15:
        return "flat", "Belirgin zam/indirim baskısı yok"
    if impact > 0:
        return ("up", "Hafif zam baskısı") if magnitude < 0.50 else ("up", "Zam baskısı")
    return ("down", "Hafif indirim baskısı") if magnitude < 0.50 else ("down", "İndirim baskısı")


def scan_brent(saved=None):
    saved = saved if isinstance(saved, dict) else {}
    now = datetime.now(timezone.utc)
    sources = []
    try:
        brent = _fetch_quote("BZ=F")
        sources.append({"source": "Yahoo Finance · Brent BZ=F", "ok": True})
    except Exception as exc:
        brent = None
        sources.append({"source": "Yahoo Finance · Brent BZ=F", "ok": False, "error": str(exc)[:180]})
    try:
        fx = _fetch_quote("TRY=X")
        sources.append({"source": "Yahoo Finance · USD/TRY", "ok": True})
    except Exception as exc:
        fx = None
        sources.append({"source": "Yahoo Finance · USD/TRY", "ok": False, "error": str(exc)[:180]})

    if not brent or not fx:
        fallback = dict(saved)
        fallback["updated_at"] = now.isoformat()
        fallback["live_ok"] = False
        fallback["sources"] = sources
        fallback["error"] = "Brent veya USD/TL verisi alınamadı; son kayıt gösteriliyor."
        return fallback

    brent_now = float(brent["price"])
    brent_prev = float(brent.get("previous_close") or brent_now)
    fx_now = float(fx["price"])
    fx_prev = float(fx.get("previous_close") or fx_now)
    crude_now = brent_now * fx_now / BARREL_LITERS
    crude_prev = brent_prev * fx_prev / BARREL_LITERS
    pump_impact = (crude_now - crude_prev) * (1.0 + VAT_RATE)
    status, label = _pressure_label(pump_impact)

    history = list(saved.get("history") or [])
    history.append({"timestamp": now.isoformat(), "brent_usd": round(brent_now, 4), "usdtry": round(fx_now, 4), "estimated_impact_tl_l": round(pump_impact, 4)})
    history = history[-1008:]

    return {
        "updated_at": now.isoformat(),
        "live_ok": True,
        "brent": {"price_usd_bbl": round(brent_now, 4), "previous_close_usd_bbl": round(brent_prev, 4), "change_usd": round(brent_now-brent_prev, 4), "change_percent": round(((brent_now/brent_prev)-1)*100, 3) if brent_prev else 0, "symbol": "BZ=F", "quote_type": "delayed_futures_quote"},
        "usdtry": {"rate": round(fx_now, 4), "previous_close": round(fx_prev, 4), "change_percent": round(((fx_now/fx_prev)-1)*100, 3) if fx_prev else 0, "symbol": "TRY=X"},
        "turkey_model": {"status": status, "label": label, "estimated_pump_impact_tl_l": round(pump_impact, 2), "raw_crude_cost_now_tl_l": round(crude_now, 3), "raw_crude_cost_previous_tl_l": round(crude_prev, 3), "basis": "önceki piyasa kapanışı", "formula": "((Brent×USD/TL)/158.987 farkı) × 1.20 KDV", "note": "Bu resmi zam/indirim tutarı değildir. Sadece Brent ve USD/TL kaynaklı teorik pompa fiyatı baskısını gösterir; ürün marjı, Akdeniz ürün fiyatı, rafineri/dağıtım marjı ve vergi kararları dahil değildir."},
        "history": history,
        "sources": sources,
        "version": 1,
    }


if __name__ == "__main__":
    print(json.dumps(scan_brent(), ensure_ascii=False, indent=2))
