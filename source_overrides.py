import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlsplit
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from live_scan import scan_now as raw_scan_now
from sector_intelligence import build_deadlines, recent_changes
from scrape import HEADERS, clean, make_item, uid, merge_seen, parse_date, severity

DARPHANE_UTTS_HOME = 'https://www.darphane.gov.tr/ulusal-tasit-tanima-sistemi'
UTTS_PORTAL_HOME = 'https://www.utts.gov.tr/'
UTTS_PORTAL_ALT_HOME = 'https://utts.gov.tr/'
UTTS_HEALTH_URLS = (
    'https://www.utts.gov.tr/uploads/pdf/33-20260114-110402-69677822d34f3.pdf',
    'https://www.utts.gov.tr/uploads/pdf/33-20260114-110641-696778c13586a.pdf',
    'https://cdn-document.utts.gov.tr/utts-akaryakit-dagitim-sirketleri-kayit-kilavuzu.pdf',
    'https://cdn-document.utts.gov.tr/utts-yn-pompa-okc-firma-kayit-kilavuzu.pdf',
)
SEED_UTTS = [
    ('https://www.darphane.gov.tr/duyuru/utts-kapsaminda-yetkili-istasyon-montaj-firmalari-teknik-servis-bedelleri-hakkinda-duyuru', 'UTTS Kapsamında Yetkili İstasyon Montaj Firmaları Teknik Servis Bedelleri Hakkında Duyuru', '2026-02-03'),
    ('https://www.darphane.gov.tr/duyuru/2026-yili-akaryakit-istasyonlari-utts-donanim-montaj-hizmet-bedelleri-hakkinda-duyuru', '2026 Yılı Akaryakıt İstasyonları UTTS Donanım Montaj Hizmet Bedelleri Hakkında Duyuru', '2026-02-03'),
    ('https://www.darphane.gov.tr/duyuru/kamuoyuna-duyuru-15', 'Kamuoyuna Duyuru - UTTS', '2026-03-25'),
    ('https://www.darphane.gov.tr/duyuru/ulusal-tasit-tanima-sistemi-uygulamasina-yonelik-sure-uzatimina-iliskin-duyuru', 'Ulusal Taşıt Tanıma Sistemi Uygulamasına Yönelik Süre Uzatımına İlişkin Duyuru', '2025-12-26'),
    ('https://www.darphane.gov.tr/duyuru/utts-hakkinda-kamuoyuna-duyuru-4', 'UTTS Hakkında Kamuoyuna Duyuru', '2025-06-20'),
    ('https://www.darphane.gov.tr/duyuru/utts-hakkinda-kamuoyuna-duyuru-2', 'UTTS Hakkında Kamuoyuna Duyuru', '2025-05-09'),
    ('https://www.darphane.gov.tr/duyuru/utts-ucret-duzenlemeleri-hakkinda', 'UTTS Ücret Düzenlemeleri Hakkında', '2025-03-24'),
    ('https://www.darphane.gov.tr/duyuru/kamuoyuna-duyuru-13', 'UTTS Hakkında Kamuoyuna Duyuru', '2025-02-04'),
    ('https://www.darphane.gov.tr/duyuru/basin-duyurusu-2', 'Kamuoyuna Duyuru - UTTS', '2024-12-08'),
]

OLD_SOURCE_NAMES = {'UTTS', 'TOBB Sektör Haberleri', 'Darphane Duyurular'}
OLD_SOURCE_GROUPS = {'UTTS', 'TOBB'}
OLD_SOURCE_KEYS = {'utts', 'tobb', 'darphane'}
DARPHANE_NAME, DARPHANE_GROUP = 'Darphane / UTTS Duyuruları', 'Darphane / UTTS'
PORTAL_NAME, PORTAL_GROUP = 'UTTS Portalı (utts.gov.tr)', 'UTTS Portalı'

