import re
from datetime import date, datetime

from scrape import clean

MONTHS = {
    'ocak': 1, 'şubat': 2, 'subat': 2, 'mart': 3, 'nisan': 4, 'mayıs': 5, 'mayis': 5,
    'haziran': 6, 'temmuz': 7, 'ağustos': 8, 'agustos': 8, 'eylül': 9, 'eylul': 9,
    'ekim': 10, 'kasım': 11, 'kasim': 11, 'aralık': 12, 'aralik': 12,
}

DATE_PATTERNS = (
    re.compile(r'\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b'),
    re.compile(
        r'\b(\d{1,2})\s+(Ocak|Şubat|Subat|Mart|Nisan|Mayıs|Mayis|Haziran|Temmuz|Ağustos|Agustos|Eylül|Eylul|Ekim|Kasım|Kasim|Aralık|Aralik)\s+(20\d{2})\b',
        re.I,
    ),
)

DEADLINE_TERMS = (
    'son tarih', 'son gün', 'son gun', 'tarihine kadar', 'tarihe kadar', 'en geç', 'en gec',
    'süre uzat', 'sure uzat', 'yükümlülük', 'yukumluluk', 'zorunlu', 'başvuru', 'basvuru',
    'yürürlüğe', 'yururluge', 'yürürlük', 'yururluk', 'itibaren', 'itibarıyla', 'itibariyla',
    'tamamlanması', 'tamamlanmasi', 'geçiş', 'gecis', 'uzatılması', 'uzatilmasi',
    'taktırılması', 'taktirilmasi', 'gerekmektedir',
)


def _to_date(match):
    try:
        if match.re is DATE_PATTERNS[0]:
            d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
        else:
            d = int(match.group(1))
            m = MONTHS[match.group(2).casefold()]
            y = int(match.group(3))
        return date(y, m, d)
    except Exception:
        return None


def _all_dates(text):
    out = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text or ''):
            parsed = _to_date(match)
            if parsed:
                out.append((parsed, match.start(), match.end(), match.group(0)))
    return sorted(out, key=lambda x: x[1])


def _direct_text(item):
    return clean(f"{item.get('title') or ''} {item.get('source_excerpt') or ''}")


def build_deadlines(items, today=None, horizon_days=730):
    today = today or datetime.utcnow().date()
    deadlines = {}
    for item in items or []:
        if not item.get('official'):
            continue
        text = _direct_text(item)
        if not text:
            continue
        low = text.casefold()
        for target, start, end, literal in _all_dates(text):
            days_left = (target - today).days
            if days_left < 0 or days_left > horizon_days:
                continue
            left = max(0, start - 150)
            right = min(len(text), end + 190)
            context = clean(text[left:right])
            context_low = context.casefold()
            if not any(term in context_low for term in DEADLINE_TERMS):
                continue
            key = (target.isoformat(), item.get('id') or item.get('url') or item.get('title'))
            deadlines[key] = {
                'date': target.isoformat(),
                'date_text': literal,
                'days_left': days_left,
                'title': item.get('title'),
                'source': item.get('source'),
                'source_name': item.get('source_name') or item.get('source'),
                'category': item.get('category'),
                'url': item.get('url'),
                'context': context,
                'item_id': item.get('id'),
            }
    return sorted(deadlines.values(), key=lambda x: (x['date'], x.get('title') or ''))[:40]


def recent_changes(items, limit=40):
    changed = [
        i for i in (items or [])
        if i.get('changed_at') and (i.get('change_details') or int(i.get('revision') or 0) > 0)
    ]
    changed.sort(key=lambda i: i.get('changed_at') or '', reverse=True)
    return [
        {
            'id': i.get('id'),
            'title': i.get('title'),
            'source': i.get('source'),
            'source_name': i.get('source_name'),
            'url': i.get('url'),
            'changed_at': i.get('changed_at'),
            'revision': i.get('revision', 0),
            'change_details': i.get('change_details') or [],
        }
        for i in changed[:limit]
    ]
