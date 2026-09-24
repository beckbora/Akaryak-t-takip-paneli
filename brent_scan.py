import json
from datetime import datetime, timezone

import requests


BARREL_LITERS = 158.987294928
GALLON_LITERS = 3.785411784
VAT_RATE = 0.20
YAHOO_URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PetrolPiyasasiTakip/2.0)",
    "Accept": "application/json,text/plain,*/*",
}


def _num(value):
    try:
        value = float(value)
        return value if value == value else None
    except Exception:
        return None


def _fetch_chart(symbol, range_name="1mo", interval="1d"):
    last_error = None
    for template in YAHOO_URLS:
        try:
            r = requests.get(
                template.format(symbol=requests.utils.quote(symbol, safe="")),
                params={"interval": interval, "range": range_name, "includePrePost": "false"},
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            payload = r.json()
            result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
            if not result:
                raise ValueError("Yahoo Finance sonucu boş")
            meta = result.get("meta") or {}
            timestamps = result.get("timestamp") or []
            closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            history = []
            for ts, close in zip(timestamps, closes):
                value = _num(close)
                if value is None:
                    continue
                history.append({"timestamp": int(ts), "price": value})

            current = _num(meta.get("regularMarketPrice"))
            if current is None and history:
                current = history[-1]["price"]
            previous = _num(meta.get("previousClose")) or _num(meta.get("chartPreviousClose"))
            if previous is None and len(history) >= 2:
                previous = history[-2]["price"]
            if previous is None:
                previous = current
            if current is None:
                raise ValueError("Güncel fiyat bulunamadı")

            return {
                "symbol": symbol,
                "price": current,
                "previous_close": previous,
                "currency": meta.get("currency"),
                "exchange": meta.get("exchangeName"),
                "market_time": meta.get("regularMarketTime"),
                "history": history,
            }
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error)[:240] if last_error else "Yahoo Finance erişilemedi")


def _pressure_label(impact):
    magnitude = abs(float(impact or 0))
    if magnitude < 0.15:
        return "flat", "Belirgin zam/indirim baskısı yok"
    if impact > 0:
        return ("up", "Hafif zam baskısı") if magnitude < 0.50 else ("up", "Zam baskısı")
    return ("down", "Hafif indirim baskısı") if magnitude < 0.50 else ("down", "İndirim baskısı")


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _last_material_change(price_data, fuel_label):
    rows = []
    for row in (price_data or {}).get("changes") or []:
        if row.get("fuel") != fuel_label:
            continue
        try:
            delta = float(row.get("delta") or 0)
        except Exception:
            continue
        if abs(delta) < 0.50:
            continue
        dt = _parse_dt(row.get("detected_at"))
        if not dt:
            continue
        rows.append({
            "dt": dt,
            "delta": delta,
            "location": str(row.get("location") or row.get("location_key") or ""),
        })
    if not rows:
        return None

    rows.sort(key=lambda x: x["dt"], reverse=True)

    # A Turkey-wide pump reset should appear in several cities in the same scan
    # window. Do not let a one-city/local adjustment reset the market baseline.
    for candidate in rows:
        direction = 1 if candidate["delta"] > 0 else -1
        cohort = [
            x for x in rows
            if (1 if x["delta"] > 0 else -1) == direction
            and abs((candidate["dt"] - x["dt"]).total_seconds()) <= 45 * 60
        ]
        locations = {x["location"] for x in cohort if x["location"]}
        if len(locations) >= 3:
            return max(x["dt"] for x in cohort)

    return None


def _history_price_at_or_before(quote, target_dt):
    history = (quote or {}).get("history") or []
    if not history or not target_dt:
        return None
    target_ts = int(target_dt.timestamp())
    eligible = [x for x in history if int(x.get("timestamp") or 0) <= target_ts]
    if eligible:
        return float(max(eligible, key=lambda x: x["timestamp"])["price"])
    # If the baseline predates the downloaded window, use the earliest point and
    # expose that fact in the model metadata rather than silently inventing data.
    return float(history[0]["price"])


