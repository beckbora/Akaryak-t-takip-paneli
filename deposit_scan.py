import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlsplit
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from scrape import HEADERS, canonical_url, clean, parse_date

TERMS = (
    'doa', 'dbys', 'depozito', 'depozitolu', 'depozitosu olan ambalaj',
    'depozito yönetim sistemi', 'depozito yonetim sistemi',
    'depozito bilgi yönetim sistemi', 'depozito bilgi yonetim sistemi',
    'depozito iade', 'iade makinesi', 'iade makinası', 'iade noktası',
    'dim', 'dsys', 'dekab', 'saha operatörü', 'saha operatoru',
    'türkiye çevre ajansı', 'turkiye cevre ajansi',
)

PROVINCES = [
    'Adana','Adıyaman','Afyonkarahisar','Ağrı','Aksaray','Amasya','Ankara','Antalya','Ardahan','Artvin',
    'Aydın','Balıkesir','Bartın','Batman','Bayburt','Bilecik','Bingöl','Bitlis','Bolu','Burdur','Bursa',
    'Çanakkale','Çankırı','Çorum','Denizli','Diyarbakır','Düzce','Edirne','Elazığ','Erzincan','Erzurum',
    'Eskişehir','Gaziantep','Giresun','Gümüşhane','Hakkâri','Hatay','Iğdır','Isparta','İstanbul','İzmir',
    'Kahramanmaraş','Karabük','Karaman','Kars','Kastamonu','Kayseri','Kırıkkale','Kırklareli','Kırşehir',
    'Kilis','Kocaeli','Konya','Kütahya','Malatya','Manisa','Mardin','Mersin','Muğla','Muş','Nevşehir',
    'Niğde','Ordu','Osmaniye','Rize','Sakarya','Samsun','Siirt','Sinop','Sivas','Şanlıurfa','Şırnak',
    'Tekirdağ','Tokat','Trabzon','Tunceli','Uşak','Van','Yalova','Yozgat','Zonguldak'
]

SOURCES = [
    {
        'key': 'doa', 'name': 'DOA Resmî Platformu', 'group': 'DOA',
        'domains': ('doa.gov.tr',), 'home': 'https://doa.gov.tr/',
        'authority': 'DOA / Türkiye Çevre Ajansı',
        'queries': (
            'site:doa.gov.tr DOA depozito',
            'site:doa.gov.tr "iade noktası" "iade makinesi"',
        ),
    },
    {
        'key': 'dbys', 'name': 'DBYS Resmî Duyuruları', 'group': 'DBYS',
        'domains': ('dbys.gov.tr',), 'home': 'https://dbys.gov.tr/',
        'authority': 'Depozito Bilgi Yönetim Sistemi / Darphane',
        'queries': (
            'site:dbys.gov.tr depozito DBYS duyuru',
            'site:portal.dbys.gov.tr depozito duyuru DSYS DİM',
        ),
        'listing_urls': (
            'https://dbys.gov.tr/',
            'https://portal.dbys.gov.tr/tr-tr/home/announcements',
        ),
    },
    {
        'key': 'csb', 'name': 'ÇŞİDB Resmî Haberleri', 'group': 'ÇŞİDB',
        'domains': ('csb.gov.tr',), 'home': 'https://csb.gov.tr/',
        'authority': 'T.C. Çevre, Şehircilik ve İklim Değişikliği Bakanlığı',
        'queries': (
            'site:csb.gov.tr DOA depozito',
            'site:csb.gov.tr "Depozito Yönetim Sistemi"',
        ),
    },
    {
        'key': 'tuca', 'name': 'TÜÇA Resmî Kaynakları', 'group': 'TÜÇA',
        'domains': ('tuca.gov.tr',), 'home': 'https://tuca.gov.tr/',
        'authority': 'Türkiye Çevre Ajansı',
        'queries': (
            'site:tuca.gov.tr DOA DBYS depozito',
            'site:tuca.gov.tr "Depozito Yönetim Sistemi"',
        ),
    },
    {
        'key': 'darphane_dbys', 'name': 'Darphane DYS / DBYS', 'group': 'Darphane',
        'domains': ('darphane.gov.tr',), 'home': 'https://www.darphane.gov.tr/',
        'authority': 'T.C. Hazine ve Maliye Bakanlığı Darphane ve Damga Matbaası Genel Müdürlüğü',
        'queries': (
            'site:darphane.gov.tr DBYS depozito',
            'site:darphane.gov.tr "Depozito Yönetim Sistemi"',
        ),
    },
    {
        'key': 'gib_depozito', 'name': 'GİB Depozito Duyuruları', 'group': 'GİB',
        'domains': ('gib.gov.tr',), 'home': 'https://www.gib.gov.tr/',
        'authority': 'Gelir İdaresi Başkanlığı',
        'queries': ('site:gib.gov.tr DBYS depozito TÜÇA',),
    },
    {
        'key': 'resmigazete_depozito', 'name': 'Resmî Gazete Depozito', 'group': 'Resmî Gazete',
        'domains': ('resmigazete.gov.tr',), 'home': 'https://www.resmigazete.gov.tr/',
        'authority': 'Resmî Gazete',
        'queries': ('site:resmigazete.gov.tr depozito ambalaj Türkiye Çevre Ajansı',),
    },
]

