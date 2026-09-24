import json
import re
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup


BARREL_LITERS = 158.987294928
VAT_RATE = 0.20
DENSITY = {"diesel": 0.845, "gasoline": 0.775}
CIF_MED_URLS = {
    "diesel": "https://commodityscope.com/prices/ulsd-10-ppm-mediterranean-cif",
    "gasoline": "https://commodityscope.com/prices/gasoline-10-ppm-mediterranean-cif",
}
YAHOO_URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PetrolPiyasasiTakip/3.0)",
    "Accept": "text/html,application/json,text/plain,*/*",
}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
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
                if value is not None:
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
                "market_time": meta.get("regularMarketTime"),
                "history": history,
            }
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error)[:240] if last_error else "Yahoo Finance erişilemedi")


def _parse_english_date(value):
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})", value or "", re.I)
    if not m:
        return None
    month = MONTHS.get(m.group(2).casefold())
    if not month:
        return None
    return datetime(int(m.group(3)), month, int(m.group(1)), 23, 59, tzinfo=timezone.utc)


def _fetch_cif_med(fuel_key):
    url = CIF_MED_URLS[fuel_key]
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
    price_match = re.search(
        r"latest indicative .*? assessment is\s*([\d,.]+)\s*USD/mt",
        text,
        re.I,
    )
    date_match = re.search(r"dated\s+(\d{1,2}\s+[A-Za-z]+\s+20\d{2})", text, re.I)
    if not price_match or not date_match:
        # Fallback to the headline blocks used by the site.
        price_match = price_match or re.search(
            r"(?:Assessed Mid \(mt\)|CommodityScope Assessment)\s*\$?([\d,.]+)",
            text,
            re.I,
        )
        date_match = date_match or re.search(
            r"Assessment date\s+(\d{1,2}\s+[A-Za-z]+\s+20\d{2})",
            text,
            re.I,
        )
    if not price_match or not date_match:
        raise ValueError("CIF Med fiyatı veya tarihi ayrıştırılamadı")
    price = float(price_match.group(1).replace(",", ""))
    at = _parse_english_date(date_match.group(1))
    if not at:
        raise ValueError("CIF Med tarihi ayrıştırılamadı")
    return {
        "fuel_key": fuel_key,
        "price_usd_mt": price,
        "assessment_at": at,
        "assessment_date": at.date().isoformat(),
        "url": url,
        "source": "CommodityScope · CIF Med indicative",
    }


def _price_at_or_before(quote, target_dt):
    history = (quote or {}).get("history") or []
    if not history:
        return None
    target_ts = int(target_dt.timestamp())
    eligible = [x for x in history if int(x.get("timestamp") or 0) <= target_ts]
    if eligible:
        return float(max(eligible, key=lambda x: x["timestamp"])["price"])
    return float(history[0]["price"])


def _pressure_label(impact):
    magnitude = abs(float(impact or 0))
    if magnitude < 0.15:
        return "flat", "Belirgin zam/indirim baskısı yok"
    if impact > 0:
        return ("up", "Hafif zam baskısı") if magnitude < 0.50 else ("up", "Zam baskısı")
    return ("down", "Hafif indirim baskısı") if magnitude < 0.50 else ("down", "İndirim baskısı")


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
        "signed_amount": signed,
        "amount": amount,
        "confirmation_count": int(item.get("confirmation_count") or 0),
        "source": item.get("source"),
        "url": item.get("url"),
        "effective_date": item.get("effective_date"),
    }


def _compare_model(model_value, expectation):
    if not expectation:
        return {"available": False, "label": "Haber beklentisi yok", "status": "none"}
    expected = float(expectation["signed_amount"])
    model = float(model_value or 0)
    same_direction = expected * model > 0
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


def _cif_nowcast_model(fuel_key, fuel_label, cif, brent, fx, expectation):
    at = cif["assessment_at"]
    brent_base = _price_at_or_before(brent, at) or float(brent.get("previous_close") or brent["price"])
    fx_base = _price_at_or_before(fx, at) or float(fx.get("previous_close") or fx["price"])
    brent_now = float(brent["price"])
    fx_now = float(fx["price"])

    # CIF Med is the correct market basis class, but free indicative data is daily.
    # Nowcast today's still-unpublished product level by carrying forward the Brent
    # percentage move since the latest CIF Med assessment.
    brent_ratio = brent_now / brent_base if brent_base else 1.0
    cif_nowcast = float(cif["price_usd_mt"]) * brent_ratio

    density = DENSITY[fuel_key]
    baseline_tl_l = float(cif["price_usd_mt"]) * fx_base * density / 1000.0
    nowcast_tl_l = cif_nowcast * fx_now * density / 1000.0
    pump_impact = (nowcast_tl_l - baseline_tl_l) * (1.0 + VAT_RATE)
    status, label = _pressure_label(pump_impact)

    return {
        "fuel_key": fuel_key,
        "fuel": fuel_label,
        "status": status,
        "label": label,
        "estimated_impact_tl_l": round(pump_impact, 2),
        "basis": f"CIF Med {cif['assessment_date']} → 10 dk Brent/kur nowcast",
        "baseline_at": at.isoformat(),
        "cif_med_price_usd_mt": round(float(cif["price_usd_mt"]), 2),
        "cif_med_nowcast_usd_mt": round(cif_nowcast, 2),
        "brent_base_usd_bbl": round(brent_base, 4),
        "brent_now_usd_bbl": round(brent_now, 4),
        "brent_change_percent_since_cif": round((brent_ratio - 1.0) * 100.0, 2),
        "fx_base": round(fx_base, 4),
        "fx_now": round(fx_now, 4),
        "fx_change_percent_since_cif": round(((fx_now / fx_base) - 1.0) * 100.0, 2) if fx_base else 0,
        "baseline_product_cost_tl_l": round(baseline_tl_l, 3),
        "nowcast_product_cost_tl_l": round(nowcast_tl_l, 3),
        "comparison": _compare_model(pump_impact, expectation),
        "source": cif["source"],
        "source_url": cif["url"],
        "note": (
            "EPDK'nın referans sınıfı olan CIF Med'e yakın ücretsiz günlük indicative veri kullanılır. "
            "Gün içi 10 dakikalık nowcast, son yayımlanan CIF Med seviyesini Brent ve USD/TL hareketiyle taşır. "
            "Platts lisanslı verisinin birebir yerine geçmez."
        ),
    }


