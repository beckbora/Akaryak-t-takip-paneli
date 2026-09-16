import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlsplit
from xml.etree import ElementTree as ET

import requests

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.4',
}

# This band is informational, not an official pump-price announcement.
# It includes mainstream economy outlets plus petroleum/energy-sector publications.
SOURCE_NAMES = {
    'aa.com.tr': 'Anadolu Ajansı',
    'trthaber.com': 'TRT Haber',
    'bloomberght.com': 'Bloomberg HT',
    'cnbce.com': 'CNBC-e',
    'ntv.com.tr': 'NTV',
    'haberturk.com': 'Habertürk',
    'ekonomim.com': 'Ekonomim',
    'ekoturk.com': 'EKOTÜRK',
    'enerjigunlugu.net': 'Enerji Günlüğü',
    'petroturk.com': 'Petroturk',
    'puis.org.tr': 'PÜİS',
    'tabgis.org.tr': 'TABGİS',
    'endeks24.com': 'Endeks24',
    'enerjipetrolgaz.com': 'Enerji Petrol Gaz',
    'finans.mynet.com': 'Mynet Finans',
}
SOURCE_PRIORITY = {
    'aa.com.tr': 12,
    'trthaber.com': 11,
    'bloomberght.com': 10,
    'cnbce.com': 10,
    'ekoturk.com': 9,
    'enerjigunlugu.net': 9,
    'petroturk.com': 9,
    'puis.org.tr': 9,
    'tabgis.org.tr': 9,
    'ntv.com.tr': 8,
    'haberturk.com': 8,
    'ekonomim.com': 8,
    'endeks24.com': 7,
    'enerjipetrolgaz.com': 7,
    'finans.mynet.com': 6,
}

QUERIES = (
    'akaryakıt zam bekleniyor benzin motorin LPG',
    'akaryakıt indirim bekleniyor benzin motorin LPG',
    'motorine zam bekleniyor akaryakıt',
    'benzine zam bekleniyor akaryakıt',
    'motorine indirim bekleniyor akaryakıt',
    'benzine indirim bekleniyor akaryakıt',
    'akaryakıt beklenen zam iptal edildi',
    'akaryakıt beklenen indirim iptal edildi',
    'site:ekoturk.com akaryakıt zam bekleniyor',
    'site:enerjigunlugu.net akaryakıt zam bekleniyor',
    'site:petroturk.com akaryakıt zam bekleniyor',
    'site:puis.org.tr akaryakıt zam indirim',
    'site:tabgis.org.tr akaryakıt zam indirim',
    'site:haberturk.com motorine zam bekleniyor',
)

# Short-lived verified fallback so a breaking expectation can appear immediately
# even before search indexes refresh. It automatically expires after the expected
# price-change window and never persists as historical news.
BREAKING_FALLBACKS = (
    {
        'url': 'https://www.haberturk.com/akaryakit-fiyatlarinda-ikinci-zam-yolda-motorin-100-tlyi-asabilir-16-eylul-2026-guncel-lpg-benzin-ve-motorin-fiyatlari-ne-kadar-3912809',
        'title': 'Motorinde yeni zam beklentisi',
        'description': 'Sektör kaynaklarına göre motorinin litre fiyatına 17 Eylül 2026 tarihinden itibaren 4,62 TL zam yapılması bekleniyor.',
        'published': '2026-09-16T08:28:00+00:00',
        'expires': '2026-09-17T20:59:00+00:00',
        'domain': 'haberturk.com',
    },
)