DARPHANE_SOURCE = {'key': 'darphane_utts', 'name': DARPHANE_NAME, 'group': DARPHANE_GROUP, 'official': True, 'url': DARPHANE_UTTS_HOME, 'market': 'Petrol', 'record_type': 'UTTS Duyurusu'}
PORTAL_SOURCE = {'key': 'utts_portal', 'name': PORTAL_NAME, 'group': PORTAL_GROUP, 'official': True, 'url': UTTS_PORTAL_HOME, 'market': 'Petrol', 'record_type': 'UTTS Duyurusu'}


def _norm(v):
    return clean(v).casefold().replace('\u0307', '')


def _token(text, token):
    return re.search(r'(?<!\w)' + re.escape(_norm(token)) + r'(?!\w)', _norm(text), re.UNICODE) is not None


def _host(url):
    try:
        return urlsplit(url or '').netloc.lower().removeprefix('www.')
    except Exception:
        return ''


def _utts_domain(url):
    h = _host(url)
    return h == 'utts.gov.tr' or h.endswith('.utts.gov.tr')


def _is_utts(text):
    low = _norm(text)
    if _token(text, 'utts') or any(x in low for x in ('ulusal taşıt tanıma', 'ulusal tasit tanima', 'taşıt tanıma sistemi', 'tasit tanima sistemi', 'taşıt tanıma okuyucu', 'tasit tanima okuyucu', 'taşıt tanıma birimi', 'tasit tanima birimi', 'yetkili istasyon montaj', 'tabanca okuyucu')):
        return True
    if not any(_token(text, x) for x in ('tto', 'tim', 'ttb', 'tts', 'yimf')):
        return False
    return any(x in low for x in ('akaryakıt', 'akaryakit', 'istasyon montaj', 'yakıt pompası', 'yakit pompasi', 'pompa ökc', 'pompa okc', 'darphane', 'yn ökc', 'yn okc', 'y-imf'))


def _sector_title(title):
    low = _norm(title)
    if _is_utts(title) or _token(title, 'lpg') or _token(title, 'epdk'):
        return True
    return any(x in low for x in ('akaryakıt', 'akaryakit', 'petrol piyasası', 'petrol piyasasi', 'petrol ürünleri', 'petrol urunleri', 'benzin', 'motorin', 'otogaz', 'pompa ökc', 'pompa okc', 'yeni nesil ödeme kaydedici', 'ulusal marker', 'zorunlu petrol stoku', 'rafineri', 'bayilik lisansı', 'bayilik lisansi'))


def _direct_text(item):
    return clean(f"{item.get('title') or ''} {item.get('source_excerpt') or ''}")


def _category(item):
    text, low = _direct_text(item), _norm(_direct_text(item))
    if item.get('source_key') in {'darphane_utts', 'utts_portal'} or item.get('source') in {DARPHANE_GROUP, PORTAL_GROUP} or _is_utts(text):
        return 'UTTS'
    if _token(text, 'okc') or _token(text, 'pos') or 'ödeme kaydedici' in low or 'odeme kaydedici' in low or 'mali cihaz' in low:
        return 'ÖKC / POS'
    if _token(text, 'lpg') or any(x in low for x in ('otogaz', 'tüplügaz', 'tuplugaz')):
        return 'LPG'
    if _token(text, 'ötv') or _token(text, 'otv') or 'vergi' in low:
        return 'Vergi / ÖTV'
    if any(x in low for x in ('lisans', 'denetim', 'ceza', 'idari yaptırım', 'idari yaptirim')):
        return 'Lisans / Denetim'
    if item.get('record_type') == 'Denetim':
        return 'Lisans / Denetim'
    if item.get('record_type') in {'Mevzuat', 'Kurul Kararı'} or any(x in low for x in ('kurul kararı', 'kurul karari', 'tebliğ', 'teblig', 'yönetmelik', 'yonetmelik', 'kanun', 'mevzuat')):
        return 'Mevzuat'
    if item.get('market') == 'LPG':
        return 'LPG'
    return 'Akaryakıt'


