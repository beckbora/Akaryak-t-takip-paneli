import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
PRICE_DATA = ROOT / 'prices.json'

PO_URL = 'https://www.petrolofisi.com.tr/akaryakit-fiyatlari'
PO_EXTRA = [
    {
        'location_key': 'SAKARYA',
        'location': 'Sakarya (Adapazarı)',
        'url': 'https://www.petrolofisi.com.tr/akaryakit-fiyatlari/sakarya-akaryakit-fiyatlari',
        'row_keys': ['ADAPAZARI'],
    },
    {
        'location_key': 'TEKIRDAG',
        'location': 'Tekirdağ',
        'url': 'https://www.petrolofisi.com.tr/akaryakit-fiyatlari/tekirdag-akaryakit-fiyatlari',
        'row_keys': ['TEKIRDAG', 'SULEYMANPASA'],
    },
]

EPDK_PETROL_URL = 'https://apigateway.epdk.gov.tr/petrolBayiSatisFiyatBulten'
EPDK_LPG_URL = 'https://apigateway.epdk.gov.tr/lpgBayiSatisFiyatBultenGunluk'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.6,en;q=0.4',
}

FOCUS_LOCATIONS = [
    'ISTANBUL (ANADOLU)',
    'ISTANBUL (AVRUPA)',
    'ANKARA',
    'IZMIR',
    'SAKARYA',
    'TEKIRDAG',
]
DISPLAY_NAMES = {
    'ISTANBUL (ANADOLU)': 'İstanbul Anadolu',
    'ISTANBUL (AVRUPA)': 'İstanbul Avrupa',
    'ANKARA': 'Ankara',
    'IZMIR': 'İzmir',
    'SAKARYA': 'Sakarya (Adapazarı)',
    'TEKIRDAG': 'Tekirdağ',
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
    return {
        'current': [], 'changes': [], 'history': [], 'epdk_history': [],
        'epdk_year_history': [], 'annual_trends': {}, 'sources': []
    }


def first_number(text):
    m = re.search(r'(?<!\d)(\d{1,3}[.,]\d{1,4})(?!\d)', text or '')
    if not m:
        return None
    try:
        return float(m.group(1).replace(',', '.'))
    except Exception:
        return None


def parse_po_table(html, wanted_keys, location_key=None, location_name=None, source_url=PO_URL):
    soup = BeautifulSoup(html, 'html.parser')
    wanted = {x.upper() for x in wanted_keys}
    for tr in soup.find_all('tr'):
        cells = [c.get_text(' ', strip=True) for c in tr.find_all(['th', 'td'])]
        if len(cells) < 7:
            continue
        row_key = re.sub(r'\s+', ' ', cells[0]).strip().upper()
        if row_key not in wanted:
            continue
        values = [first_number(c) for c in cells[1:]]
        if len(values) < 6 or values[0] is None or values[1] is None or values[5] is None:
            continue
        key = location_key or row_key
        return {
            'location': location_name or DISPLAY_NAMES.get(key, key.title()),
            'location_key': key,
            'gasoline': round(values[0], 2),
            'diesel': round(values[1], 2),
            'lpg': round(values[5], 2),
            'currency': 'TL',
            'unit': 'L',
            'source': 'Petrol Ofisi',
            'source_url': source_url,
            'checked_at': now_iso(),
        }
    return None


def fetch_po_prices():
    checked = now_iso()
    rows = []
    errors = []

    try:
        r = requests.get(PO_URL, headers=HEADERS, timeout=9)
        r.raise_for_status()
        for key in FOCUS_LOCATIONS[:4]:
            row = parse_po_table(r.text, [key], location_key=key, location_name=DISPLAY_NAMES[key], source_url=PO_URL)
            if row:
                row['checked_at'] = checked
                rows.append(row)
    except Exception as exc:
        errors.append(f'ana sayfa: {str(exc)[:120]}')

    for extra in PO_EXTRA:
        try:
            r = requests.get(extra['url'], headers=HEADERS, timeout=9)
            r.raise_for_status()
            row = parse_po_table(
                r.text,
                extra['row_keys'],
                location_key=extra['location_key'],
                location_name=extra['location'],
                source_url=extra['url'],
            )
            if row:
                row['checked_at'] = checked
                rows.append(row)
            else:
                errors.append(f"{extra['location']}: fiyat satırı ayrıştırılamadı")
        except Exception as exc:
            errors.append(f"{extra['location']}: {str(exc)[:120]}")

    unique = {x['location_key']: x for x in rows}
    rows = [unique[k] for k in FOCUS_LOCATIONS if k in unique]
    status = {
        'source': 'Petrol Ofisi güncel pompa fiyatları',
        'ok': bool(rows),
        'count': len(rows),
        'checked_at': checked,
    }
    if errors:
        status['note'] = ' | '.join(errors)[:240]
    if not rows:
        status['error'] = status.pop('note', 'Petrol Ofisi fiyatları alınamadı')
    return rows, status


def fetch_epdk_day(endpoint, day, market):
    date_str = day.strftime('%Y-%m-%d')
    try:
        r = requests.get(
            endpoint,
            headers={**HEADERS, 'Accept': 'application/json', 'Content-Type': 'application/json'},
            json={'raporTarihi': date_str},
            timeout=7,
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


def fetch_epdk_history(saved_history, days=2):
    today = datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=i) for i in range(days)]
    found = []
    errors = []
    jobs = []
    with ThreadPoolExecutor(max_workers=min(10, max(2, days * 2))) as pool:
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
    cutoff = (today - timedelta(days=400)).isoformat()
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


