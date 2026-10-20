import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent
PRICE_DATA = ROOT / 'prices.json'


def _read_prices():
    try:
        data = json.loads(PRICE_DATA.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _fmt(value):
    return f'{value:.2f}'.replace('.', ',')


def enrich_with_pump_realization(data, price_data=None):
    if not isinstance(data, dict):
        data = {'items': []}
    price_data = price_data if isinstance(price_data, dict) else _read_prices()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=30)

    by_fuel = {'diesel': [], 'gasoline': [], 'lpg': []}
    for change in price_data.get('changes') or []:
        label = change.get('fuel')
        if label == 'Motorin':
            fuel_key = 'diesel'
        elif label == 'Benzin 95':
            fuel_key = 'gasoline'
        elif label == 'LPG/Otogaz':
            fuel_key = 'lpg'
        else:
            continue
        try:
            detected = datetime.fromisoformat(change.get('detected_at'))
            delta = float(change.get('delta') or 0)
        except Exception:
            continue
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)
        if detected < cutoff:
            continue
        # Ignore pump rounding/local micro-adjustments. A sector-wide expectation
        # should result in a material movement, not a few kuruş.
        if abs(delta) < 0.50:
            continue
        by_fuel[fuel_key].append((detected, delta, change))

    items = list(data.get('items') or [])
    for item in items:
        fuel_key = item.get('fuel_key')
        if fuel_key not in by_fuel or not by_fuel[fuel_key]:
            continue
        rows = by_fuel[fuel_key]
        latest_time = max(x[0] for x in rows)
        # Reject stale changes and conflicting directions within a scan batch.
        # Same scan batch / same nationwide movement.
        cohort = [x for x in rows if abs((latest_time - x[0]).total_seconds()) <= 1800]
        deltas = [x[1] for x in cohort]
        if any(x > 0 for x in deltas) and any(x < 0 for x in deltas):
            continue
        if not deltas:
            continue
        mid = round(float(median(deltas)), 2)
        if abs(mid) < 0.50:
            continue
        realized_status = 'realized_up' if mid > 0 else 'realized_down'
        label = item.get('fuel') or {'diesel': 'MOTORİN', 'gasoline': 'BENZİN', 'lpg': 'LPG' }[fuel_key]
        movement = 'ZAM GERÇEKLEŞTİ' if mid > 0 else 'İNDİRİM GERÇEKLEŞTİ'
        sample = cohort[0][2]
        item.update({
            'pump_verified': True,
            'status': realized_status,
            'amount': abs(mid),
            'line': f'✅ {label} · {_fmt(abs(mid))} TL {movement}',
            'source': 'Petrol Ofisi pompa fiyat takibi',
            'url': sample.get('source_url'),
            'published_at': latest_time.isoformat(),
            'official': False,
            'label': 'Gerçekleşen fiyat hareketi',
        })

    # Headlines never constitute proof of a realized pump movement.
    # A realized news claim without matching price evidence is unverified.
    for item in items:
        if item.get('status') in ('realized_up', 'realized_down') and not item.get('pump_verified'):
            direction = 'up' if item['status'] == 'realized_up' else 'down'
            item['status'] = direction
            item['pump_verified'] = False
            item['label'] = 'Haber bildirimi · pompa doğrulaması bekleniyor'
            item['line'] = ('🔺 ' if direction == 'up' else '🔻 ') + item.get('fuel', '') + ' · POMPA DOĞRULAMASI BEKLENİYOR'
    data['items'] = items
    data['found'] = any(x.get('status') != 'none' for x in items)
    return data