def _sanitize(raw):
    item = dict(raw)
    if item.get('source_key') == 'gib':
        d = parse_date(clean(item.get('title') or ''))
        if d:
            item['date'] = d
    excerpt = clean(item.get('source_excerpt') or '') if item.get('description_origin') == 'official_source' else ''
    item['source_excerpt'] = item['summary'] = excerpt
    item['description_origin'] = 'official_source' if excerpt else 'title_only'
    item['category'] = _category(item)
    item['severity'] = severity(_direct_text(item))
    item['source_host'] = _host(item.get('url') or item.get('source_url'))
    item['source_location'] = item.get('source_name') or item.get('source') or 'Kaynak'
    return item


def _old(item):
    return item.get('source_name') in OLD_SOURCE_NAMES or item.get('source') in OLD_SOURCE_GROUPS or item.get('source_key') in OLD_SOURCE_KEYS


def _sector_item(item):
    if item.get('source_key') in {'darphane_utts', 'utts_portal'} or item.get('source') in {DARPHANE_GROUP, PORTAL_GROUP} or item.get('epdk_focus'):
        return True
    if item.get('source') in {'PÜİS', 'TABGİS', 'PETDER', 'LPG Derneği'}:
        return True
    return _sector_title(item.get('title') or '')


def _baseline(item):
    return f"{item['date']}T12:00:00+00:00" if item.get('date') else '2000-01-01T00:00:00+00:00'


def _official_excerpt(soup):
    container = soup.find('article') or soup.find('main')
    if not container:
        return ''
    out = []
    for p in container.find_all('p'):
        t = clean(p.get_text(' ', strip=True))
        if len(t) >= 25:
            out.append(t)
        if sum(map(len, out)) >= 650:
            break
    return clean(' '.join(out))[:700]


def _discover_darphane():
    url = 'https://www.bing.com/search?format=rss&q=' + quote_plus('site:darphane.gov.tr/duyuru (UTTS OR "Ulusal Taşıt Tanıma")')
    out = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=7); r.raise_for_status()
        root = ET.fromstring(r.text)
        for n in root.findall('.//item'):
            link, title = clean(n.findtext('link') or ''), clean(n.findtext('title') or '')
            if 'darphane.gov.tr/duyuru/' in link and _is_utts(title):
                out.append((link, title, None))
    except Exception:
        pass
    return out


def _darphane_item(seed, existing, now):
    url, title, date = seed
    excerpt, reachable = '', False
    try:
        r = requests.get(url, headers=HEADERS, timeout=8); r.raise_for_status(); reachable = True
        soup = BeautifulSoup(r.text, 'html.parser')
        h = soup.find('h1') or soup.find('h2')
        if h and len(clean(h.get_text(' ', strip=True))) > 7:
            title = clean(h.get_text(' ', strip=True))
        excerpt = _official_excerpt(soup)
    except Exception:
        pass
    item = make_item(DARPHANE_SOURCE, title, url, clean(f'{date or ""} {title}'))
    if date and not item.get('date'):
        item['date'] = date
    item.update(source=DARPHANE_GROUP, source_name=DARPHANE_NAME, source_key='darphane_utts', official=True, market='Petrol', record_type='UTTS Duyurusu', source_url=DARPHANE_UTTS_HOME, source_excerpt=excerpt, summary=excerpt, description_origin='official_source' if excerpt else 'title_only', category='UTTS', source_host=_host(url), source_location=DARPHANE_NAME)
    prev = existing.get(uid(item)); merged = merge_seen(item, prev, now)
    if not prev:
        merged['first_seen'] = _baseline(item)
    return merged, reachable


def fetch_darphane_utts(existing):
    now = datetime.now(timezone.utc).isoformat(); seeds = {x[0]: x for x in SEED_UTTS + _discover_darphane()}; found = {}; reached = 0
    for seed in seeds.values():
        item, ok = _darphane_item(seed, existing, now); found[item['id']] = item; reached += int(ok)
    status = {'source': DARPHANE_GROUP, 'source_name': DARPHANE_NAME, 'ok': reached > 0, 'count': len(found), 'checked_at': now, 'official_urls_reached': reached, 'official_urls_total': len(seeds), 'home': DARPHANE_UTTS_HOME, 'note': 'Darphane üzerindeki UTTS duyuru adresleri doğrudan kontrol edilir.'}
    if not reached:
        status['error'] = 'Darphane UTTS duyuru sayfalarına erişilemedi'
    return list(found.values()), status


