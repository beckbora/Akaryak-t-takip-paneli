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

# The band is informational, not an official price announcement. Only established
# news outlets are accepted and only the single newest expectation/update is shown.
SOURCE_NAMES = {
    'aa.com.tr': 'Anadolu Ajansı',
    'trthaber.com': 'TRT Haber',
    'bloomberght.com': 'Bloomberg HT',
    'cnbce.com': 'CNBC-e',
    'ntv.com.tr': 'NTV',
    'haberturk.com': 'Habertürk',
    'ekonomim.com': 'Ekonomim',
}
SOURCE_PRIORITY = {
    'aa.com.tr': 7,
    'trthaber.com': 6,
    'bloomberght.com': 5,
    'cnbce.com': 5,
    'ntv.com.tr': 4,
    'haberturk.com': 4,
    'ekonomim.com': 3,
}

QUERIES = (
    'akaryakıt "zam bekleniyor" benzin motorin LPG',
    'akaryakıt "indirim bekleniyor" benzin motorin LPG',
    'akaryakıt "beklenen zam" "iptal edildi"',
    'akaryakıt "beklenen indirim" "iptal edildi"',
)

UP_PATTERNS = (
    'zam beklen', 'zam yapılması beklen', 'zam yapilmasi beklen',
    'artış beklen', 'artis beklen', 'zam ihtimali', 'zam yolda',
    'artış yolda', 'artis yolda',
)
DOWN_PATTERNS = (
    'indirim beklen', 'indirim yapılması beklen', 'indirim yapilmasi beklen',
    'düşüş beklen', 'dusus beklen', 'indirim yolda',
)
CANCEL_PATTERNS = (
    'iptal edildi', 'iptal oldu', 'uygulanmayacak', 'uygulanmadı', 'uygulanmadi',
    'geri çekildi', 'geri cekildi', 'ertelendi', 'devreye alınmadı', 'devreye alinmadi',
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
            if not domain or not title or not published:
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
        # Preserve which expected movement was cancelled when possible.
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


def _event_sentence(text, status):
    sentences = [x.strip() for x in re.split(r'(?<=[.!?])\s+|\s+[|•]\s+', text or '') if x.strip()]
    wanted = CANCEL_PATTERNS if status.startswith('cancel') else (DOWN_PATTERNS if status == 'down' else UP_PATTERNS)
    for sentence in sentences:
        low = _norm(sentence)
        if any(x in low for x in wanted):
            return sentence
    return text or ''


def _amount(text):
    # Only accept plausible per-litre movement amounts to avoid mistaking current
    # pump prices or dates for an expected change amount.
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


def scan_price_expectation():
    cutoff = datetime.now(timezone.utc) - timedelta(days=5)
    candidates = []

    with ThreadPoolExecutor(max_workers=len(QUERIES)) as pool:
        jobs = [pool.submit(_rss, q) for q in QUERIES]
        for job in as_completed(jobs):
            candidates.extend(job.result())

    dedup = {}
    for row in candidates:
        if row['published'] < cutoff:
            continue
        text = row['title'] + '. ' + row['description']
        status = _status(text)
        if not status:
            continue
        sentence = _event_sentence(text, status)
        fuel = _fuel(sentence + ' ' + row['title'])
        amount = _amount(sentence)
        # Expectations should normally carry an amount. Cancellation updates are
        # still useful even when the new article does not repeat the old amount.
        if status in {'up', 'down'} and amount is None:
            continue
        row.update({
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
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'note': 'Son 5 günde doğrulanabilir tek satırlık zam/indirim beklentisi bulunamadı.',
        }

    item = rows[0]
    return {
        'found': True,
        'line': item['line'],
        'status': item['status'],
        'fuel': item['fuel'],
        'amount': item['amount'],
        'source': item['source'],
        'url': item['url'],
        'published_at': item['published'].isoformat(),
        'official': False,
        'label': 'Sektör beklentisi',
        'checked_at': datetime.now(timezone.utc).isoformat(),
    }