def scan_brent(saved=None, price_data=None, expectation_data=None):
    saved = saved if isinstance(saved, dict) else {}
    expectation_data = expectation_data if isinstance(expectation_data, dict) else {}
    now = datetime.now(timezone.utc)
    sources = []

    try:
        brent = _fetch_chart("BZ=F")
        sources.append({"source": "Yahoo Finance · Brent BZ=F", "ok": True})
    except Exception as exc:
        brent = None
        sources.append({"source": "Yahoo Finance · Brent BZ=F", "ok": False, "error": str(exc)[:180]})
    try:
        fx = _fetch_chart("TRY=X")
        sources.append({"source": "Yahoo Finance · USD/TRY", "ok": True})
    except Exception as exc:
        fx = None
        sources.append({"source": "Yahoo Finance · USD/TRY", "ok": False, "error": str(exc)[:180]})

    cif = {}
    for fuel_key, label in (("diesel", "CIF Med ULSD"), ("gasoline", "CIF Med Benzin")):
        try:
            cif[fuel_key] = _fetch_cif_med(fuel_key)
            sources.append({"source": f"CommodityScope · {label}", "ok": True})
        except Exception as exc:
            sources.append({"source": f"CommodityScope · {label}", "ok": False, "error": str(exc)[:180]})

    if not brent or not fx or "diesel" not in cif or "gasoline" not in cif:
        fallback = dict(saved)
        fallback["updated_at"] = now.isoformat()
        fallback["live_ok"] = False
        fallback["sources"] = sources
        fallback["error"] = "Brent, USD/TL veya CIF Med verisi alınamadı; son kayıt gösteriliyor."
        return fallback

    diesel_exp = _signed_expectation(expectation_data, "diesel")
    gasoline_exp = _signed_expectation(expectation_data, "gasoline")
    diesel_model = _cif_nowcast_model("diesel", "MOTORİN", cif["diesel"], brent, fx, diesel_exp)
    gasoline_model = _cif_nowcast_model("gasoline", "BENZİN", cif["gasoline"], brent, fx, gasoline_exp)

    active_model = diesel_model if diesel_exp else (gasoline_model if gasoline_exp else max(
        [diesel_model, gasoline_model],
        key=lambda x: abs(float(x.get("estimated_impact_tl_l") or 0)),
    ))

    brent_now = float(brent["price"])
    brent_prev = float(brent.get("previous_close") or brent_now)
    fx_now = float(fx["price"])
    fx_prev = float(fx.get("previous_close") or fx_now)
    crude_now = brent_now * fx_now / BARREL_LITERS
    crude_prev = brent_prev * fx_prev / BARREL_LITERS
    crude_daily_impact = (crude_now - crude_prev) * (1.0 + VAT_RATE)
    crude_status, crude_label = _pressure_label(crude_daily_impact)

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
        "cif_med": {
            "diesel": {k: v for k, v in cif["diesel"].items() if k != "assessment_at"},
            "gasoline": {k: v for k, v in cif["gasoline"].items() if k != "assessment_at"},
        },
        "fuel_models": {"diesel": diesel_model, "gasoline": gasoline_model},
        "turkey_model": {
            "status": active_model["status"],
            "label": active_model["label"],
            "estimated_pump_impact_tl_l": active_model["estimated_impact_tl_l"],
            "fuel_key": active_model["fuel_key"],
            "basis": active_model["basis"],
            "formula": "CIF Med baz × Brent nowcast × USD/TL × yoğunluk × KDV",
            "note": "Türkiye tahmini, son günlük CIF Med ürün seviyesinin Brent ve kurla 10 dakikalık nowcast'idir.",
        },
        "brent_only_context": {
            "status": crude_status,
            "label": crude_label,
            "daily_theoretical_impact_tl_l": round(crude_daily_impact, 2),
        },
        "history": history,
        "sources": sources,
        "version": 3,
    }


if __name__ == "__main__":
    print(json.dumps(scan_brent(), ensure_ascii=False, indent=2))