def _portal_from_html(html):
    soup = BeautifulSoup(html, 'html.parser'); out = {}
    for a in soup.find_all('a', href=True):
        href = urljoin(UTTS_PORTAL_HOME, a.get('href') or '')
        if not _utts_domain(href) or 'ulusal-tasit-tanima-sistemi-mevzuat-detaylari-' not in href:
            continue
        parent = a.find_parent(['article', 'li', 'div']) or a.parent
        text = clean(parent.get_text(' ', strip=True)) if parent else clean(a.get_text(' ', strip=True))
        h = parent.find(['h2', 'h3', 'h4', 'h5']) if parent else None
        title = clean(h.get_text(' ', strip=True)) if h else clean(a.get_text(' ', strip=True)) or text[:240]
        out[href] = {'url': href, 'title': title or 'UTTS Duyurusu', 'date': parse_date(text) or parse_date(title)}
    return list(out.values())


def _portal_search():
    url = 'https://www.bing.com/search?format=rss&q=' + quote_plus('site:utts.gov.tr "Ulusal Taşıt Tanıma Sistemi" UTTS')
    out = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=5); r.raise_for_status(); root = ET.fromstring(r.text)
        for n in root.findall('.//item'):
            link, title, desc = clean(n.findtext('link') or ''), clean(n.findtext('title') or ''), clean(n.findtext('description') or '')
            if _utts_domain(link) and _is_utts(f'{title} {desc}'):
                out[link] = {'url': link, 'title': title or 'UTTS Duyurusu', 'date': parse_date(title) or parse_date(desc)}
    except Exception:
        pass
    return list(out.values())


def _probe_utts_static():
    headers = dict(HEADERS); headers.update({'Range': 'bytes=0-2047', 'Accept': '*/*'}); last = None
    for url in UTTS_HEALTH_URLS:
        try:
            r = requests.get(url, headers=headers, timeout=3.5, allow_redirects=True, stream=True)
            ok = r.status_code in (200, 206) and _utts_domain(r.url or url); final = r.url or url; r.close()
            if ok:
                return True, final, None
            last = f'HTTP {r.status_code}'
        except Exception as exc:
            last = str(exc)[:180]
    return False, None, last


def _portal_item(seed, existing, now):
    url, title, date = seed['url'], clean(seed.get('title') or 'UTTS Duyurusu'), seed.get('date'); excerpt = ''
    try:
        r = requests.get(url, headers=HEADERS, timeout=5); r.raise_for_status()
        if 'html' in (r.headers.get('content-type') or '').lower():
            soup = BeautifulSoup(r.text, 'html.parser'); article = soup.find('article')
            if article:
                h = article.find(['h1', 'h2', 'h3'])
                if h and _is_utts(clean(h.get_text(' ', strip=True))):
                    title = clean(h.get_text(' ', strip=True))
                excerpt = _official_excerpt(soup)
                date = date or parse_date(clean(article.get_text(' ', strip=True)))
    except Exception:
        pass
    item = make_item(PORTAL_SOURCE, title, url, clean(f'{date or ""} {title}'))
    if date and not item.get('date'):
        item['date'] = date
    item.update(source=PORTAL_GROUP, source_name=PORTAL_NAME, source_key='utts_portal', official=True, market='Petrol', record_type='UTTS Duyurusu', source_url=UTTS_PORTAL_HOME, source_excerpt=excerpt, summary=excerpt, description_origin='official_source' if excerpt else 'title_only', category='UTTS', source_host=_host(url) or 'utts.gov.tr', source_location=PORTAL_NAME)
    prev = existing.get(uid(item)); merged = merge_seen(item, prev, now)
    if not prev:
        merged['first_seen'] = _baseline(item)
    return merged