SEEDS = [
    {
        'source_key': 'doa',
        'url': 'https://doa.gov.tr/ambalaj-iadesi-nereye-yapilir',
        'title': 'DOA İade Makineleri ve İade Noktaları Haritası',
        'date': None,
        'summary': 'DOA resmî platformunda il, ilçe, mahalle ve lokasyona göre Depozito İade Makinesi ve DOA İade Merkezi araması yapılabilir.',
        'record_type': 'İade Noktası Rehberi',
    },
    {
        'source_key': 'dbys',
        'url': 'https://dbys.gov.tr/haberler-duyurular/2026-yili-3-ve-4-donem-uygulanacak-depozito-katilim-bedeli',
        'title': '2026 Yılı 3. ve 4. Dönem Uygulanacak Depozito Katılım Bedeli',
        'date': '2026-06-19',
        'summary': '01.07.2026 tarihinden itibaren geçerli 2026 yılı 3. ve 4. dönem DEKAB tarifesine ilişkin DBYS resmî duyurusu.',
        'record_type': 'Genel Duyuru',
    },
    {
        'source_key': 'dbys',
        'url': 'https://dbys.gov.tr/',
        'title': 'Etiket ve Ambalaj İadelerinin DBYS Üzerinden Gerçekleştirilmesine İlişkin Duyuru',
        'date': '2026-07-02',
        'summary': 'Etiket ve ambalaj iadelerinin DBYS üzerinden yürütülmesine ilişkin resmî DBYS duyurusu.',
        'record_type': 'Genel Duyuru',
    },
    {
        'source_key': 'dbys',
        'url': 'https://portal.dbys.gov.tr/tr-tr/home/announcements',
        'title': 'DSYS Operatörleri İl Dağılımı',
        'date': '2026-03-17',
        'summary': 'Depozito Saha Yönetim Sistemi operatörlerinin il dağılımına ilişkin DBYS resmî duyuru arşivi kaydı.',
        'record_type': 'DSYS Duyurusu',
    },
    {
        'source_key': 'dbys',
        'url': 'https://dbys.gov.tr/',
        'title': '2026 Yılı Yapıştırma DYS Etiketi Birim Fiyatına İlişkin Duyuru',
        'date': '2026-01-30',
        'summary': '2026 yılı yapıştırma DYS etiketi birim fiyatına ilişkin resmî DBYS duyurusu.',
        'record_type': 'Fiyat Duyurusu',
    },
    {
        'source_key': 'dbys',
        'url': 'https://dbys.gov.tr/',
        'title': '2026 Yılı Etiket ve Ambalaj Üreticileri Kontrol ve Doğrulama Ekipmanları Birim Bedelleri',
        'date': '2026-01-14',
        'summary': 'Etiket ve ambalaj üreticileri kontrol ve doğrulama ekipmanlarına ait 2026 yılı birim bedelleri.',
        'record_type': 'Fiyat Duyurusu',
    },
    {
        'source_key': 'csb',
        'url': 'https://csb.gov.tr/haberler/doa-1-temmuz-itibariyla-turkiye-geneline-yayilacak-305475',
        'title': 'DOA 1 Temmuz İtibarıyla Türkiye Geneline Yayılacak',
        'date': '2026-06-04',
        'summary': 'Bakanlık, DOA sisteminin 1 Temmuz 2026 itibarıyla Türkiye genelinde yaygınlaşacağını ve depozito iade bedelinin 1 TL olacağını duyurdu.',
        'record_type': 'Bakanlık Haberi',
    },
]

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def _norm(text):
    return clean(text).casefold()


