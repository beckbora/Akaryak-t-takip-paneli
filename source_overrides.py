import re
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlsplit
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from live_scan import scan_now as raw_scan_now
from scrape import HEADERS, clean, make_item, uid, merge_seen, parse_date, severity

DARPHANE_UTTS_HOME = 'https://www.darphane.gov.tr/ulusal-tasit-tanima-sistemi'
UTTS_PORTAL_HOME = 'https://www.utts.gov.tr/'
UTTS_PORTAL_ALT_HOME = 'https://utts.gov.tr/'

SEED_UTTS = [
    {
        'url': 'https://www.darphane.gov.tr/duyuru/utts-kapsaminda-yetkili-istasyon-montaj-firmalari-teknik-servis-bedelleri-hakkinda-duyuru',
        'title': 'UTTS Kapsamında Yetkili İstasyon Montaj Firmaları Teknik Servis Bedelleri Hakkında Duyuru',
        'date': '2026-02-03',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/2026-yili-akaryakit-istasyonlari-utts-donanim-montaj-hizmet-bedelleri-hakkinda-duyuru',
        'title': '2026 Yılı Akaryakıt İstasyonları UTTS Donanım Montaj Hizmet Bedelleri Hakkında Duyuru',
        'date': '2026-02-03',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/kamuoyuna-duyuru-15',
        'title': 'Kamuoyuna Duyuru - UTTS',
        'date': '2026-03-25',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/ulusal-tasit-tanima-sistemi-uygulamasina-yonelik-sure-uzatimina-iliskin-duyuru',
        'title': 'Ulusal Taşıt Tanıma Sistemi Uygulamasına Yönelik Süre Uzatımına İlişkin Duyuru',
        'date': '2025-12-26',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/utts-hakkinda-kamuoyuna-duyuru-4',
        'title': 'UTTS Hakkında Kamuoyuna Duyuru',
        'date': '2025-06-20',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/utts-hakkinda-kamuoyuna-duyuru-2',
        'title': 'UTTS Hakkında Kamuoyuna Duyuru',
        'date': '2025-05-09',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/utts-ucret-duzenlemeleri-hakkinda',
        'title': 'UTTS Ücret Düzenlemeleri Hakkında',
        'date': '2025-03-24',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/kamuoyuna-duyuru-13',
        'title': 'UTTS Hakkında Kamuoyuna Duyuru',
        'date': '2025-02-04',
    },
    {
        'url': 'https://www.darphane.gov.tr/duyuru/basin-duyurusu-2',
        'title': 'Kamuoyuna Duyuru - UTTS',
        'date': '2024-12-08',
    },
]

UTTS_STRONG_PHRASES = (
    'ulusal taşıt tanıma',
    'ulusal tasit tanima',
    'taşıt tanıma sistemi',
    'tasit tanima sistemi',
    'taşıt tanıma okuyucu',
    'tasit tanima okuyucu',
    'taşıt tanıma birimi',
    'tasit tanima birimi',
    'yetkili istasyon montaj',
    'tabanca okuyucu',
)
UTTS_AMBIGUOUS_TOKENS = ('tto', 'tim', 'ttb', 'tts', 'yimf')
UTTS_CONTEXT_TERMS = (
    'akaryakıt', 'akaryakit', 'akaryakıt istasyonu', 'akaryakit istasyonu',
    'istasyon montaj', 'yakıt pompası', 'yakit pompasi', 'pompa ökc',
    'pompa okc', 'tabanca okuyucu', 'taşıt tanıma', 'tasit tanima',
    'darphane', 'yn ökc', 'yn okc', 'y-imf', 'yetkili istasyon montaj',
)

SECTOR_TITLE_PHRASES = (
    'akaryakıt', 'akaryakit', 'akaryakıt istasyonu', 'akaryakit istasyonu',
    'petrol piyasası', 'petrol piyasasi', 'petrol ürünleri', 'petrol urunleri',
    'benzin', 'motorin', 'otogaz', 'pompa ökc', 'pompa okc', 'akaryakıt pompa',
    'akaryakit pompa', 'yn ökc', 'yn okc', 'yeni nesil ödeme kaydedici',
    'ulusal marker', 'zorunlu petrol stoku', 'rafineri', 'dağıtıcılar arası',
    'dagiticilar arasi', 'bayilik lisansı', 'bayilik lisansi',
)

