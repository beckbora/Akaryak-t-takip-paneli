from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from live_scan import scan_now
from scrape import HEADERS, clean, make_item, uid, merge_seen

DARPHANE_UTTS_URLS = [
    'https://www.darphane.gov.tr/kategori/genel',
    'https://www.darphane.gov.tr/kategori/genel/sayfa/2',
    'https://www.darphane.gov.tr/kategori/genel/sayfa/3',
    'https://www.darphane.gov.tr/kategori/genel/sayfa/4',
    'https://www.darphane.gov.tr/kategori/genel/sayfa/5',
]

UTTS_TERMS = (
    'utts',
    'ulusal taşıt tanıma',
    'ulusal tasit tanima',
    'taşıt tanıma',
    'tasit tanima',
    'tto',
    'tim',
    'ttb',
    'y-imf',
    'yimf',
    'yetkili istasyon montaj',
    'tabanca okuyucu',
)

OLD_SOURCE_NAMES = {
    'UTTS',
    'TOBB Sektör Haberleri',
    'Darphane Duyurular',
}
OLD_SOURCE_GROUPS = {'UTTS', 'TOBB'}
OLD_SOURCE_KEYS = {'utts', 'tobb', 'darphane'}

DARPHANE_SOURCE = {
    'key': 'darphane_utts',
    'name': 'Darphane / UTTS Duyuruları',
    'group': 'Darphane / UTTS',
    'official': True,
    'url': DARPHANE_UTTS_URLS[0],
    'market': 'Petrol',
    'record_type': 'UTTS Duyurusu',
}


def _norm(text):
    return clean(text).casefold()


def _is_utts(text):
    low = _norm(text)
    return any(term in low for term in UTTS_TERMS)


def _is_old_item(item):
    return (
        item.get('source_name') in OLD_SOURCE_NAMES
        or item.get('source') in OLD_SOURCE_GROUPS
        or item.get('source_key') in OLD_SOURCE_KEYS
    )


def _baseline_seen(item):
    if item.get('date'):
        return f"{item['date']}T12:00:00+00:00"
    return '2000-01-01T00:00:00+00:00'


def fetch_darphane_utts(existing):
    now = datetime.now(timezone.utc).isoformat()
    found = {}
    reached = 0
    errors = []

    for page_url in DARPHANE_UTTS_URLS:
        try:
            r = requests.get(page_url, headers=HEADERS, timeout=10)
            r.raise_for_status()
            reached += 1
            soup = BeautifulSoup(r.text, 'html.parser')

            for a in soup.find_all('a', href=True):
                title = clean(a.get_text(' ', strip=True))
                if len(title) < 8 or not _is_utts(title):
                    continue

                href = urljoin(page_url, a.get('href', ''))
                if 'darphane.gov.tr/duyuru/' not in href:
                    continue

                parent = a.find_parent(['article', 'li', 'div']) or a.parent
                context = clean(parent.get_text(' ', strip=True) if parent else title)
                item = make_item(DARPHANE_SOURCE, title, href, context)
                item['source'] = 'Darphane / UTTS'
                item['source_name'] = 'Darphane / UTTS Duyuruları'
                item['source_key'] = 'darphane_utts'
                item['official'] = True
                item['category'] = 'UTTS'
                item['market'] = 'Petrol'
                item['record_type'] = 'UTTS Duyurusu'
                item['source_url'] = DARPHANE_UTTS_URLS[0]

                item_id = uid(item)
                previous = existing.get(item_id)
                merged = merge_seen(item, previous, now)
                if not previous:
                    merged['first_seen'] = _baseline_seen(item)
                found[item_id] = merged
        except Exception as exc:
            errors.append(str(exc)[:160])

    status = {
        'source': 'Darphane / UTTS',
        'source_name': 'Darphane / UTTS Duyuruları',
        'ok': reached > 0,
        'count': len(found),
        'checked_at': now,
        'pages_reached': reached,
        'pages_total': len(DARPHANE_UTTS_URLS),
    }
    if reached == 0:
        status['error'] = errors[0] if errors else 'Darphane duyuru arşivine erişilemedi'
    elif reached < len(DARPHANE_UTTS_URLS):
        status['note'] = f'{reached}/{len(DARPHANE_UTTS_URLS)} Darphane arşiv sayfasına erişildi'

    return list(found.values()), status


def normalize_sector_data(data):
    items = [i for i in (data.get('items') or []) if not _is_old_item(i)]
    existing = {i.get('id'): i for i in items if i.get('id')}

    darphane_items, darphane_status = fetch_darphane_utts(existing)
    merged = {i.get('id'): i for i in items if i.get('id')}
    for item in darphane_items:
        merged[item['id']] = item

    items = list(merged.values())
    items.sort(
        key=lambda x: (
            x.get('date') or '0000-00-00',
            x.get('changed_at') or x.get('first_seen') or '',
        ),
        reverse=True,
    )

    statuses = []
    for status in data.get('sources') or []:
        if status.get('source_name') in OLD_SOURCE_NAMES:
            continue
        if status.get('source') in OLD_SOURCE_GROUPS:
            continue
        statuses.append(status)
    statuses.append(darphane_status)

    out = dict(data)
    out['items'] = items[:900]
    out['sources'] = statuses
    out['version'] = max(int(data.get('version') or 0), 8)
    return out


def scan_sector_now():
    return normalize_sector_data(scan_now())
