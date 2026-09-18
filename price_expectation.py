import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from statistics import median
from zoneinfo import ZoneInfo
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlsplit
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
TR_TZ = ZoneInfo('Europe/Istanbul')
EXPECTATION_DATA = ROOT / 'price_expectation.json'
PRICE_DATA = ROOT / 'prices.json'

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.4',
}

# General economy/news sources + outlets that regularly cover Turkish fuel markets.
SOURCE_NAMES = {
    'aa.com.tr': 'Anadolu Ajansı',
    'trthaber.com': 'TRT Haber',
    'bloomberght.com': 'Bloomberg HT',
    'cnbce.com': 'CNBC-e',
    'ntv.com.tr': 'NTV',
    'haberturk.com': 'Habertürk',
    'ekonomim.com': 'Ekonomim',
    'enerjigunlugu.net': 'Enerji Günlüğü',
    'petroturk.com': 'Petroturk',
    'enerjipetrolgaz.com': 'Enerji Petrol Gaz',
    'puis.org.tr': 'PÜİS',
    'tabgis.org.tr': 'TABGİS',
    'endeks24.com': 'Endeks24',
    'ekoturk.com': 'EKOTÜRK',
    'dunya.com': 'Dünya',
    'cumhuriyet.com.tr': 'Cumhuriyet',
    'sozcu.com.tr': 'Sözcü',
}
SOURCE_PRIORITY = {
    'aa.com.tr': 12,
    'trthaber.com': 11,
    'enerjigunlugu.net': 10,
    'petroturk.com': 10,
    'puis.org.tr': 10,
    'tabgis.org.tr': 10,
    'bloomberght.com': 9,
    'cnbce.com': 9,
    'ekonomim.com': 8,
    'haberturk.com': 8,
    'ntv.com.tr': 7,
    'enerjipetrolgaz.com': 7,
    'ekoturk.com': 6,
    'dunya.com': 6,
    'cumhuriyet.com.tr': 5,
    'sozcu.com.tr': 5,
    'endeks24.com': 4,
}

QUERIES = (
    'motorin zam bekleniyor indirim bekleniyor zam geldi indirim geldi akaryakıt',
    'benzin zam bekleniyor indirim bekleniyor zam geldi indirim geldi akaryakıt',
    'akaryakıt beklenen zam iptal edildi beklenen indirim iptal edildi',
    'motorin zam gerçekleşti pompaya yansıdı bugün',
    'benzin zam gerçekleşti pompaya yansıdı bugün',
)

UP_PATTERNS = (
    'zam beklen', 'zam yapılması beklen', 'zam yapilmasi beklen',
    'artış beklen', 'artis beklen', 'zam ihtimali', 'zam yolda',
    'artış yolda', 'artis yolda', 'zam gelecek', 'zam geliyor',
)
DOWN_PATTERNS = (
    'indirim beklen', 'indirim yapılması beklen', 'indirim yapilmasi beklen',
    'düşüş beklen', 'dusus beklen', 'indirim yolda', 'indirim gelecek', 'indirim geliyor',
)
CANCEL_PATTERNS = (
    'iptal edildi', 'iptal oldu', 'uygulanmayacak', 'uygulanmadı', 'uygulanmadi',
    'geri çekildi', 'geri cekildi', 'ertelendi', 'devreye alınmadı', 'devreye alinmadi',
)
REALIZED_UP_PATTERNS = (
    'zam geldi', 'zam yapıldı', 'zam yapildi', 'zam gerçekleşti', 'zam gerceklesti',
    'artış gerçekleşti', 'artis gerceklesti', 'fiyatı arttı', 'fiyati artti',
    'zam pompaya yansıdı', 'zam pompaya yansidi', 'artış pompaya yansıdı', 'artis pompaya yansidi',
    'litre fiyatına', 'litre fiyatina',
)
REALIZED_DOWN_PATTERNS = (
    'indirim geldi', 'indirim yapıldı', 'indirim yapildi', 'indirim gerçekleşti', 'indirim gerceklesti',
    'düşüş gerçekleşti', 'dusus gerceklesti', 'fiyatı düştü', 'fiyati dustu',
    'indirim pompaya yansıdı', 'indirim pompaya yansidi',
)

TR_MONTHS = {
    'ocak': 1, 'şubat': 2, 'subat': 2, 'mart': 3, 'nisan': 4, 'mayıs': 5, 'mayis': 5,
    'haziran': 6, 'temmuz': 7, 'ağustos': 8, 'agustos': 8, 'eylül': 9, 'eylul': 9,
    'ekim': 10, 'kasım': 11, 'kasim': 11, 'aralık': 12, 'aralik': 12,
}