OLD_SOURCE_NAMES = {'UTTS', 'TOBB Sektör Haberleri', 'Darphane Duyurular'}
OLD_SOURCE_GROUPS = {'UTTS', 'TOBB'}
OLD_SOURCE_KEYS = {'utts', 'tobb', 'darphane'}

CANONICAL_UTTS_SOURCE_NAME = 'Darphane / UTTS Duyuruları'
CANONICAL_UTTS_GROUP = 'Darphane / UTTS'
UTTS_PORTAL_SOURCE_NAME = 'UTTS Portalı (utts.gov.tr)'
UTTS_PORTAL_GROUP = 'UTTS Portalı'

DARPHANE_SOURCE = {
    'key': 'darphane_utts',
    'name': CANONICAL_UTTS_SOURCE_NAME,
    'group': CANONICAL_UTTS_GROUP,
    'official': True,
    'url': DARPHANE_UTTS_HOME,
    'market': 'Petrol',
    'record_type': 'UTTS Duyurusu',
}

UTTS_PORTAL_SOURCE = {
    'key': 'utts_portal',
    'name': UTTS_PORTAL_SOURCE_NAME,
    'group': UTTS_PORTAL_GROUP,
    'official': True,
    'url': UTTS_PORTAL_HOME,
    'market': 'Petrol',
    'record_type': 'UTTS Duyurusu',
}


def _norm(text):
    return clean(text).casefold().replace('\u0307', '')


def _token(text, value):
    low = _norm(text)
    token = _norm(value)
    return re.search(r'(?<!\w)' + re.escape(token) + r'(?!\w)', low, flags=re.UNICODE) is not None


def _phrase(text, value):
    return _norm(value) in _norm(text)


def _is_utts(text):
    if _token(text, 'utts'):
        return True
    if any(_phrase(text, term) for term in UTTS_STRONG_PHRASES):
        return True
    if not any(_token(text, term) for term in UTTS_AMBIGUOUS_TOKENS):
        return False
    low = _norm(text)
    return any(_norm(term) in low for term in UTTS_CONTEXT_TERMS)


def _sector_signal(title):
    if _is_utts(title):
        return True
    low = _norm(title)
    if _token(title, 'lpg') or _token(title, 'epdk'):
        return True
    return any(_norm(term) in low for term in SECTOR_TITLE_PHRASES)


def _source_host(url):
    try:
        return urlsplit(url or '').netloc.lower().removeprefix('www.')
    except Exception:
        return ''


def _is_utts_domain(url):
    host = _source_host(url)
    return host == 'utts.gov.tr' or host.endswith('.utts.gov.tr')


def _direct_text(item):
    title = clean(item.get('title') or '')
    excerpt = clean(item.get('source_excerpt') or '')
    return clean(f'{title} {excerpt}')


def _content_category(item):
    text = _direct_text(item)
    low = _norm(text)

    if item.get('source_key') in {'darphane_utts', 'utts_portal'}:
        return 'UTTS'
    if item.get('source') in {CANONICAL_UTTS_GROUP, UTTS_PORTAL_GROUP}:
        return 'UTTS'
    if _is_utts(text):
        return 'UTTS'

    if (
        _token(text, 'okc') or _token(text, 'ö.k.c') or _token(text, 'pos')
        or 'ödeme kaydedici' in low or 'odeme kaydedici' in low
        or 'yeni nesil ökc' in low or 'yeni nesil okc' in low
        or 'mali cihaz' in low
    ):
        return 'ÖKC / POS'

    if _token(text, 'lpg') or any(x in low for x in ('otogaz', 'tüplügaz', 'tuplugaz')):
        return 'LPG'

    if _token(text, 'ötv') or _token(text, 'otv') or 'vergi' in low:
        return 'Vergi / ÖTV'

    if any(x in low for x in ('lisans', 'denetim', 'ceza', 'idari yaptırım', 'idari yaptirim')):
        return 'Lisans / Denetim'

    if any(x in low for x in ('kurul kararı', 'kurul karari', 'tebliğ', 'teblig', 'yönetmelik', 'yonetmelik', 'kanun', 'mevzuat')):
        return 'Mevzuat'

    if item.get('record_type') == 'Denetim':
        return 'Lisans / Denetim'
    if item.get('record_type') in {'Mevzuat', 'Kurul Kararı'}:
        return 'Mevzuat'
    if item.get('market') == 'LPG':
        return 'LPG'
    return 'Akaryakıt'