def fetch_utts_portal(existing):
    now = datetime.now(timezone.utc).isoformat(); direct = False; direct_error = None; seeds = {}
    headers = dict(HEADERS); headers.update({'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', 'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.5', 'Cache-Control': 'no-cache'})
    for home in (UTTS_PORTAL_HOME, UTTS_PORTAL_ALT_HOME):
        try:
            r = requests.get(home, headers=headers, timeout=2.5, allow_redirects=True); r.raise_for_status()
            if len(r.content) < 300:
                raise RuntimeError('short response')
            direct = True
            for s in _portal_from_html(r.text): seeds[s['url']] = s
            break
        except Exception as exc:
            direct_error = str(exc)[:180]

    indexed = _portal_search()
    for s in indexed: seeds.setdefault(s['url'], s)
    indexed_ok = bool(indexed)
    static_ok, static_url, static_error = (False, None, None) if direct else _probe_utts_static()

    found = {}
    for s in seeds.values():
        item = _portal_item(s, existing, now); found[item['id']] = item

    ok = direct or static_ok or indexed_ok
    if direct:
        mode, note = 'direct', 'utts.gov.tr ana sayfası ve UTTS bağlantıları doğrudan kontrol edildi.'
    elif static_ok:
        mode, note = 'official_static_fallback', 'Ana sayfa sunucu tarafında zaman aşımına uğradı; resmî utts.gov.tr / cdn-document.utts.gov.tr doküman sunucusu erişilebilir. Kaynak aktif kabul edildi.'
    elif indexed_ok:
        mode, note = 'official_index_fallback', 'Ana sayfaya doğrudan sunucu erişimi sınırlı; resmî utts.gov.tr alanındaki indekslenmiş UTTS sayfaları yedek yöntemle izleniyor.'
    else:
        mode, note = 'unavailable', 'UTTS ana sayfası, resmî doküman sunucuları ve indeks kontrolü birlikte doğrulanamadı.'

    status = {'source': PORTAL_GROUP, 'source_name': PORTAL_NAME, 'ok': ok, 'count': len(found), 'checked_at': now, 'home': UTTS_PORTAL_HOME, 'access_mode': mode, 'direct_reachable': direct, 'static_reachable': static_ok, 'indexed_reachable': indexed_ok, 'note': note}
    if static_url: status['health_probe_url'] = static_url
    if not ok: status['error'] = direct_error or static_error or 'utts.gov.tr doğrulanamadı'
    elif not direct and direct_error: status['direct_error'] = direct_error
    return list(found.values()), status



DEDICATED_EPDK_TITLES = {
    'epdk_petrol_istatistik': 'sektör raporu',
    'epdk_lpg_istatistik': 'sektör raporu',
    'epdk_fiyatlandirma': 'fiyatlandırma raporu',
    'epdk_petrol_lisans': 'aylık toplu lisanslar',
    'epdk_lpg_lisans': 'aylık toplu lisanslar',
}
DETAIL_TEXT_TERMS = ('süre uzat', 'sure uzat', 'yükümlülük', 'yukumluluk', 'son tarih', 'tebliğ', 'teblig')


def _detail_page_text(url, title=''):
    try:
        r = requests.get(url, headers=HEADERS, timeout=5)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        for bad in soup.find_all(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            bad.decompose()
        candidates = []
        for node in soup.select('article, main, .duyuru-detay, .duyuruDetay, .content, .icerik, #content, #icerik'):
            text = clean(node.get_text(' ', strip=True))
            if 120 <= len(text) <= 12000:
                candidates.append(text)
        if not candidates and soup.body:
            text = clean(soup.body.get_text(' ', strip=True))
            if 120 <= len(text) <= 12000:
                candidates.append(text)
        if not candidates:
            return ''
        title_key = clean(re.sub(r'^\d{1,2}\s+\w+\s+20\d{2}\s+', '', title or '')).casefold()[:45]
        candidates.sort(key=lambda t: (title_key in t.casefold() if title_key else False, min(len(t), 5000)), reverse=True)
        return candidates[0][:2200]
    except Exception:
        return ''


def _enrich_gib_detail_text(items):
    targets = [
        i for i in items
        if i.get('source_key') == 'gib'
        and not clean(i.get('source_excerpt') or '')
        and i.get('url')
        and any(term in clean(i.get('title') or '').casefold() for term in DETAIL_TEXT_TERMS)
    ]
    targets.sort(key=lambda i: i.get('date') or '0000-00-00', reverse=True)
    targets = targets[:8]
    if not targets:
        return items
    by_id = {id(i): i for i in targets}
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(_detail_page_text, i.get('url'), i.get('title')): id(i) for i in targets}
        for job in as_completed(jobs):
            item = by_id.get(jobs[job])
            if not item:
                continue
            try:
                text = clean(job.result())
            except Exception:
                text = ''
            if text:
                item['source_excerpt'] = text
                item['summary'] = text
                item['description_origin'] = 'official_source'
    return items


