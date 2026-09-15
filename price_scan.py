import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
PRICE_DATA = ROOT / 'prices.json'

PO_URL = 'https://www.petrolofisi.com.tr/akaryakit-fiyatlari'
EPDK_PETROL_URL = 'https://apigateway.epdk.gov.tr/petrolBayiSatisFiyatBulten'
EPDK_LPG_URL = 'https://apigateway.epdk.gov.tr/lpgBayiSatisFiyatBultenGunluk'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.6,en;q=0.4',
}

FOCUS_LOCATIONS = ['ISTANBUL (ANADOLU)', 'ISTANBUL (AVRUPA)', 'ANKARA', 'IZMIR']
DISPLAY_NAMES = {
    'ISTANBUL (ANADOLU)': 'İstanbul Anadolu',
    'ISTANBUL (AVRUPA)': 'İstanbul Avrupa',
    'ANKARA': 'Ankara',
    'IZMIR': 'İzmir',
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_saved():
    try:
        data = json.loads(PRICE_DATA.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {'current': [], 'changes': [], 'history': [], 'epdk_history': [], 'sources': []}


def first_number(text):
    m = re.search(r'(?<!\d)(\d{1,3}[.,]\d{1,4})(?!\d)', text or '')
    if not m:
        return None
    try:
        return float(m.group(1).replace(',', '.'))
    except Exception:
        return None


def fetch_po_prices():
    checked = now_iso()
    try:
        r = requests.get(PO_URL, headers=HEADERS, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        rows = []
        seen = set()
        for tr in soup.find_all('tr'):
            cells = [c.get_text(' ', strip=True) for c in tr.find_all(['th', 'td'])]
            if len(cells) < 4:
                continue
            loc = re.sub(r'\s+', ' ', cells[0]).strip().upper()
            if loc not in FOCUS_LOCATIONS or loc in seen:
                continue
            values = [first_number(c) for c in cells[1:]]
            # Petrol Ofisi table order: Benzin 95, Diesel, Gazyağı, Kalorifer, Fuel Oil, Otogaz
            if len(values) < 6 or values[0] is None or values[1] is None or values[5] is None:
                continue
            seen.add(loc)
            rows.append({
                'location': DISPLAY_NAMES.get(loc, loc.title()),
                'location_key': loc,
                'gasoline': round(values[0], 2),
                'diesel': round(values[1], 2),
                'lpg': round(values[5], 2),
                'currency': 'TL',
                'unit': 'L',
                'source': 'Petrol Ofisi',
                'source_url': PO_URL,
                'checked_at': checked,
            })
        if not rows:
            raise RuntimeError('Petrol Ofisi fiyat tablosu ayrıştırılamadı')
        rows.sort(key=lambda x: FOCUS_LOCATIONS.index(x['location_key']))
        return rows, {'source': 'Petrol Ofisi güncel pompa fiyatları', 'ok': True, 'count': len(rows), 'checked_at': checked}
    except Exception as exc:
        return [], {'source': 'Petrol Ofisi güncel pompa fiyatları', 'ok': False, 'count': 0, 'checked_at': checked, 'error': str(exc)[:180]}


def fetch_epdk_day(endpoint, day, market):
    date_str = day.strftime('%Y-%m-%d')
    try:
        r = requests.get(
            endpoint,
            headers={**HEADERS, 'Accept': 'application/json', 'Content-Type': 'application/json'},
            json={'raporTarihi': date_str},
            timeout=10,
        )
        r.raise_for_status()
        payload = r.json()
        data = payload.get('data') or []
        out = []
        for row in data:
            fuel = str(row.get('Yakıt') or row.get('Yakit') or '').strip()
            price = row.get('Fiyat')
            if not fuel or price is None:
                continue
            try:
                price = float(str(price).replace(',', '.'))
            except Exception:
                continue
            out.append({
                'date': str(row.get('Tarih') or date_str)[:10],
                'fuel': fuel,
                'price': round(price, 5),
                'unit': str(row.get('Ölçü Birimi') or row.get('Olcu Birimi') or ''),
                'market': market,
                'source': 'EPDK',
            })
        return out, None
    except Exception as exc:
        return [], str(exc)[:160]


def fetch_epdk_history(saved_history, days=14):
    today = datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=i) for i in range(days)]
    found = []
    errors = []
    jobs = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        for day in dates:
            jobs.append(pool.submit(fetch_epdk_day, EPDK_PETROL_URL, day, 'Petrol'))
            jobs.append(pool.submit(fetch_epdk_day, EPDK_LPG_URL, day, 'LPG'))
        for job in as_completed(jobs):
            rows, err = job.result()
            found.extend(rows)
            if err:
                errors.append(err)

    merged = {}
    for row in (saved_history or []) + found:
        key = (row.get('date'), row.get('market'), row.get('fuel'))
        if all(key):
            merged[key] = row
    history = list(merged.values())
    history.sort(key=lambda x: (x.get('date', ''), x.get('market', ''), x.get('fuel', '')))
    # Keep roughly six months of official history if it accumulates.
    cutoff = (today - timedelta(days=190)).isoformat()
    history = [x for x in history if x.get('date', '') >= cutoff]
    status = {
        'source': 'EPDK günlük Petrol & LPG fiyat bültenleri',
        'ok': bool(found) or bool(saved_history),
        'count': len(found),
        'checked_at': now_iso(),
    }
    if not found and errors:
        status['error'] = errors[0]
    return history, status


def current_map(rows):
    return {x.get('location_key'): x for x in rows if x.get('location_key')}


def build_changes(previous, current, detected_at):
    old = current_map(previous)
    changes = []
    for row in current:
        prev = old.get(row['location_key'])
        if not prev:
            continue
        for field, label in [('gasoline', 'Benzin 95'), ('diesel', 'Motorin'), ('lpg', 'LPG/Otogaz')]:
            before = prev.get(field)
            after = row.get(field)
            if before is None or after is None:
                continue
            delta = round(float(after) - float(before), 2)
            if abs(delta) < 0.01:
                continue
            changes.append({
                'detected_at': detected_at,
                'location': row['location'],
                'location_key': row['location_key'],
                'fuel': label,
                'old_price': round(float(before), 2),
                'new_price': round(float(after), 2),
                'delta': delta,
                'direction': 'zam' if delta > 0 else 'indirim',
                'percent': round((delta / float(before)) * 100, 2) if float(before) else None,
                'source': row.get('source', 'Petrol Ofisi'),
                'source_url': row.get('source_url', PO_URL),
            })
    return changes


def merge_changes(saved_changes, new_changes):
    merged = {}
    for c in (saved_changes or []) + (new_changes or []):
        key = (
            c.get('detected_at', '')[:13], c.get('location_key'), c.get('fuel'),
            c.get('old_price'), c.get('new_price')
        )
        merged[key] = c
    out = list(merged.values())
    out.sort(key=lambda x: x.get('detected_at', ''), reverse=True)
    return out[:250]


def merge_history(saved_history, current, detected_at):
    history = list(saved_history or [])
    day = detected_at[:10]
    for row in current:
        for field, label in [('gasoline', 'Benzin 95'), ('diesel', 'Motorin'), ('lpg', 'LPG/Otogaz')]:
            rec = {
                'date': day,
                'timestamp': detected_at,
                'location': row['location'],
                'location_key': row['location_key'],
                'fuel': label,
                'price': row[field],
                'source': row.get('source', 'Petrol Ofisi'),
            }
            # One observation per location/fuel/day unless the price changes during the day.
            last = next((x for x in reversed(history) if x.get('location_key') == rec['location_key'] and x.get('fuel') == label), None)
            if not last or last.get('date') != day or abs(float(last.get('price', 0)) - float(rec['price'])) >= 0.01:
                history.append(rec)
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=190)).isoformat()
    history = [x for x in history if x.get('date', '') >= cutoff]
    return history[-2000:]


def scan_prices():
    saved = read_saved()
    detected_at = now_iso()
    current, po_status = fetch_po_prices()
    if not current:
        current = saved.get('current') or []

    live_changes = build_changes(saved.get('current') or [], current, detected_at)
    changes = merge_changes(saved.get('changes') or [], live_changes)
    history = merge_history(saved.get('history') or [], current, detected_at) if current else (saved.get('history') or [])
    epdk_history, epdk_status = fetch_epdk_history(saved.get('epdk_history') or [], days=14)

    return {
        'updated_at': detected_at,
        'current': current,
        'live_changes': live_changes,
        'changes': changes,
        'history': history,
        'epdk_history': epdk_history,
        'sources': [po_status, epdk_status],
        'version': 1,
    }


def write_prices():
    data = scan_prices()
    PRICE_DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"Wrote {len(data.get('current', []))} current city rows and {len(data.get('changes', []))} price changes")
    return data


if __name__ == '__main__':
    write_prices()