def month_back(base, months):
    total = base.year * 12 + (base.month - 1) - months
    y, m0 = divmod(total, 12)
    m = m0 + 1
    # The 15th avoids month-end invalid dates and usually has a bulletin.
    return date(y, m, min(base.day, 15))


def fetch_epdk_nearest_petrol(target_day):
    offsets = [0, -1, 1, -2, 2, -3, 3]
    errors = []
    for off in offsets:
        rows, err = fetch_epdk_day(EPDK_PETROL_URL, target_day + timedelta(days=off), 'Petrol')
        if rows:
            return rows, None
        if err:
            errors.append(err)
    return [], errors[0] if errors else 'kayıt bulunamadı'


def fetch_epdk_year_history(saved_history):
    today = datetime.now(timezone.utc).date()
    targets = [month_back(today, i) for i in range(12, -1, -1)]
    found = []
    errors = []
    with ThreadPoolExecutor(max_workers=7) as pool:
        jobs = {pool.submit(fetch_epdk_nearest_petrol, d): d for d in targets}
        for job in as_completed(jobs):
            rows, err = job.result()
            found.extend(rows)
            if err:
                errors.append(err)

    merged = {}
    for row in (saved_history or []) + found:
        if row.get('market') != 'Petrol':
            continue
        key = (row.get('date'), row.get('fuel'))
        if all(key):
            merged[key] = row
    history = list(merged.values())
    cutoff = (today - timedelta(days=400)).isoformat()
    history = [x for x in history if x.get('date', '') >= cutoff]
    history.sort(key=lambda x: (x.get('date', ''), x.get('fuel', '')))
    status = {
        'source': 'EPDK 1 yıllık Benzin & Motorin trendi',
        'ok': bool(found) or bool(saved_history),
        'count': len(found),
        'checked_at': now_iso(),
    }
    if not found and errors:
        status['error'] = errors[0]
    return history, status


def normalize_fuel(value):
    return re.sub(r'\s+', ' ', (value or '').casefold()).strip()


def trend_series(history, kind):
    by_date = {}
    for row in history or []:
        fuel = normalize_fuel(row.get('fuel'))
        exact = False
        fallback = False
        if kind == 'gasoline':
            exact = fuel in {'kurşunsuz benzin 95 oktan', 'kursunsuz benzin 95 oktan'}
            fallback = ('benzin' in fuel and '95' in fuel and 'diğer' not in fuel and 'diger' not in fuel)
        elif kind == 'diesel':
            exact = fuel == 'motorin'
            fallback = fuel.startswith('motorin') and 'diğer' not in fuel and 'diger' not in fuel
        if not (exact or fallback):
            continue
        d = row.get('date')
        if not d:
            continue
        candidate = {'date': d, 'price': float(row.get('price', 0)), 'exact': exact}
        old = by_date.get(d)
        if old is None or (candidate['exact'] and not old['exact']):
            by_date[d] = candidate
    return [{'date': d, 'price': round(v['price'], 5)} for d, v in sorted(by_date.items())]


def annual_trend_summary(history):
    out = {}
    for kind, label in [('gasoline', 'Benzin 95'), ('diesel', 'Motorin')]:
        series = trend_series(history, kind)
        if len(series) < 2:
            out[kind] = {'label': label, 'points': series}
            continue
        first, last = series[0], series[-1]
        delta = round(last['price'] - first['price'], 2)
        pct = round((delta / first['price']) * 100, 2) if first['price'] else None
        out[kind] = {
            'label': label,
            'start_date': first['date'],
            'start_price': round(first['price'], 2),
            'end_date': last['date'],
            'end_price': round(last['price'], 2),
            'delta': delta,
            'percent': pct,
            'direction': 'zam' if delta > 0 else ('indirim' if delta < 0 else 'yatay'),
            'points': series,
        }
    return out


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
    return out[:400]


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
            last = next((x for x in reversed(history) if x.get('location_key') == rec['location_key'] and x.get('fuel') == label), None)
            if not last or last.get('date') != day or abs(float(last.get('price', 0)) - float(rec['price'])) >= 0.01:
                history.append(rec)
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=400)).isoformat()
    history = [x for x in history if x.get('date', '') >= cutoff]
    return history[-5000:]


def scan_prices(history_days=2, include_year=True):
    saved = read_saved()
    detected_at = now_iso()
    current, po_status = fetch_po_prices()
    if not current:
        current = saved.get('current') or []

    live_changes = build_changes(saved.get('current') or [], current, detected_at)
    changes = merge_changes(saved.get('changes') or [], live_changes)
    history = merge_history(saved.get('history') or [], current, detected_at) if current else (saved.get('history') or [])
    epdk_history, epdk_status = fetch_epdk_history(saved.get('epdk_history') or [], days=history_days)

    year_history = saved.get('epdk_year_history') or []
    year_status = {
        'source': 'EPDK 1 yıllık Benzin & Motorin trendi',
        'ok': bool(year_history),
        'count': len(year_history),
        'checked_at': detected_at,
    }
    if include_year:
        year_history, year_status = fetch_epdk_year_history(year_history)
    annual_trends = annual_trend_summary(year_history)

    return {
        'updated_at': detected_at,
        'current': current,
        'live_changes': live_changes,
        'changes': changes,
        'history': history,
        'epdk_history': epdk_history,
        'epdk_year_history': year_history,
        'annual_trends': annual_trends,
        'sources': [po_status, epdk_status, year_status],
        'version': 2,
    }


def write_prices():
    data = scan_prices(history_days=14, include_year=True)
    PRICE_DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(
        f"Wrote {len(data.get('current', []))} current city rows, "
        f"{len(data.get('changes', []))} price changes and annual trends"
    )
    return data


if __name__ == '__main__':
    write_prices()