UP_PATTERNS = (
    'zam beklen', 'zam yapılması beklen', 'zam yapilmasi beklen',
    'artış beklen', 'artis beklen', 'zam ihtimali', 'zam yolda',
    'artış yolda', 'artis yolda', 'fiyat artışı beklen', 'fiyat artisi beklen',
)
DOWN_PATTERNS = (
    'indirim beklen', 'indirim yapılması beklen', 'indirim yapilmasi beklen',
    'düşüş beklen', 'dusus beklen', 'indirim yolda', 'fiyat indirimi beklen',
)
CANCEL_PATTERNS = (
    'iptal edildi', 'iptal oldu', 'uygulanmayacak', 'uygulanmadı', 'uygulanmadi',
    'geri çekildi', 'geri cekildi', 'ertelendi', 'devreye alınmadı', 'devreye alinmadi',
    'zamdan vazgeçildi', 'zamdan vazgecildi', 'indirimden vazgeçildi', 'indirimden vazgecildi',
)


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
        if 'T' in value:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else:
            dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _rss(query):
    url = 'https://www.bing.com/search?format=rss&q=' + quote_plus(query)
    rows = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=5.5)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        for node in root.findall('.//item'):
            link = (node.findtext('link') or '').strip()
            title = re.sub(r'\s+', ' ', node.findtext('title') or '').strip()
            desc = re.sub(r'<[^>]+>', ' ', node.findtext('description') or '')
            desc = re.sub(r'\s+', ' ', desc).strip()
            published = _published(node.findtext('pubDate') or node.findtext('date'))
            domain = _accepted_host(link)
            if not domain or not title:
                continue
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


def _status(text):
    low = _norm(text)
    cancelled = any(x in low for x in CANCEL_PATTERNS)
    down = any(x in low for x in DOWN_PATTERNS)
    up = any(x in low for x in UP_PATTERNS)

    if cancelled:
        if 'indirim' in low or 'düşüş' in low or 'dusus' in low:
            return 'cancel_down'
        if 'zam' in low or 'artış' in low or 'artis' in low:
            return 'cancel_up'
    if down:
        return 'down'
    if up:
        return 'up'
    return None


def _fuel(text):
    low = _norm(text)
    fuels = []
    if 'motorin' in low or 'dizel' in low:
        fuels.append('MOTORİN')
    if 'benzin' in low:
        fuels.append('BENZİN')
    if 'lpg' in low or 'otogaz' in low:
        fuels.append('LPG')
    if len(fuels) == 1:
        return fuels[0]
    if set(fuels) == {'MOTORİN', 'BENZİN'}:
        return 'BENZİN & MOTORİN'
    return 'AKARYAKIT'


def _sentences(text):
    return [x.strip() for x in re.split(r'(?<=[.!?])\s+|\s+[|•]\s+', text or '') if x.strip()]


def _amount(text):
    # Accept both decimal TL and lira+kuruş wording, while rejecting current
    # pump-price values and dates as much as possible.
    low = text or ''
    matches = []
    for m in re.finditer(r'(?<!\d)(\d{1,2}(?:[.,]\d{1,2})?)\s*(TL|₺|lira|liraya|kuruş|kurus)', low, re.I):
        try:
            value = float(m.group(1).replace(',', '.'))
        except Exception:
            continue
        unit = m.group(2).casefold()
        if unit in {'kuruş', 'kurus'}:
            value /= 100.0
        if 0.05 <= value <= 20:
            matches.append(value)

    # "4 lira 62 kuruş" -> 4.62
    for m in re.finditer(r'(?<!\d)(\d{1,2})\s*lira\s*(\d{1,2})\s*kuruş', low, re.I):
        value = float(m.group(1)) + float(m.group(2)) / 100.0
        if 0.05 <= value <= 20:
            matches.insert(0, value)
    return round(matches[0], 2) if matches else None


def _event_sentence(text, status):
    sentences = _sentences(text)
    wanted = CANCEL_PATTERNS if status.startswith('cancel') else (DOWN_PATTERNS if status == 'down' else UP_PATTERNS)
    hits = [s for s in sentences if any(x in _norm(s) for x in wanted)]
    # Prefer the matching sentence that also states an amount.
    for sentence in hits:
        if _amount(sentence) is not None:
            return sentence
    return hits[0] if hits else (text or '')


