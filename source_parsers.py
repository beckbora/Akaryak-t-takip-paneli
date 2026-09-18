import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from scrape import (
    canonical_url, candidates, clean, make_item, parse_date, relevant, source_relevant
)

MONTH_NAMES = {
    'ocak': 1, 'şubat': 2, 'subat': 2, 'mart': 3, 'nisan': 4, 'mayıs': 5, 'mayis': 5,
    'haziran': 6, 'temmuz': 7, 'ağustos': 8, 'agustos': 8, 'eylül': 9, 'eylul': 9,
    'ekim': 10, 'kasım': 11, 'kasim': 11, 'aralık': 12, 'aralik': 12,
}

PARSER_KEYS = {
    'epdk_petrol_istatistik': 'epdk_report',
    'epdk_lpg_istatistik': 'epdk_report',
    'epdk_fiyatlandirma': 'epdk_pricing',
    'epdk_petrol_lisans': 'epdk_license',
    'epdk_lpg_lisans': 'epdk_license',
    'gib': 'gib_archive',
}


def _small_context(node, max_len=1800):
    best = clean(node.get_text(' ', strip=True)) if hasattr(node, 'get_text') else ''
    parent = getattr(node, 'parent', None)
    while parent is not None:
        if getattr(parent, 'name', None) in {'body', 'html'}:
            break
        text = clean(parent.get_text(' ', strip=True)) if hasattr(parent, 'get_text') else ''
        if len(text) > max_len:
            break
        low = text.casefold()
        report_hits = low.count('sektör raporu') + low.count('fiyatlandırma raporu')
        # Bir üst kapsayıcı birden fazla kayıt barındırıyorsa komşu duyuruların
        # metnini bu kayda taşımadan burada dur.
        if report_hits > 2:
            break
        if len(text) >= len(best):
            best = text
        if 'yayınlanma tarihi' in low or 'revizyon tarihi' in low or 'revizyon kapsamı' in low:
            break
        parent = getattr(parent, 'parent', None)
    return best


def _official_excerpt(item, context):
    excerpt = clean(context)[:1100]
    item['source_excerpt'] = excerpt
    item['summary'] = excerpt
    item['description_origin'] = 'official_source' if excerpt else 'title_only'
    return item


def _published_date(context):
    patterns = (
        r'(?:İlk\s+)?Yayınlanma\s+Tarihi\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]20\d{2})',
        r'Revizyon\s+Tarihi\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]20\d{2})',
    )
    for pattern in patterns:
        m = re.search(pattern, context or '', flags=re.I)
        if m:
            parsed = parse_date(m.group(1))
            if parsed:
                return parsed
    return parse_date(context)


def _report_period(title):
    m = re.search(
        r'\b(20\d{2})\s+(Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\b',
        title or '',
        flags=re.I,
    )
    if not m:
        return None
    month = MONTH_NAMES.get(m.group(2).casefold())
    return f'{m.group(1)}-{month:02d}' if month else None


def _item_from_node(source, node, title, href=None, context=None):
    context = clean(context or _small_context(node))
    href = canonical_url(urljoin(source['url'], href or source['url']))
    item = make_item(source, title, href, title)
    published = _published_date(context)
    if published:
        item['date'] = published
    period = _report_period(title)
    if period:
        item['report_period'] = period
    item['source_url'] = source['url']
    return _official_excerpt(item, context)


def parse_epdk_report(source, html):
    soup = BeautifulSoup(html, 'html.parser')
    market_term = 'lpg' if source.get('market') == 'LPG' else 'petrol'
    found = {}
    for node in soup.find_all(['a', 'h2', 'h3', 'h4', 'h5', 'button']):
        title = clean(node.get_text(' ', strip=True))
        low = title.casefold()
        if len(title) < 18 or 'sektör raporu' not in low:
            continue
        if market_term not in low and not (market_term == 'lpg' and 'sıvılaştırılmış petrol gaz' in low):
            continue
        href = node.get('href') if getattr(node, 'name', None) == 'a' else None
        context = _small_context(node, 2200)
        item = _item_from_node(source, node, title, href=href, context=context)
        item['epdk_focus'] = True
        item['record_type'] = 'Sektör Raporu'
        key = (item['title'].casefold(), item.get('report_period') or '', item.get('url') or '')
        found[key] = item
    return list(found.values())[:100]