def _norm(text):
    return re.sub(r'\s+', ' ', text or '').strip().casefold()


def _host(url):
    try:
        host = (urlsplit(url).hostname or '').lower()
        return host[4:] if host.startswith('www.') else host
    except Exception:
        return ''


def _accepted_host(url):
    host = _host(url)
    for domain in SOURCE_NAMES:
        if host == domain or host.endswith('.' + domain):
            return domain
    return None


def _published(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _date_from_text(text):
    m = re.search(r'\b(\d{1,2})\s+(Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\s+(20\d{2})\b', text or '', re.I)
    if not m:
        return None
    try:
        month = TR_MONTHS[m.group(2).casefold()]
        return datetime(int(m.group(3)), month, int(m.group(1)), 9, 0, tzinfo=timezone.utc)
    except Exception:
        return None


def _rss(query):
    url = 'https://www.bing.com/search?format=rss&q=' + quote_plus(query)
    rows = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=6)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        for node in root.findall('.//item'):
            link = (node.findtext('link') or '').strip()
            title = re.sub(r'\s+', ' ', node.findtext('title') or '').strip()
            desc = re.sub(r'<[^>]+>', ' ', node.findtext('description') or '')
            desc = re.sub(r'\s+', ' ', desc).strip()
            domain = _accepted_host(link)
            if not domain or not title:
                continue
            published = _published(node.findtext('pubDate') or node.findtext('date'))
            if not published:
                published = _date_from_text(title + ' ' + desc)
            rows.append({
                'url': link,
                'title': title,
                'description': desc,
                'published': published,
                'domain': domain,
            })
    except Exception:
        pass
    return rows


def _google_news_rss(query):
    url = (
        'https://news.google.com/rss/search?q=' + quote_plus(query + ' when:2d')
        + '&hl=tr&gl=TR&ceid=TR:tr'
    )
    rows = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=7)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        for node in root.findall('.//item'):
            link = (node.findtext('link') or '').strip()
            title = re.sub(r'\s+', ' ', node.findtext('title') or '').strip()
            desc = re.sub(r'<[^>]+>', ' ', node.findtext('description') or '')
            desc = re.sub(r'\s+', ' ', desc).strip()
            source_node = node.find('source')
            source_url = (source_node.attrib.get('url') or '').strip() if source_node is not None else ''
            domain = _accepted_host(source_url)
            if not domain or not title:
                continue
            published = _published(node.findtext('pubDate') or node.findtext('date'))
            if not published:
                published = _date_from_text(title + ' ' + desc)
            rows.append({
                'url': link,
                'title': title,
                'description': desc,
                'published': published,
                'domain': domain,
                'source': SOURCE_NAMES.get(domain) or (source_node.text.strip() if source_node is not None and source_node.text else domain),
                'discovery': 'google-news-rss',
            })
    except Exception:
        pass
    return rows