def _is_sector_item(item):
    if item.get('source_key') in {'darphane_utts', 'utts_portal'}:
        return True
    if item.get('source') in {CANONICAL_UTTS_GROUP, UTTS_PORTAL_GROUP}:
        return True
    if item.get('epdk_focus'):
        return True
    if item.get('source') in {'PÜİS', 'TABGİS', 'PETDER', 'LPG Derneği'}:
        return True

    if item.get('source_key') in {'epdk', 'gib'} or item.get('source') == 'Resmî Gazete':
        return _sector_signal(item.get('title') or '')

    return _sector_signal(item.get('title') or '')


def _sanitize_item(raw):
    item = dict(raw)
    title = clean(item.get('title') or '')

    if item.get('source_key') == 'gib':
        title_date = parse_date(title)
        if title_date:
            item['date'] = title_date

    excerpt = clean(item.get('source_excerpt') or '')
    if item.get('description_origin') != 'official_source':
        excerpt = ''
    item['source_excerpt'] = excerpt
    item['summary'] = excerpt
    item['description_origin'] = 'official_source' if excerpt else 'title_only'

    item['category'] = _content_category(item)
    item['severity'] = severity(_direct_text(item))
    item['source_host'] = _source_host(item.get('url') or item.get('source_url'))
    item['source_location'] = item.get('source_name') or item.get('source') or 'Kaynak'
    return item


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


def _discover_darphane_urls():
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
            discovered.append({'url': link, 'title': title, 'date': None})
    except Exception:
        pass
    return discovered


def _extract_official_paragraphs(soup):
    container = soup.find('article') or soup.find('main')
    if not container:
        return ''
    parts = []
    for p in container.find_all('p'):
        txt = clean(p.get_text(' ', strip=True))
        if len(txt) >= 25:
            parts.append(txt)
        if sum(len(x) for x in parts) >= 650:
            break
    return clean(' '.join(parts))[:700]


def _official_item(seed, existing, now):
    url = seed['url']
    title = seed.get('title') or 'Darphane UTTS Duyurusu'
    date = seed.get('date')
    excerpt = ''
    reachable = False

    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        reachable = True
        soup = BeautifulSoup(r.text, 'html.parser')
        heading = soup.find('h1') or soup.find('h2')
        page_title = clean(heading.get_text(' ', strip=True)) if heading else ''
        if page_title and len(page_title) > 7:
            title = page_title
        excerpt = _extract_official_paragraphs(soup)
    except Exception:
        pass

    context = clean(f'{date or ""} {title}')
    item = make_item(DARPHANE_SOURCE, title, url, context)
    if date and not item.get('date'):
        item['date'] = date
    item['source'] = CANONICAL_UTTS_GROUP
    item['source_name'] = CANONICAL_UTTS_SOURCE_NAME
    item['source_key'] = 'darphane_utts'
    item['official'] = True
    item['market'] = 'Petrol'
    item['record_type'] = 'UTTS Duyurusu'
    item['source_url'] = DARPHANE_UTTS_HOME
    item['source_excerpt'] = excerpt
    item['description_origin'] = 'official_source' if excerpt else 'title_only'
    item['summary'] = excerpt
    item['category'] = 'UTTS'
    item['source_host'] = _source_host(url)
    item['source_location'] = CANONICAL_UTTS_SOURCE_NAME

    item_id = uid(item)
    previous = existing.get(item_id)
    merged = merge_seen(item, previous, now)
    if not previous:
        merged['first_seen'] = _baseline_seen(item)
    return merged, reachable


def fetch_darphane_utts(existing):
    now = datetime.now(timezone.utc).isoformat()
    candidates = {}
    for seed in SEED_UTTS + _discover_darphane_urls():
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
        'home': DARPHANE_UTTS_HOME,
        'note': 'Darphane üzerindeki UTTS duyuru adresleri doğrudan kontrol edilir.',
    }
    if reachable_count == 0:
        status['error'] = 'Darphane UTTS duyuru sayfalarına erişilemedi'
    return list(found.values()), status