def parse_epdk_pricing(source, html):
    soup = BeautifulSoup(html, 'html.parser')
    found = {}
    for node in soup.find_all(['a', 'h2', 'h3', 'h4', 'button']):
        title = clean(node.get_text(' ', strip=True))
        if len(title) < 18 or 'fiyatlandırma raporu' not in title.casefold():
            continue
        href = node.get('href') if getattr(node, 'name', None) == 'a' else None
        item = _item_from_node(source, node, title, href=href, context=_small_context(node, 1500))
        item['epdk_focus'] = True
        item['record_type'] = 'Fiyatlandırma Raporu'
        key = (item['title'].casefold(), item.get('report_period') or '', item.get('url') or '')
        found[key] = item
    return list(found.values())[:80]


def parse_epdk_license(source, html):
    soup = BeautifulSoup(html, 'html.parser')
    found = {}
    for row in soup.find_all('tr'):
        text = clean(row.get_text(' ', strip=True))
        if len(text) < 12:
            continue
        cells = [clean(c.get_text(' ', strip=True)) for c in row.find_all(['td', 'th'])]
        if not cells or not any(re.search(r'\b20\d{2}\b', c) for c in cells):
            continue
        if not any(name in text.casefold() for name in MONTH_NAMES):
            continue
        subject = next((c for c in reversed(cells) if re.search(r'\b20\d{2}\b', c) and len(c) <= 80), '')
        if not subject:
            continue
        title = f"{source.get('market', 'EPDK')} Piyasası Aylık Toplu Lisanslar · {subject}"
        link = row.find('a', href=True)
        href = link.get('href') if link else source['url']
        item = _item_from_node(source, row, title, href=href, context=text)
        item['date'] = parse_date(text) or item.get('date')
        item['epdk_focus'] = True
        item['record_type'] = 'Aylık Toplu Lisans'
        found[(subject.casefold(), item.get('date') or '')] = item
    return list(found.values())[:80]


def parse_gib_archive(source, html):
    soup = BeautifulSoup(html, 'html.parser')
    found = {}
    for a in soup.find_all('a', href=True):
        title = clean(a.get_text(' ', strip=True))
        if len(title) < 12 or not relevant(title):
            continue
        href = canonical_url(urljoin(source['url'], a['href']))
        parent = a.find_parent(['li', 'tr', 'article'])
        date_text = clean(parent.get_text(' ', strip=True)) if parent else title
        item = make_item(source, title, href, title)
        item['date'] = parse_date(date_text) or parse_date(title)
        item['source_url'] = source['url']
        item['source_excerpt'] = ''
        item['summary'] = ''
        item['description_origin'] = 'title_only'
        found[(title.casefold(), href)] = item
    return list(found.values())[:100]


def parse_epdk_focus(source, html):
    soup = BeautifulSoup(html, 'html.parser')
    found = {}
    for a in soup.find_all('a', href=True):
        title = clean(a.get_text(' ', strip=True))
        if len(title) < 10:
            continue
        parent = a.find_parent(['tr', 'li', 'article'])
        context = clean(parent.get_text(' ', strip=True)) if parent else title
        if not source_relevant(source, f'{title} {context}'):
            continue
        item = _item_from_node(source, a, title, href=a.get('href'), context=context)
        item['epdk_focus'] = True
        found[(item['title'].casefold(), item.get('url') or '')] = item
    return list(found.values())[:120]


def parse_source(source, html):
    parser = source.get('parser') or PARSER_KEYS.get(source.get('key'))
    if parser == 'epdk_report':
        return parse_epdk_report(source, html)
    if parser == 'epdk_pricing':
        return parse_epdk_pricing(source, html)
    if parser == 'epdk_license':
        return parse_epdk_license(source, html)
    if parser == 'gib_archive':
        return parse_gib_archive(source, html)
    if source.get('epdk_focus'):
        return parse_epdk_focus(source, html)
    return candidates(source, html)