def _signed_expectation(expectation_data, fuel_key):
    item = next(
        (x for x in (expectation_data or {}).get("items") or [] if x.get("fuel_key") == fuel_key),
        None,
    )
    if not item or item.get("status") not in {"up", "down"} or item.get("amount") is None:
        return None
    amount = abs(float(item.get("amount") or 0))
    signed = amount if item.get("status") == "up" else -amount
    return {
        "status": item.get("status"),
        "amount": amount,
        "signed_amount": signed,
        "line": item.get("line"),
        "source": item.get("source"),
        "url": item.get("url"),
        "confirmation_count": int(item.get("confirmation_count") or 0),
        "effective_date": item.get("effective_date"),
    }


def _compare_model(model_value, expectation):
    if not expectation:
        return {"available": False, "label": "Haber beklentisi yok", "status": "none"}
    expected = float(expectation["signed_amount"])
    model = float(model_value or 0)
    same_direction = (expected == 0 and model == 0) or (expected * model > 0)
    diff = round(model - expected, 2)
    abs_diff = abs(diff)
    if not same_direction:
        label, status = "Yön farklı", "low"
    elif abs_diff <= 0.35:
        label, status = "Yüksek uyum", "high"
    elif abs_diff <= 0.80:
        label, status = "Yakın", "medium"
    else:
        label, status = "Aynı yön, tutar farklı", "low"
    return {
        "available": True,
        "label": label,
        "status": status,
        "model_tl_l": round(model, 2),
        "expectation_tl_l": round(expected, 2),
        "difference_tl_l": diff,
        "confirmation_count": expectation.get("confirmation_count", 0),
        "source": expectation.get("source"),
        "url": expectation.get("url"),
        "effective_date": expectation.get("effective_date"),
    }


def _fuel_model(fuel_key, fuel_label, product_quote, fx_quote, baseline_dt, expectation):
    now_product = float(product_quote["price"])
    now_fx = float(fx_quote["price"])

    if baseline_dt:
        base_product = _history_price_at_or_before(product_quote, baseline_dt)
        base_fx = _history_price_at_or_before(fx_quote, baseline_dt)
        basis = "son gerçekleşen pompa değişiminden beri"
    else:
        base_product = float(product_quote.get("previous_close") or now_product)
        base_fx = float(fx_quote.get("previous_close") or now_fx)
        basis = "önceki piyasa kapanışından beri"

    if base_product is None:
        base_product = float(product_quote.get("previous_close") or now_product)
    if base_fx is None:
        base_fx = float(fx_quote.get("previous_close") or now_fx)

    current_tl_l = now_product * now_fx / GALLON_LITERS
    baseline_tl_l = base_product * base_fx / GALLON_LITERS
    impact = (current_tl_l - baseline_tl_l) * (1.0 + VAT_RATE)
    status, label = _pressure_label(impact)

    product_change_pct = ((now_product / base_product) - 1.0) * 100.0 if base_product else 0
    fx_change_pct = ((now_fx / base_fx) - 1.0) * 100.0 if base_fx else 0
    comparison = _compare_model(impact, expectation)

    return {
        "fuel_key": fuel_key,
        "fuel": fuel_label,
        "status": status,
        "label": label,
        "estimated_impact_tl_l": round(impact, 2),
        "basis": basis,
        "baseline_at": baseline_dt.isoformat() if baseline_dt else None,
        "product_symbol": product_quote.get("symbol"),
        "product_price_usd_gal": round(now_product, 4),
        "baseline_product_price_usd_gal": round(base_product, 4),
        "product_change_percent": round(product_change_pct, 2),
        "fx_change_percent": round(fx_change_pct, 2),
        "proxy_cost_now_tl_l": round(current_tl_l, 3),
        "proxy_cost_baseline_tl_l": round(baseline_tl_l, 3),
        "comparison": comparison,
        "note": (
            "Bitmiş ürün fiyatı için ücretsiz piyasa proxy'si kullanılır. "
            "EPDK'nın referans verdiği CIF MED/Platts verisinin birebir karşılığı değildir."
        ),
    }


