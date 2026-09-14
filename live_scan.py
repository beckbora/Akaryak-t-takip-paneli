import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from scrape import SOURCES, HEADERS, candidates, uid, merge_seen, category, severity

ROOT = Path(__file__).resolve().parent
RG_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0 Safari/537.36',
    'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.7,en;q=0.5',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
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
            'source': src['group'],
            'source_name': src['name'],
            'ok': True,
            'count': len(items),
            'checked_at': now,
        }
    except Exception as exc:
        return [], {
            'source': src['group'],
            'source_name': src['name'],
            'ok': False,
            'count': 0,
            'checked_at': now,
            'error': str(exc)[:180],
        }


def resmi_gazete_live(days=5):
    now = datetime.now(timezone.utc)
    source = {'name': 'Resmî Gazete', 'group': 'Resmî Gazete', 'official': True}
    found = []
    ok_days = 0
    errors = []

    for delta in range(days):
        day = now - timedelta(days=delta)
        ymd = day.strftime('%Y%m%d')
        urls = [
            f'https://resmigazete.gov.tr/{day:%d.%m.%Y}',
            f'https://resmigazete.gov.tr/eskiler/{day:%Y}/{day:%m}/{ymd}.htm',
        ]
        html = None
        used_url = None

        for url in urls:
            try:
                r = requests.get(url, headers=RG_HEADERS, timeout=5, allow_redirects=True)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                if len(r.text) < 500:
                    continue
                html = r.text
                used_url = r.url or url
                break
            except Exception as exc:
                errors.append(f'{url}: {str(exc)[:120]}')

        if not html:
            continue

        ok_days += 1
        src = {**source, 'url': used_url}
        for item in candidates(src, html):
            item['source'] = 'Resmî Gazete'
            item['source_name'] = 'Resmî Gazete'
            item['official'] = True
            if not item.get('date'):
                item['date'] = day.strftime('%Y-%m-%d')
            found.append(item)

    status = {
        'source': 'Resmî Gazete',
        'source_name': 'Resmî Gazete',
        'ok': ok_days > 0,
        'count': len(found),
        'checked_at': now.isoformat(),
        'days_reached': ok_days,
    }
    if ok_days == 0 and errors:
        status['error'] = errors[0][:180]
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
                    'source': src['group'],
                    'source_name': src['name'],
                    'ok': False,
                    'count': 0,
                    'checked_at': now,
                    'error': str(exc)[:180],
                }
            statuses.append(status)
            for item in items:
                item['source_url'] = src['url']
                item_id = uid(item)
                fresh.append(merge_seen(item, existing.get(item_id), now))

    try:
        rg_items, rg_status = resmi_gazete_live(days=5)
        statuses.append(rg_status)
        for item in rg_items:
            item['source_url'] = 'https://resmigazete.gov.tr/'
            item_id = uid(item)
            fresh.append(merge_seen(item, existing.get(item_id), now))
    except Exception as exc:
        statuses.append({
            'source': 'Resmî Gazete',
            'source_name': 'Resmî Gazete',
            'ok': False,
            'count': 0,
            'checked_at': now,
            'error': str(exc)[:180],
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
        key=lambda x: (
            x.get('date') or '0000-00-00',
            x.get('changed_at') or x.get('first_seen') or '',
        ),
        reverse=True,
    )
    return {
        'updated_at': now,
        'live': True,
        'items': items[:700],
        'sources': statuses,
        'version': 4,
    }