def _clean_parser_migration_change(item):
    details = item.get('change_details') or []
    key = item.get('source_key')
    required = DEDICATED_EPDK_TITLES.get(key)
    if not required or len(details) != 1:
        return item
    ch = details[0]
    old = clean(ch.get('old') or '').casefold()
    new = clean(ch.get('new') or '').casefold()
    if ch.get('field') == 'title' and required in new and required not in old:
        if int(item.get('revision') or 0) <= 1:
            item['revision'] = 0
            item['changed_at'] = None
        item['change_details'] = []
    return item


def _dedicated_epdk_record(raw):
    key = raw.get('source_key')
    required = DEDICATED_EPDK_TITLES.get(key)
    if not required:
        return True
    title = clean(raw.get('title') or '')
    low = title.casefold()
    if required not in low:
        return False
    if key == 'epdk_fiyatlandirma':
        return bool(raw.get('report_period')) or bool(re.search(r'\b20\d{2}\b', title))
    if key in {'epdk_petrol_istatistik', 'epdk_lpg_istatistik'}:
        return bool(re.search(r'\b20\d{2}\b', title))
    return True

def normalize_sector_data(data):
    cleaned = []
    for raw in data.get('items') or []:
        if _old(raw) or not _dedicated_epdk_record(raw):
            continue
        item = _sanitize(raw)
        if _sector_item(item):
            cleaned.append(item)

    existing = {i.get('id'): i for i in cleaned if i.get('id')}
    d_items, d_status = fetch_darphane_utts(existing)
    merged = {i.get('id'): i for i in cleaned if i.get('id')}
    for i in d_items: merged[i['id']] = _sanitize(i)
    p_items, p_status = fetch_utts_portal(merged)
    for i in p_items: merged[i['id']] = _sanitize(i)

    items = [_clean_parser_migration_change(i) for i in merged.values()]
    items = _enrich_gib_detail_text(items)
    for i in items:
        if i.get('description_origin') == 'official_source':
            i['category'] = _category(i)
            i['severity'] = severity(_direct_text(i))
    items.sort(key=lambda x: (x.get('date') or ((x.get('report_period') or '0000-00') + '-01'), x.get('changed_at') or x.get('first_seen') or ''), reverse=True)
    items = items[:900]
    counts = Counter((i.get('source_name') or i.get('source')) for i in items)

    statuses = []
    custom_names, custom_groups = {DARPHANE_NAME, PORTAL_NAME}, {DARPHANE_GROUP, PORTAL_GROUP}
    for s in data.get('sources') or []:
        if s.get('source_name') in OLD_SOURCE_NAMES or s.get('source') in OLD_SOURCE_GROUPS or s.get('source_name') in custom_names or s.get('source') in custom_groups:
            continue
        st = dict(s); st['count'] = counts.get(st.get('source_name') or st.get('source'), 0); statuses.append(st)
    statuses.extend((d_status, p_status))

    out = dict(data)
    out['items'] = items
    out['sources'] = statuses
    out['deadlines'] = build_deadlines(items)
    out['recent_changes'] = recent_changes(items)
    out['version'] = max(int(data.get('version') or 0), 18)
    out['content_policy'] = 'source_text_only'
    out['features'] = ['source_specific_parsers', 'epdk_reports', 'change_tracking', 'deadline_radar']
    return out


def scan_sector_now():
    return normalize_sector_data(raw_scan_now())