def scan_brent(saved=None, price_data=None, expectation_data=None):
    saved = saved if isinstance(saved, dict) else {}
    price_data = price_data if isinstance(price_data, dict) else {}
    expectation_data = expectation_data if isinstance(expectation_data, dict) else {}
    now = datetime.now(timezone.utc)
    sources = []

    quotes = {}
    for symbol, label in [
        ("BZ=F", "Yahoo Finance · Brent BZ=F"),
        ("HO=F", "Yahoo Finance · Motorin/Distilat proxy HO=F"),
        ("RB=F", "Yahoo Finance · Benzin proxy RB=F"),
        ("TRY=X", "Yahoo Finance · USD/TRY"),
    ]:
        try:
            quotes[symbol] = _fetch_chart(symbol)
            sources.append({"source": label, "ok": True})
        except Exception as exc:
            sources.append({"source": label, "ok": False, "error": str(exc)[:180]})

    required = ("BZ=F", "HO=F", "RB=F", "TRY=X")
    if any(x not in quotes for x in required):
        fallback = dict(saved)
        fallback["updated_at"] = now.isoformat()
        fallback["live_ok"] = False
        fallback["sources"] = sources
        fallback["error"] = "Brent, ürün proxy'si veya USD/TL verisi alınamadı; son kayıt gösteriliyor."
        return fallback

    brent = quotes["BZ=F"]
    fx = quotes["TRY=X"]
    brent_now = float(brent["price"])
    brent_prev = float(brent.get("previous_close") or brent_now)
    fx_now = float(fx["price"])
    fx_prev = float(fx.get("previous_close") or fx_now)

    crude_now = brent_now * fx_now / BARREL_LITERS
    crude_prev = brent_prev * fx_prev / BARREL_LITERS
    crude_daily_impact = (crude_now - crude_prev) * (1.0 + VAT_RATE)
    crude_status, crude_label = _pressure_label(crude_daily_impact)

    diesel_baseline = _last_material_change(price_data, "Motorin")
    gasoline_baseline = _last_material_change(price_data, "Benzin 95")
    diesel_exp = _signed_expectation(expectation_data, "diesel")
    gasoline_exp = _signed_expectation(expectation_data, "gasoline")

    diesel_model = _fuel_model(
        "diesel", "MOTORİN", quotes["HO=F"], fx, diesel_baseline, diesel_exp
    )
    gasoline_model = _fuel_model(
        "gasoline", "BENZİN", quotes["RB=F"], fx, gasoline_baseline, gasoline_exp
    )

    active_model = diesel_model if diesel_exp else (gasoline_model if gasoline_exp else max(
        [diesel_model, gasoline_model],
        key=lambda x: abs(float(x.get("estimated_impact_tl_l") or 0)),
    ))

    history = list(saved.get("history") or [])
    history.append({
        "timestamp": now.isoformat(),
        "brent_usd": round(brent_now, 4),
        "usdtry": round(fx_now, 4),
        "diesel_model_tl_l": diesel_model["estimated_impact_tl_l"],
        "gasoline_model_tl_l": gasoline_model["estimated_impact_tl_l"],
    })
    history = history[-1008:]

    return {
        "updated_at": now.isoformat(),
        "live_ok": True,
        "brent": {
            "price_usd_bbl": round(brent_now, 4),
            "previous_close_usd_bbl": round(brent_prev, 4),
            "change_usd": round(brent_now - brent_prev, 4),
            "change_percent": round(((brent_now / brent_prev) - 1.0) * 100.0, 3) if brent_prev else 0,
            "symbol": "BZ=F",
            "quote_type": "delayed_futures_quote",
        },
        "usdtry": {
            "rate": round(fx_now, 4),
            "previous_close": round(fx_prev, 4),
            "change_percent": round(((fx_now / fx_prev) - 1.0) * 100.0, 3) if fx_prev else 0,
            "symbol": "TRY=X",
        },
        "fuel_models": {
            "diesel": diesel_model,
            "gasoline": gasoline_model,
        },
        "turkey_model": {
            "status": active_model["status"],
            "label": active_model["label"],
            "estimated_pump_impact_tl_l": active_model["estimated_impact_tl_l"],
            "fuel_key": active_model["fuel_key"],
            "basis": active_model["basis"],
            "formula": "bitmiş ürün proxy × USD/TL değişimi × KDV",
            "note": (
                "Ana Türkiye tahmini artık günlük Brent farkını değil, ilgili bitmiş ürün proxy'sinin "
                "son gerçek pompa değişiminden bu yana USD/TL ile birlikte biriken maliyet farkını kullanır."
            ),
        },
        "brent_only_context": {
            "status": crude_status,
            "label": crude_label,
            "daily_theoretical_impact_tl_l": round(crude_daily_impact, 2),
        },
        "history": history,
        "sources": sources,
        "version": 2,
    }


if __name__ == "__main__":
    print(json.dumps(scan_brent(), ensure_ascii=False, indent=2))