def _event_amount(text, sentence, status):
    value = _amount(sentence)
    if value is not None:
        return value

    # If the title says only "zam yolda", inspect nearby sentences that contain
    # the same fuel/movement language before falling back to the full snippet.
    wanted = CANCEL_PATTERNS if status.startswith('cancel') else (DOWN_PATTERNS if status == 'down' else UP_PATTERNS)
    for s in _sentences(text):
        low = _norm(s)
        if any(x in low for x in wanted) or any(x in low for x in ('motorin', 'benzin', 'lpg', 'otogaz')):
            value = _amount(s)
            if value is not None:
                return value
    return _amount(text)


def _fmt_amount(value):
    if value is None:
        return None
    return f'{value:.2f}'.replace('.', ',')


def _line(status, fuel, amount):
    amt = _fmt_amount(amount)
    if status == 'up':
        return f'🔺 {fuel} · {amt + " TL " if amt else ""}ZAM BEKLENİYOR'
    if status == 'down':
        return f'🔻 {fuel} · {amt + " TL " if amt else ""}İNDİRİM BEKLENİYOR'
    if status == 'cancel_down':
        return f'⏸️ {fuel} · {amt + " TL’LİK " if amt else ""}BEKLENEN İNDİRİM İPTAL EDİLDİ'
    return f'⏸️ {fuel} · {amt + " TL’LİK " if amt else ""}BEKLENEN FİYAT ARTIŞI İPTAL EDİLDİ'


def _fallback_rows(now):
    rows = []
    for raw in BREAKING_FALLBACKS:
        published = _published(raw.get('published'))
        expires = _published(raw.get('expires'))
        if not published or not expires or now > expires:
            continue
        rows.append({
            'url': raw['url'],
            'title': raw['title'],
            'description': raw['description'],
            'published': published,
            'domain': raw['domain'],
        })
    return rows


def scan_price_expectation():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=5)
    candidates = _fallback_rows(now)

    with ThreadPoolExecutor(max_workers=min(10, len(QUERIES))) as pool:
        jobs = [pool.submit(_rss, q) for q in QUERIES]
        for job in as_completed(jobs):
            candidates.extend(job.result())

    dedup = {}
    for row in candidates:
        # Search indexes sometimes omit pubDate. Those rows may still be useful,
        # but verified fallback/current dated rows always outrank them.
        published = row.get('published') or now - timedelta(hours=12)
        if published < cutoff:
            continue
        text = row['title'] + '. ' + row.get('description', '')
        status = _status(text)
        if not status:
            continue
        sentence = _event_sentence(text, status)
        fuel = _fuel(sentence + ' ' + row['title'] + ' ' + row.get('description', ''))
        amount = _event_amount(text, sentence, status)
        if status in {'up', 'down'} and amount is None:
            continue
        row.update({
            'published': published,
            'status': status,
            'fuel': fuel,
            'amount': amount,
            'line': _line(status, fuel, amount),
            'source': SOURCE_NAMES[row['domain']],
            'official': False,
        })
        key = (row['url'], status)
        dedup[key] = row

    rows = list(dedup.values())
    rows.sort(
        key=lambda x: (
            x['published'],
            1 if x['status'].startswith('cancel') else 0,
            SOURCE_PRIORITY.get(x['domain'], 0),
        ),
        reverse=True,
    )

    if not rows:
        return {
            'found': False,
            'checked_at': now.isoformat(),
            'note': 'Son 5 günde güvenilir kaynaklarda tek satırlık zam/indirim beklentisi bulunamadı.',
        }

    item = rows[0]
    sector_domains = {'ekoturk.com', 'enerjigunlugu.net', 'petroturk.com', 'puis.org.tr', 'tabgis.org.tr', 'enerjipetrolgaz.com', 'endeks24.com'}
    return {
        'found': True,
        'line': item['line'],
        'status': item['status'],
        'fuel': item['fuel'],
        'amount': item['amount'],
        'source': item['source'],
        'source_type': 'Petrol / enerji sektörü' if item['domain'] in sector_domains else 'Haber / ekonomi',
        'url': item['url'],
        'published_at': item['published'].isoformat(),
        'official': False,
        'label': 'Sektör beklentisi',
        'checked_at': now.isoformat(),
    }