def _portal_card_title(anchor):
    fallback = clean(anchor.get_text(' ', strip=True))
    for parent in anchor.parents:
        if getattr(parent, 'name', None) not in {'article', 'li', 'div'}:
            continue
        text = clean(parent.get_text(' ', strip=True))
        if not (15 <= len(text) <= 900):
            continue
        heading = parent.find(['h2', 'h3', 'h4', 'h5', 'h6'])
        if heading:
            value = clean(heading.get_text(' ', strip=True))
            if len(value) >= 10 and value.casefold() not in {'duyurular', 'tümü', 'tumu'}:
                return value
        if _is_utts(text):
            text = re.sub(r'\bDetaylı\s+Bilgi\b', '', text, flags=re.I)
            text = re.sub(r'^\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\s*', '', text)
            return clean(text)[:300]
    return fallback


def _discover_utts_portal_from_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    found = {}
    for a in soup.find_all('a', href=True):
        href = urljoin(UTTS_PORTAL_HOME, a.get('href') or '')
        if not _is_utts_domain(href):
            continue
        if 'ulusal-tasit-tanima-sistemi-mevzuat-detaylari-' not in href:
            continue
        title = _portal_card_title(a)
        if not title:
            title = 'UTTS Duyurusu'
        parent = a.find_parent(['article', 'li', 'div']) or a.parent
        parent_text = clean(parent.get_text(' ', strip=True)) if parent else title
        date = parse_date(parent_text) or parse_date(title)
        found[href] = {'url': href, 'title': title, 'date': date}
    return list(found.values())


def _discover_utts_portal_search():
    queries = (
        'site:utts.gov.tr "Ulusal Taşıt Tanıma Sistemi"',
        'site:utts.gov.tr UTTS',
    )
    found = {}
    for query in queries:
        url = 'https://www.bing.com/search?format=rss&q=' + quote_plus(query)
        try:
            r = requests.get(url, headers=HEADERS, timeout=7)
            r.raise_for_status()
            root = ET.fromstring(r.text)
            for node in root.findall('.//item'):
                link = clean(node.findtext('link') or '')
                title = clean(node.findtext('title') or '')
                description = clean(node.findtext('description') or '')
                if not _is_utts_domain(link):
                    continue
                if not _is_utts(f'{title} {description}'):
                    continue
                found[link] = {
                    'url': link,
                    'title': title or 'UTTS Duyurusu',
                    'date': parse_date(title) or parse_date(description),
                }
        except Exception:
            continue
    return list(found.values())


def _utts_portal_item(seed, existing, now):
    url = seed['url']
    title = clean(seed.get('title') or 'UTTS Duyurusu')
    date = seed.get('date')
    excerpt = ''

    try:
        r = requests.get(url, headers=HEADERS, timeout=6)
        r.raise_for_status()
        content_type = (r.headers.get('content-type') or '').lower()
        if 'html' in content_type:
            soup = BeautifulSoup(r.text, 'html.parser')
            article = soup.find('article')
            if article:
                headings = article.find_all(['h1', 'h2', 'h3', 'h4', 'h5'])
                for heading in headings:
                    value = clean(heading.get_text(' ', strip=True))
                    if len(value) >= 10 and _is_utts(value):
                        title = value
                        break
                excerpt = _extract_official_paragraphs(soup)
                if not date:
                    date = parse_date(clean(article.get_text(' ', strip=True)))
    except Exception:
        pass

    item = make_item(UTTS_PORTAL_SOURCE, title, url, clean(f'{date or ""} {title}'))
    if date and not item.get('date'):
        item['date'] = date
    item['source'] = UTTS_PORTAL_GROUP
    item['source_name'] = UTTS_PORTAL_SOURCE_NAME
    item['source_key'] = 'utts_portal'
    item['official'] = True
    item['market'] = 'Petrol'
    item['record_type'] = 'UTTS Duyurusu'
    item['source_url'] = UTTS_PORTAL_HOME
    item['source_excerpt'] = excerpt
    item['description_origin'] = 'official_source' if excerpt else 'title_only'
    item['summary'] = excerpt
    item['category'] = 'UTTS'
    item['source_host'] = _source_host(url) or 'utts.gov.tr'
    item['source_location'] = UTTS_PORTAL_SOURCE_NAME

    item_id = uid(item)
    previous = existing.get(item_id)
    merged = merge_seen(item, previous, now)
    if not previous:
        merged['first_seen'] = _baseline_seen(item)
    return merged