def _relevant(text):
    low = _norm(text)
    return any(term in low for term in TERMS)


def _host_allowed(url, source):
    try:
        host = (urlsplit(url).hostname or '').lower()
    except Exception:
        return False
    return any(host == d or host.endswith('.' + d) for d in source['domains'])


def _uid(url, title):
    raw = (canonical_url(url) + '|' + clean(title).casefold()).encode('utf-8')
    return hashlib.sha1(raw).hexdigest()[:20]


def _category(text):
    low = _norm(text)
    if any(x in low for x in ('iade nokt', 'iade makin', 'dim ', 'd.i.m')):
        return 'DİM / İade Noktası'
    if any(x in low for x in ('dsys', 'saha operat', 'operatör', 'operator')):
        return 'DSYS / Operatör'
    if any(x in low for x in ('dekab', 'birim fiyat', 'birim bedel', 'ücret', 'tarife')):
        return 'Ücret / DEKAB'
    if any(x in low for x in ('usul ve esas', 'yönetmelik', 'yonetmelik', 'tebliğ', 'teblig', 'resmî gazete', 'resmi gazete')):
        return 'Mevzuat'
    if 'dbys' in low or 'depozito bilgi yönetim' in low or 'depozito bilgi yonetim' in low:
        return 'DBYS'
    return 'DOA / Depozito'


def _severity(text):
    low = _norm(text)
    if any(x in low for x in ('zorunlu', 'son tarih', 'yürürlük', 'yururluk', '01.07.2026', '1 temmuz')):
        return 'critical'
    if any(x in low for x in ('duyuru', 'dekab', 'birim fiyat', 'yetkilendir', 'operatör', 'operator')):
        return 'important'
    return 'normal'


def _rss_search(query, source):
    url = 'https://www.bing.com/search?format=rss&q=' + quote_plus(query)
    out = []
    ok = False
    try:
        r = SESSION.get(url, timeout=4.5)
        r.raise_for_status()
        ok = True
        root = ET.fromstring(r.text)
        for node in root.findall('.//item'):
            link = clean(node.findtext('link') or '')
            title = clean(node.findtext('title') or '')
            desc = clean(node.findtext('description') or '')
            if not link or not title or not _host_allowed(link, source):
                continue
            if not _relevant(title + ' ' + desc):
                continue
            out.append({
                'url': canonical_url(link),
                'title': title,
                'summary': desc,
                'date': parse_date(desc + ' ' + title),
            })
    except Exception:
        pass
    return out, ok


def _listing_links(url, source):
    out = []
    ok = False
    try:
        r = SESSION.get(url, timeout=4.5)
        r.raise_for_status()
        ok = True
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            title = clean(a.get_text(' ', strip=True))
            href = canonical_url(urljoin(url, a['href']))
            if len(title) < 8 or not _host_allowed(href, source):
                continue
            context_node = a.find_parent(['article', 'li', 'tr', 'div']) or a.parent
            context = clean(context_node.get_text(' ', strip=True) if context_node else title)
            if not _relevant(title + ' ' + context):
                continue
            out.append({
                'url': href,
                'title': title,
                'summary': context[:800],
                'date': parse_date(context),
            })
    except Exception:
        pass
    return out[:120], ok


