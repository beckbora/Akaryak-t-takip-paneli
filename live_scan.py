import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from scrape import (
    SOURCES, HEADERS, candidates, uid, merge_seen, category, severity,
    make_item, relevant, clean
)

ROOT = Path(__file__).resolve().parent
RG_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.7,en;q=0.5',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def read_saved():
    try:
        return json.loads((ROOT / 'data.json').read_text(encoding='utf-8'))
    except Exception:
        return {'items': [], 'sources': []}


def fetch_one(src):
    now = datetime.now(timezone.utc).isoformat()
    try:
        r = requests.get(src['url'], headers=HEADERS, timeout=8)
        r.raise_for_status()
        items = candidates(src, r.text)
        return items, {
            'source': src['group'], 'source_name': src['name'], 'ok': True,
            'count': len(items), 'checked_at': now,
        }
    except Exception as exc:
        return [], {
            'source': src['group'], 'source_name': src['name'], 'ok': False,
            'count': 0, 'checked_at': now, 'error': str(exc)[:180],
        }


def _parse_rg_mirror(day, html):
    soup = BeautifulSoup(html, 'html.parser')
    official_day_url = f'https://resmigazete.gov.tr/{day:%d.%m.%Y}'
    src = {
        'name': 'Resmî Gazete', 'group': 'Resmî Gazete', 'official': True,
        'url': official_day_url,
    }
    items = []
    seen = set()

    for heading in soup.find_all(['h3', 'h4']):
        title = clean(heading.get_text(' ', strip=True))
        if len(title) < 10:
            continue
        container = heading.find_parent(['article', 'section', 'div']) or heading.parent
        context = clean(container.get_text(' ', strip=True) if container else title)
        if not relevant(f'{title} {context}'):
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)

        href = official_day_url
        if container:
            for a in container.find_all('a', href=True):
                label = clean(a.get_text(' ', strip=True)).casefold()
                if 'resmî gazete' in label or 'resmi gazete' in label:
                    candidate = a.get('href', '')
                    if candidate.startswith('http') and 'resmigazete.gov.tr' in candidate:
                        href = candidate
                        break

        item = make_item(src, title, href, context)
        item['date'] = day.strftime('%Y-%m-%d')
        item['source'] = 'Resmî Gazete'
        item['source_name'] = 'Resmî Gazete'
        item['official'] = True
        item['retrieval'] = 'mirror'
        items.append(item)

    return items


def _fetch_rg_mirror_day(day):
    url = f'https://www.resmigazeteozeti.com/tarih/{day:%Y-%m-%d}'
    try:
        r = requests.get(url, headers=RG_HEADERS, timeout=6)
        if r.status_code == 404:
            return [], False, None
        r.raise_for_status()
        if len(r.text) < 500:
            return [], False, 'short response'
        return _parse_rg_mirror(day, r.text), True, None
    except Exception as exc:
        return [], False, str(exc)[:140]


def resmi_gazete_live(days=14):
    now = datetime.now(timezone.utc)
    today = now.date()
    official_url = f'https://resmigazete.gov.tr/{today:%d.%m.%Y}'

    # Quick official attempt. If Vercel IP is blocked, immediately switch to mirror.
    try:
        r = requests.get(official_url, headers=RG_HEADERS, timeout=2.5, allow_redirects=True)
        if r.ok and len(r.text) > 500:
            src = {'name': 'Resmî Gazete', 'group': 'Resmî Gazete', 'official': True, 'url': official_url}
            items = candidates(src, r.text)
            for item in items:
                item['source'] = 'Resmî Gazete'
                item['source_name'] = 'Resmî Gazete'
                item['official'] = True
                item['retrieval'] = 'official'
                if not item.get('date'):
                    item['date'] = today.strftime('%Y-%m-%d')
            return items, {
                'source': 'Resmî Gazete', 'source_name': 'Resmî Gazete', 'ok': True,
                'count': len(items), 'checked_at': now.isoformat(),
                'access_mode': 'official', 'days_reached': 1,
            }
    except Exception:
        pass

    # Cloud-safe fallback. Recent daily indexes are read in parallel.
    found = []
    reached = 0
    errors = []
    day_list = [today - timedelta(days=i) for i in range(days)]
    with ThreadPoolExecutor(max_workers=7) as pool:
        jobs = {pool.submit(_fetch_rg_mirror_day, day): day for day in day_list}
        for job in as_completed(jobs):
            try:
                items, ok, err = job.result()
                if ok:
                    reached += 1
                    found.extend(items)
                elif err:
                    errors.append(err)
            except Exception as exc:
                errors.append(str(exc)[:140])

    unique = {}
    for item in found:
        unique[(item.get('title', '').casefold(), item.get('date'))] = item
    found = list(unique.values())

    status = {
        'source': 'Resmî Gazete', 'source_name': 'Resmî Gazete',
        'ok': reached > 0, 'count': len(found), 'checked_at': now.isoformat(),
        'access_mode': 'verified_mirror', 'days_reached': reached,
    }
    if reached == 0:
        status['error'] = (errors[0] if errors else 'official and mirror unavailable')[:180]
    else:
        status['note'] = 'Resmî Gazete doğrudan erişimi Vercel tarafında kısıtlı; günlük indeks yedek kaynaktan okunuyor, bağlantılar resmigazete.gov.tr adresine gider.'
    return found, status


def scan_now():
    saved = read_saved()
    existing = {i.get('id'): i for i in saved.get('items', []) if i.get('id')}
    now = datetime.now(timezone.utc).isoformat()
    fresh = []
    statuses = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(fetch_one, src): src for src in SOURCES}
        for job in as_completed(jobs):
            src = jobs[job]
            try:
                items, status = job.result()
            except Exception as exc:
                items, status = [], {
                    'source': src['group'], 'source_name': src['name'], 'ok': False,
                    'count': 0, 'checked_at': now, 'error': str(exc)[:180],
                }
            statuses.append(status)
            for item in items:
                item['source_url'] = src['url']
                item_id = uid(item)
                fresh.append(merge_seen(item, existing.get(item_id), now))

    try:
        rg_items, rg_status = resmi_gazete_live(days=14)
        statuses.append(rg_status)
        for item in rg_items:
            item['source_url'] = 'https://resmigazete.gov.tr/'
            item_id = uid(item)
            fresh.append(merge_seen(item, existing.get(item_id), now))
    except Exception as exc:
        statuses.append({
            'source': 'Resmî Gazete', 'source_name': 'Resmî Gazete', 'ok': False,
            'count': 0, 'checked_at': now, 'error': str(exc)[:180],
        })

    merged = {i['id']: i for i in existing.values()}
    for item in fresh:
        merged[item['id']] = item

    items = list(merged.values())
    for item in items:
        item.setdefault('category', category(f"{item.get('title','')} {item.get('summary','')}"))
        if item.get('severity') not in {'critical', 'important', 'normal'}:
            item['severity'] = severity(f"{item.get('title','')} {item.get('summary','')}")
        item.setdefault('revision', 0)

    items.sort(
        key=lambda x: (x.get('date') or '0000-00-00', x.get('changed_at') or x.get('first_seen') or ''),
        reverse=True,
    )
    return {
        'updated_at': now, 'live': True, 'items': items[:700],
        'sources': statuses, 'version': 6,
    }