def fetch_utts_portal(existing):
    now = datetime.now(timezone.utc).isoformat()
    direct_reachable = False
    seeds = {}
    direct_error = None

    portal_headers = dict(HEADERS)
    portal_headers.update({
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.6,en;q=0.4',
        'Cache-Control': 'no-cache',
    })

    for home_url in (UTTS_PORTAL_HOME, UTTS_PORTAL_ALT_HOME):
        try:
            r = requests.get(home_url, headers=portal_headers, timeout=4.5, allow_redirects=True)
            r.raise_for_status()
            if len(r.content) < 300:
                raise RuntimeError('short response')
            direct_reachable = True
            for seed in _discover_utts_portal_from_html(r.text):
                seeds[seed['url']] = seed
            break
        except Exception as exc:
            direct_error = str(exc)[:180]

    indexed_seeds = _discover_utts_portal_search()
    for seed in indexed_seeds:
        seeds.setdefault(seed['url'], seed)
    indexed_reachable = bool(indexed_seeds)

    found = {}
    for seed in seeds.values():
        item = _utts_portal_item(seed, existing, now)
        found[item['id']] = item

    ok = direct_reachable or indexed_reachable
    if direct_reachable:
        access_mode = 'direct'
        note = 'utts.gov.tr ana sayfası ve UTTS bağlantıları doğrudan kontrol edilir.'
    elif indexed_reachable:
        access_mode = 'official_index_fallback'
        note = (
            'Sunucu tarafında utts.gov.tr ana sayfasına doğrudan erişim sınırlı/zaman aşımına uğruyor; '
            'resmî utts.gov.tr alan adındaki indekslenmiş UTTS sayfa ve dokümanları yedek yöntemle izleniyor.'
        )
    else:
        access_mode = 'unavailable'
        note = 'utts.gov.tr doğrudan ve resmî alan adı indeks kontrolüyle taranamadı.'

    status = {
        'source': UTTS_PORTAL_GROUP,
        'source_name': UTTS_PORTAL_SOURCE_NAME,
        'ok': ok,
        'count': len(found),
        'checked_at': now,
        'home': UTTS_PORTAL_HOME,
        'access_mode': access_mode,
        'direct_reachable': direct_reachable,
        'indexed_reachable': indexed_reachable,
        'note': note,
    }
    if not ok:
        status['error'] = direct_error or 'utts.gov.tr adresine erişilemedi'
    elif not direct_reachable and direct_error:
        status['direct_error'] = direct_error
    return list(found.values()), status


def normalize_sector_data(data):
    raw_items = [i for i in (data.get('items') or []) if not _is_old_item(i)]
    cleaned = []
    for raw in raw_items:
        item = _sanitize_item(raw)
        if _is_sector_item(item):
            cleaned.append(item)

    existing = {i.get('id'): i for i in cleaned if i.get('id')}
    darphane_items, darphane_status = fetch_darphane_utts(existing)

    merged = {i.get('id'): i for i in cleaned if i.get('id')}
    for item in darphane_items:
        merged[item['id']] = _sanitize_item(item)

    portal_existing = dict(existing)
    portal_existing.update(merged)
    portal_items, portal_status = fetch_utts_portal(portal_existing)
    for item in portal_items:
        merged[item['id']] = _sanitize_item(item)

    items = list(merged.values())
    items.sort(
        key=lambda x: (
            x.get('date') or '0000-00-00',
            x.get('changed_at') or x.get('first_seen') or '',
        ),
        reverse=True,
    )
    items = items[:900]

    counts = Counter((i.get('source_name') or i.get('source')) for i in items)
    statuses = []
    custom_names = {CANONICAL_UTTS_SOURCE_NAME, UTTS_PORTAL_SOURCE_NAME}
    custom_groups = {CANONICAL_UTTS_GROUP, UTTS_PORTAL_GROUP}
    for status in data.get('sources') or []:
        if status.get('source_name') in OLD_SOURCE_NAMES:
            continue
        if status.get('source') in OLD_SOURCE_GROUPS:
            continue
        if status.get('source_name') in custom_names:
            continue
        if status.get('source') in custom_groups:
            continue
        st = dict(status)
        key = st.get('source_name') or st.get('source')
        st['count'] = counts.get(key, 0)
        statuses.append(st)
    statuses.append(darphane_status)
    statuses.append(portal_status)

    out = dict(data)
    out['items'] = items
    out['sources'] = statuses
    out['version'] = max(int(data.get('version') or 0), 15)
    out['content_policy'] = 'source_text_only'
    return out


def scan_sector_now():
    return normalize_sector_data(raw_scan_now())