def _make_item(raw, source):
    title = clean(raw.get('title') or 'DOA / DBYS Duyurusu')
    summary = clean(raw.get('summary') or '')
    date = raw.get('date') or parse_date(summary + ' ' + title)
    url = canonical_url(raw.get('url') or source['home'])
    text = title + ' ' + summary
    return {
        'id': _uid(url, title),
        'title': title[:260],
        'url': url,
        'date': date,
        'summary': summary[:800],
        'source': source['group'],
        'source_name': source['name'],
        'source_key': source['key'],
        'source_url': source['home'],
        'authority': source['authority'],
        'official': True,
        'official_reason': 'Bağlantı, bu kurum için tanımlı doğrulanmış resmî .gov.tr alan adıyla eşleşmektedir.',
        'category': _category(text),
        'record_type': raw.get('record_type') or 'Haber / Duyuru',
        'severity': _severity(text),
    }


def _scan_source(source):
    raw_by_key = {}
    source_ok = False

    for seed in SEEDS:
        if seed['source_key'] == source['key'] and _host_allowed(seed['url'], source):
            raw_by_key[(canonical_url(seed['url']), clean(seed['title']).casefold())] = dict(seed)

    for listing in source.get('listing_urls', ()):
        rows, ok = _listing_links(listing, source)
        source_ok = source_ok or ok
        for row in rows:
            raw_by_key[(row['url'], clean(row['title']).casefold())] = row

    for query in source.get('queries', ()):
        rows, ok = _rss_search(query, source)
        source_ok = source_ok or ok
        for row in rows:
            raw_by_key[(row['url'], clean(row['title']).casefold())] = row

    # No per-result page downloads here: this keeps the live Vercel scan fast.
    # Every accepted result is nevertheless constrained to the source's official .gov.tr domain.
    items = [_make_item(raw, source) for raw in list(raw_by_key.values())[:90]]
    status = {
        'source': source['group'],
        'source_name': source['name'],
        'ok': source_ok,
        'count': len(items),
        'official': True,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'home': source['home'],
        'note': 'Yeni bağlantılar arama/listing üzerinden keşfedilir ve yalnızca tanımlı resmî .gov.tr alan adları kabul edilir.',
    }
    if not status['ok']:
        status['error'] = 'Canlı kaynağa veya arama indeksine erişilemedi; başlangıçtaki doğrulanmış resmî kayıtlar korunur.'
    return items, status


def scan_deposit_now():
    all_items = []
    statuses = []
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        jobs = {pool.submit(_scan_source, source): source for source in SOURCES}
        for future in as_completed(jobs):
            source = jobs[future]
            try:
                items, status = future.result()
            except Exception as exc:
                items = []
                status = {
                    'source': source['group'], 'source_name': source['name'], 'ok': False,
                    'count': 0, 'official': True,
                    'checked_at': datetime.now(timezone.utc).isoformat(),
                    'error': str(exc)[:160],
                }
            all_items.extend(items)
            statuses.append(status)

    # Same URL can legitimately host several DBYS homepage announcements, therefore dedupe by URL+title.
    dedup = {}
    for item in all_items:
        key = (canonical_url(item.get('url') or ''), clean(item.get('title') or '').casefold())
        dedup[key] = item

    items = list(dedup.values())
    items.sort(key=lambda x: (x.get('date') or '0000-00-00', x.get('title') or ''), reverse=True)
    statuses.sort(key=lambda x: x.get('source_name') or '')
    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        item['first_seen'] = (item.get('date') + 'T12:00:00+00:00') if item.get('date') else now

    return {
        'version': 2,
        'updated_at': now,
        'items': items[:300],
        'sources': statuses,
        'provinces': PROVINCES,
        'map': {
            'provider': 'DOA Resmî Platformu',
            'official': True,
            'url': 'https://doa.gov.tr/ambalaj-iadesi-nereye-yapilir',
            'title': 'DOA İade Makineleri ve İade Noktaları',
            'filter_level': 'İl / ilçe / mahalle / lokasyon',
            'note': 'Makine ve iade noktaları DOA resmî canlı haritasından görüntülenir; yerel statik kopya tutulmaz.',
        },
    }
