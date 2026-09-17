import re
from datetime import datetime, timezone
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from live_scan import scan_now
from scrape import HEADERS, clean, make_item, uid, merge_seen

DARPHANE_UTTS_HOME = 'https://www.darphane.gov.tr/ulusal-tasit-tanima-sistemi'

SEED_UTTS = [
    {
        'url': 'https://www.darphane.gov.tr/duyuru/utts-kapsaminda-yetkili-istasyon-montaj-firmalari-teknik-servis-bedelleri-hakkinda-duyuru',
        'title': 'UTTS Kapsamında Yetkili İstasyon Montaj Firmaları Teknik Servis Bedelleri Hakkında Duyuru',
        'date': '2026-02-03',
        'summary': 'UTTS kapsamında Y-İMF teknik servis, saha müdahalesi ve işçilik hizmetlerine ilişkin uygulama esasları güncellendi.',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/2026-yili-akaryakit-istasyonlari-utts-donanim-montaj-hizmet-bedelleri-hakkinda-duyuru',
        'title': '2026 Yılı Akaryakıt İstasyonları UTTS Donanım Montaj Hizmet Bedelleri Hakkında Duyuru',
        'date': '2026-02-03',
        'summary': '2026 yılı TİM/TTO montajı ve YN Pompa ÖKC entegrasyonuna ilişkin hizmet bedelleri ve uygulama esasları yayımlandı.',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/kamuoyuna-duyuru-15',
        'title': 'Kamuoyuna Duyuru - UTTS',
        'date': '2026-03-25',
        'summary': 'Darphane, UTTS donanımlarının menşei, güvenliği ve ücretlerine ilişkin kamuoyunda yer alan iddialar hakkında açıklama yayımladı.',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/ulusal-tasit-tanima-sistemi-uygulamasina-yonelik-sure-uzatimina-iliskin-duyuru',
        'title': 'Ulusal Taşıt Tanıma Sistemi Uygulamasına Yönelik Süre Uzatımına İlişkin Duyuru',
        'date': '2025-12-26',
        'summary': 'LPG pompalarındaki TTO ve ilgili taşıtlardaki TTB yükümlülükleri için 30 Haziran 2026 tarihine kadar süre verildi.',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/utts-hakkinda-kamuoyuna-duyuru-4',
        'title': 'UTTS Hakkında Kamuoyuna Duyuru',
        'date': '2025-06-20',
        'summary': 'TTB montaj süreleri ve mevcut TTS tabanca okuyucu değişim programına ilişkin Darphane açıklaması.',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/utts-hakkinda-kamuoyuna-duyuru-2',
        'title': 'UTTS Hakkında Kamuoyuna Duyuru',
        'date': '2025-05-09',
        'summary': 'Akaryakıt istasyonlarının UTTS kayıt, sipariş ve kurulum yükümlülüklerine ilişkin son tarihler hatırlatıldı.',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/utts-ucret-duzenlemeleri-hakkinda',
        'title': 'UTTS Ücret Düzenlemeleri Hakkında',
        'date': '2025-03-24',
        'summary': 'UTTS kapsamındaki TTB ücretleri, indirim ve iade uygulamalarına ilişkin düzenlemeler duyuruldu.',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/kamuoyuna-duyuru-13',
        'title': 'UTTS Hakkında Kamuoyuna Duyuru',
        'date': '2025-02-04',
        'summary': 'UTTS donanımları, maliyetleri, güvenliği ve proje uygulamasına ilişkin kamuoyu açıklaması yayımlandı.',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/basin-duyurusu-2',
        'title': 'Kamuoyuna Duyuru - UTTS',
        'date': '2024-12-08',
        'summary': 'UTTS projesi, donanımların menşei ve istasyon/taşıt yükümlülüklerine ilişkin Darphane açıklaması.',
    },
]