def _article_text(row):
    # Direct article enrichment is used only for original publisher URLs.
    url = row.get('url') or ''
    domain = row.get('domain') or ''
    if not url or _accepted_host(url) != domain:
        return ''
    try:
        r = requests.get(url, headers=HEADERS, timeout=5, allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        for bad in soup.find_all(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            bad.decompose()
        blocks = []
        for node in soup.select('article, main, .article-content, .news-content, .content, .icerik, #content'):
            text = re.sub(r'\s+', ' ', node.get_text(' ', strip=True)).strip()
            if 120 <= len(text) <= 15000:
                blocks.append(text)
        if not blocks and soup.body:
            text = re.sub(r'\s+', ' ', soup.body.get_text(' ', strip=True)).strip()
            if 120 <= len(text) <= 12000:
                blocks.append(text)
        return max(blocks, key=len)[:7000] if blocks else ''
    except Exception:
        return ''


def _effective_date(text, published):
    if not published:
        return None
    local = published.astimezone(TR_TZ)
    low = _norm(text)

    # Explicit Turkish date, year optional.
    m = re.search(
        r'\b(\d{1,2})\s+(Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)(?:\s+(20\d{2}))?\b',
        text or '',
        re.I,
    )
    if m:
        try:
            year = int(m.group(3)) if m.group(3) else local.year
            month = TR_MONTHS[m.group(2).casefold()]
            candidate = datetime(year, month, int(m.group(1)), tzinfo=TR_TZ).date()
            # Avoid mistaking an article's own publication date for the effective date
            # unless wording around the text indicates applicability.
            pos = m.start()
            around = _norm((text or '')[max(0, pos-80):m.end()+100])
            if any(x in around for x in ('geçerli', 'itibaren', 'başlayacak', 'baslayacak', 'yansıyacak', 'yansiyacak')):
                return candidate.isoformat()
        except Exception:
            pass

    if 'yarından itibaren' in low or 'yarindan itibaren' in low or 'yarın geçerli' in low or 'yarin geçerli' in low:
        return (local.date() + timedelta(days=1)).isoformat()
    if 'bu gece yarısı' in low or 'bu gece yarisi' in low or 'gece yarısından itibaren' in low or 'gece yarisindan itibaren' in low:
        return (local.date() + timedelta(days=1)).isoformat()
    if 'bugünden itibaren' in low or 'bugunden itibaren' in low:
        return local.date().isoformat()
    return None


def _tr_short_date(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value).date()
    except Exception:
        return None
    names = ['OCAK','ŞUBAT','MART','NİSAN','MAYIS','HAZİRAN','TEMMUZ','AĞUSTOS','EYLÜL','EKİM','KASIM','ARALIK']
    return f'{d.day} {names[d.month-1]}'


def _consensus_event(fuel_rows):
    if not fuel_rows:
        return None, []
    latest = fuel_rows[0]
    status = latest.get('status')
    latest_dt = latest.get('published')
    group = []
    for row in fuel_rows:
        if row.get('status') != status:
            continue
        published = row.get('published')
        if latest_dt and published and abs((latest_dt - published).total_seconds()) > 18 * 3600:
            continue
        if latest.get('amount') is not None and row.get('amount') is not None:
            if abs(float(latest['amount']) - float(row['amount'])) > 0.20:
                continue
        group.append(row)

    domains = []
    sources = []
    for row in group:
        domain = row.get('domain') or ''
        if not domain or domain in domains:
            continue
        domains.append(domain)
        sources.append({
            'name': row.get('source') or SOURCE_NAMES.get(domain) or domain,
            'domain': domain,
            'url': row.get('url'),
            'published_at': row.get('published').isoformat() if row.get('published') else None,
        })

    amounts = [float(r['amount']) for r in group if r.get('amount') is not None]
    effective_dates = [r.get('effective_date') for r in group if r.get('effective_date')]
    amount = round(float(median(amounts)), 2) if amounts else latest.get('amount')
    effective_date = None
    if effective_dates:
        effective_date = max(set(effective_dates), key=lambda x: effective_dates.count(x))

    merged = dict(latest)
    merged['amount'] = amount
    merged['effective_date'] = effective_date or latest.get('effective_date')
    merged['confirmation_count'] = len(domains)
    merged['sources_detail'] = sources
    merged['confidence'] = 'high' if len(domains) >= 3 else ('medium' if len(domains) >= 2 else 'single')
    return merged, group


def _fuel(text):
    low = _norm(text)
    if 'motorin' in low or 'mazot' in low or 'dizel' in low:
        return 'diesel'
    if 'benzin' in low:
        return 'gasoline'
    return None


def _amount(text):
    matches = []
    for m in re.finditer(r'(?<!\d)(\d{1,2}(?:[.,]\d{1,2})?)\s*(TL|₺|lira|liraya|kuruş|kurus)', text or '', re.I):
        try:
            value = float(m.group(1).replace(',', '.'))
        except Exception:
            continue
        unit = m.group(2).casefold()
        if unit in {'kuruş', 'kurus'}:
            value /= 100.0
        if 0.05 <= value <= 20:
            matches.append(value)
    return round(matches[0], 2) if matches else None


def _status(text):
    low = _norm(text)
    cancelled = any(x in low for x in CANCEL_PATTERNS)
    down_exp = any(x in low for x in DOWN_PATTERNS)
    up_exp = any(x in low for x in UP_PATTERNS)
    down_done = any(x in low for x in REALIZED_DOWN_PATTERNS)
    up_done = any(x in low for x in REALIZED_UP_PATTERNS)

    # Expectations in the same sentence take precedence over generic wording such as
    # "litre fiyatına ... zam gelmesi bekleniyor".
    if cancelled:
        if 'indirim' in low or 'düşüş' in low or 'dusus' in low:
            return 'cancel_down'
        return 'cancel_up'
    if down_exp:
        return 'down'
    if up_exp:
        return 'up'
    if down_done:
        return 'realized_down'
    if up_done and ('zam' in low or 'artış' in low or 'artis' in low):
        return 'realized_up'
    return None


def _event_sentence(text, status):
    sentences = [x.strip() for x in re.split(r'(?<=[.!?])\s+|\s+[|•]\s+', text or '') if x.strip()]
    if status == 'realized_up':
        wanted = REALIZED_UP_PATTERNS
    elif status == 'realized_down':
        wanted = REALIZED_DOWN_PATTERNS
    elif status.startswith('cancel'):
        wanted = CANCEL_PATTERNS
    elif status == 'down':
        wanted = DOWN_PATTERNS
    else:
        wanted = UP_PATTERNS
    for sentence in sentences:
        low = _norm(sentence)
        if any(x in low for x in wanted):
            return sentence
    return text or ''


def _fmt_amount(value):
    return None if value is None else f'{value:.2f}'.replace('.', ',')


def _line(fuel_label, status, amount, effective_date=None):
    amt = _fmt_amount(amount)
    when = _tr_short_date(effective_date)
    suffix = f' · {when}' if when and status in {'up', 'down'} else ''
    if status == 'up':
        return f'🔺 {fuel_label} · {amt + " TL " if amt else ""}ZAM BEKLENİYOR{suffix}'
    if status == 'down':
        return f'🔻 {fuel_label} · {amt + " TL " if amt else ""}İNDİRİM BEKLENİYOR{suffix}'
    if status == 'realized_up':
        return f'✅ {fuel_label} · {amt + " TL " if amt else ""}ZAM GERÇEKLEŞTİ'
    if status == 'realized_down':
        return f'✅ {fuel_label} · {amt + " TL " if amt else ""}İNDİRİM GERÇEKLEŞTİ'
    if status == 'cancel_down':
        return f'⏸️ {fuel_label} · BEKLENEN İNDİRİM İPTAL EDİLDİ'
    if status == 'cancel_up':
        return f'⏸️ {fuel_label} · BEKLENEN ZAM İPTAL EDİLDİ'
    return f'ℹ️ {fuel_label} · ZAM/İNDİRİM BEKLENTİSİ BULUNMUYOR'


def _read_json(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _pump_realized(fuel_key, expected_status, published, price_data):
    label = 'Motorin' if fuel_key == 'diesel' else 'Benzin 95'
    direction = 1 if expected_status == 'up' else -1
    after = published.isoformat() if published else ''
    candidates = []
    for c in price_data.get('changes') or []:
        if c.get('fuel') != label:
            continue
        if after and c.get('detected_at', '') < after:
            continue
        delta = float(c.get('delta') or 0)
        if delta * direction <= 0:
            continue
        candidates.append(c)
    candidates.sort(key=lambda x: x.get('detected_at', ''), reverse=True)
    return candidates[0] if candidates else None


def scan_price_expectation(saved=None, price_data=None):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=2)
    saved = saved if isinstance(saved, dict) else _read_json(EXPECTATION_DATA)
    price_data = price_data if isinstance(price_data, dict) else _read_json(PRICE_DATA)

    candidates = []
    with ThreadPoolExecutor(max_workers=max(8, len(QUERIES) * 2)) as pool:
        jobs = []
        for q in QUERIES:
            jobs.append(pool.submit(_rss, q))
            jobs.append(pool.submit(_google_news_rss, q))
        for job in as_completed(jobs):
            candidates.extend(job.result())

    rows = []
    seen = set()
    for row in candidates:
        text = row['title'] + '. ' + row['description']
        fuel_key = _fuel(text)
        if not fuel_key:
            continue
        published = row.get('published')
        # Undated search-index results are allowed only when they contain today's
        # Turkish date; this avoids reviving old expectations.
        if not published:
            continue
        if published < cutoff:
            continue
        status = _status(text)
        amount = None
        article_text = ''
        if status:
            sentence = _event_sentence(text, status)
            amount = _amount(sentence) or _amount(row['title']) or _amount(row['description'])
        # If the search result is terse, inspect the original publisher page.
        if (not status or amount is None) and _accepted_host(row.get('url') or ''):
            article_text = _article_text(row)
            if article_text:
                enriched_text = text + '. ' + article_text
                status = status or _status(enriched_text)
                if status:
                    sentence = _event_sentence(enriched_text, status)
                    amount = amount or _amount(sentence) or _amount(article_text)
                    text = enriched_text
        if not status:
            continue
        if status in {'up', 'down'} and amount is None:
            # Amount-less reports can still corroborate an event when the publisher is trusted.
            if SOURCE_PRIORITY.get(row['domain'], 0) < 5:
                continue
        effective_date = _effective_date(text, published)
        dedup_title = re.sub(r'\s+-\s+[^-]{2,40}$', '', row['title']).casefold()
        key = (row['domain'], dedup_title, fuel_key, status)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            **row,
            'fuel_key': fuel_key,
            'status': status,
            'amount': amount,
            'effective_date': effective_date,
        })

    status_rank = {
        'realized_up': 5, 'realized_down': 5,
        'cancel_up': 4, 'cancel_down': 4,
        'up': 3, 'down': 3,
    }
    rows.sort(key=lambda x: (
        x.get('published') or datetime(1970, 1, 1, tzinfo=timezone.utc),
        status_rank.get(x['status'], 0),
        SOURCE_PRIORITY.get(x['domain'], 0),
    ), reverse=True)

    items = []
    for fuel_key, fuel_label in [('diesel', 'MOTORİN'), ('gasoline', 'BENZİN')]:
        fuel_rows = [x for x in rows if x['fuel_key'] == fuel_key]
        item, supporting_rows = _consensus_event(fuel_rows)

        # If a stored expectation has subsequently appeared in pump-change history,
        # upgrade it even when the news search index has not yet refreshed.
        previous = next((x for x in (saved.get('items') or []) if x.get('fuel_key') == fuel_key), None)
        if item and item['status'] in {'up', 'down'}:
            realized = _pump_realized(fuel_key, item['status'], item.get('published'), price_data)
            if realized:
                item = dict(item)
                item['status'] = 'realized_up' if float(realized.get('delta') or 0) > 0 else 'realized_down'
                item['amount'] = abs(float(realized.get('delta') or 0))
                item['source'] = realized.get('source') or 'Pompa fiyat takibi'
                item['url'] = realized.get('source_url') or item.get('url')
                item['effective_date'] = None
        elif not item and previous and previous.get('status') in {'up', 'down'}:
            try:
                prev_dt = datetime.fromisoformat(previous.get('published_at'))
            except Exception:
                prev_dt = None
            realized = _pump_realized(fuel_key, previous['status'], prev_dt, price_data)
            if realized:
                item = {
                    'status': 'realized_up' if float(realized.get('delta') or 0) > 0 else 'realized_down',
                    'amount': abs(float(realized.get('delta') or 0)),
                    'published': datetime.fromisoformat(realized['detected_at']),
                    'domain': '',
                    'url': realized.get('source_url') or '',
                    'source': realized.get('source') or 'Pompa fiyat takibi',
                }

        if not item:
            items.append({
                'fuel_key': fuel_key,
                'fuel': fuel_label,
                'status': 'none',
                'line': _line(fuel_label, 'none', None),
                'amount': None,
                'source': None,
                'url': None,
                'published_at': None,
                'official': False,
                'label': 'Güncel durum',
                'effective_date': None,
                'confirmation_count': 0,
                'sources_detail': [],
                'confidence': 'none',
            })
            continue

        source = item.get('source') or SOURCE_NAMES.get(item.get('domain')) or 'Haber kaynağı'
        items.append({
            'fuel_key': fuel_key,
            'fuel': fuel_label,
            'status': item['status'],
            'line': _line(fuel_label, item['status'], item.get('amount'), item.get('effective_date')),
            'amount': item.get('amount'),
            'source': source,
            'url': item.get('url'),
            'published_at': item.get('published').isoformat() if item.get('published') else None,
            'official': False,
            'label': 'Gerçekleşen fiyat hareketi' if item['status'].startswith('realized') else ('Güncelleme' if item['status'].startswith('cancel') else 'Sektör beklentisi'),
            'effective_date': item.get('effective_date'),
            'confirmation_count': int(item.get('confirmation_count') or 1),
            'sources_detail': item.get('sources_detail') or [],
            'confidence': item.get('confidence') or 'single',
        })

    return {
        'found': any(x['status'] != 'none' for x in items),
        'checked_at': now.isoformat(),
        'items': items,
        'no_expectation_text': 'ZAM/İNDİRİM BEKLENTİSİ BULUNMUYOR',
        'next_daily_check': '12:00 Europe/Istanbul',
        'discovery': ['bing_rss', 'google_news_rss'],
        'consensus_window_hours': 18,
    }


def write_price_expectation():
    data = scan_price_expectation()
    EXPECTATION_DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


if __name__ == '__main__':
    write_price_expectation()