# Explicit UTTS phrases and abbreviations. Short abbreviations MUST be matched as
# complete tokens; otherwise words such as "denetim", "eğitim" and "yönetim"
# incorrectly matched the old substring "tim" rule and were labelled UTTS.
UTTS_PHRASES = (
    'utts',
    'ulusal taşıt tanıma',
    'ulusal tasit tanima',
    'taşıt tanıma sistemi',
    'tasit tanima sistemi',
    'yetkili istasyon montaj',
    'tabanca okuyucu',
)
UTTS_TOKENS = ('tto', 'tim', 'ttb', 'tts', 'yimf')

OLD_SOURCE_NAMES = {
    'UTTS',
    'TOBB Sektör Haberleri',
    'Darphane Duyurular',
}
OLD_SOURCE_GROUPS = {'UTTS', 'TOBB'}
OLD_SOURCE_KEYS = {'utts', 'tobb', 'darphane'}
CANONICAL_UTTS_SOURCE_NAME = 'Darphane / UTTS Duyuruları'
CANONICAL_UTTS_GROUP = 'Darphane / UTTS'

DARPHANE_SOURCE = {
    'key': 'darphane_utts',
    'name': CANONICAL_UTTS_SOURCE_NAME,
    'group': CANONICAL_UTTS_GROUP,
    'official': True,
    'url': DARPHANE_UTTS_HOME,
    'market': 'Petrol',
    'record_type': 'UTTS Duyurusu',
}


def _norm(text):
    # Turkish capital İ casefolds to i + combining dot; remove that combining mark
    # so abbreviations such as TİM and GİB can be matched safely as whole tokens.
    return clean(text).casefold().replace('\u0307', '')


def _token(text, value):
    low = _norm(text)
    token = _norm(value)
    return re.search(r'(?<!\w)' + re.escape(token) + r'(?!\w)', low, flags=re.UNICODE) is not None


def _phrase(text, value):
    return _norm(value) in _norm(text)


def _is_utts(text):
    return any(_phrase(text, term) for term in UTTS_PHRASES) or any(_token(text, term) for term in UTTS_TOKENS)


def _content_category(item):
    """Derive the label from the item's own title/summary, never from source name."""
    title = clean(item.get('title') or '')
    summary = clean(item.get('summary') or '')
    text = f'{title} {summary}'
    low = _norm(text)

    # Darphane/UTTS canonical records have already been verified as UTTS content.
    if item.get('source_key') == 'darphane_utts' or item.get('source') == CANONICAL_UTTS_GROUP:
        return 'UTTS'

    if _is_utts(text):
        return 'UTTS'

    if (
        _token(text, 'okc')
        or _token(text, 'ö.k.c')
        or _token(text, 'pos')
        or 'ödeme kaydedici' in low
        or 'odeme kaydedici' in low
        or 'yeni nesil ökc' in low
        or 'yeni nesil okc' in low
    ):
        return 'ÖKC / POS'

    if _token(text, 'lpg') or any(x in low for x in ('otogaz', 'tüplügaz', 'tuplugaz')):
        return 'LPG'

    if _token(text, 'ötv') or _token(text, 'otv') or _token(text, 'gib') or 'vergi' in low:
        return 'Vergi / ÖTV'

    if any(x in low for x in ('lisans', 'denetim', 'ceza', 'idari yaptırım', 'idari yaptirim')):
        return 'Lisans / Denetim'

    if any(x in low for x in ('kurul kararı', 'kurul karari', 'tebliğ', 'teblig', 'yönetmelik', 'yonetmelik', 'kanun', 'mevzuat')):
        return 'Mevzuat'

    # EPDK's dedicated legislation/board-decision feeds are legislation even when
    # their short title does not repeat the word "mevzuat".
    if item.get('record_type') in {'Mevzuat', 'Kurul Kararı'}:
        return 'Mevzuat'

    return 'Akaryakıt'


def _reclassify_items(items):
    fixed = []
    for raw in items or []:
        item = dict(raw)
        item['category'] = _content_category(item)
        fixed.append(item)
    return fixed


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


def _discover_urls():
    query = 'site:darphane.gov.tr/duyuru (UTTS OR "Ulusal Taşıt Tanıma")'
    url = 'https://www.bing.com/search?format=rss&q=' + quote_plus(query)
    discovered = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        for node in root.findall('.//item'):
            link = clean(node.findtext('link') or '')
            title = clean(node.findtext('title') or '')
            if 'darphane.gov.tr/duyuru/' not in link:
                continue
            if not _is_utts(title):
                continue
            discovered.append({'url': link, 'title': title, 'date': None, 'summary': ''})
    except Exception:
        pass
    return discovered


def _official_item(seed, existing, now):
    url = seed['url']
    title = seed.get('title') or 'Darphane UTTS Duyurusu'
    date = seed.get('date')
    summary = seed.get('summary') or ''
    reachable = False

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        reachable = True
        soup = BeautifulSoup(r.text, 'html.parser')
        article = soup.find('main') or soup.find('article') or soup
        body = clean(article.get_text(' ', strip=True))
        if _is_utts(body):
            heading = article.find(['h1', 'h2', 'h3'])
            page_title = clean(heading.get_text(' ', strip=True)) if heading else ''
            if page_title and len(page_title) > 7:
                title = page_title
            if body:
                summary = body[:700]
    except Exception:
        pass

    context = ' '.join(x for x in [date or '', summary, title] if x)
    item = make_item(DARPHANE_SOURCE, title, url, context)
    if date and not item.get('date'):
        item['date'] = date
    item['source'] = CANONICAL_UTTS_GROUP
    item['source_name'] = CANONICAL_UTTS_SOURCE_NAME
    item['source_key'] = 'darphane_utts'
    item['official'] = True
    item['category'] = 'UTTS'
    item['market'] = 'Petrol'
    item['record_type'] = 'UTTS Duyurusu'
    item['source_url'] = DARPHANE_UTTS_HOME

    item_id = uid(item)
    previous = existing.get(item_id)
    merged = merge_seen(item, previous, now)
    if not previous:
        merged['first_seen'] = _baseline_seen(item)
    return merged, reachable


def fetch_darphane_utts(existing):
    now = datetime.now(timezone.utc).isoformat()
    candidates = {}
    for seed in SEED_UTTS + _discover_urls():
        candidates[seed['url']] = seed

    found = {}
    reachable_count = 0
    for seed in candidates.values():
        item, reachable = _official_item(seed, existing, now)
        found[item['id']] = item
        if reachable:
            reachable_count += 1

    status = {
        'source': CANONICAL_UTTS_GROUP,
        'source_name': CANONICAL_UTTS_SOURCE_NAME,
        'ok': reachable_count > 0,
        'count': len(found),
        'checked_at': now,
        'official_urls_reached': reachable_count,
        'official_urls_total': len(candidates),
        'note': 'UTTS için yalnızca Darphane resmi duyuruları kabul edilir; yeni duyurular alan adı arama indeksiyle keşfedilip resmi URL üzerinden doğrulanır.',
    }
    if reachable_count == 0:
        status['error'] = 'Darphane resmi UTTS duyuru sayfalarına erişilemedi'

    return list(found.values()), status


def normalize_sector_data(data):
    # Reclassify every existing and freshly scanned record from its own content.
    # This also repairs incorrect historical UTTS labels already stored in data.json.
    items = [i for i in (data.get('items') or []) if not _is_old_item(i)]
    items = _reclassify_items(items)
    existing = {i.get('id'): i for i in items if i.get('id')}

    darphane_items, darphane_status = fetch_darphane_utts(existing)
    merged = {i.get('id'): i for i in items if i.get('id')}
    for item in darphane_items:
        merged[item['id']] = item

    items = _reclassify_items(list(merged.values()))
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
        if status.get('source_name') == CANONICAL_UTTS_SOURCE_NAME:
            continue
        if status.get('source') == CANONICAL_UTTS_GROUP:
            continue
        statuses.append(status)
    statuses.append(darphane_status)

    out = dict(data)
    out['items'] = items[:900]
    out['sources'] = statuses
    out['version'] = max(int(data.get('version') or 0), 11)
    return out


def scan_sector_now():
    return normalize_sector_data(scan_now())
