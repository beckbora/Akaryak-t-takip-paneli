import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from scrape import (
    SOURCES,
    HEADERS,
    KEYWORDS,
    candidates,
    uid,
    merge_seen,
    category,
    severity,
)

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


def _plain_markdown_line(raw):
    line = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', raw)
    line = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', line)
    line = re.sub(r'^[#>*\-\s]+', '', line)
    line = re.sub(r'\s+', ' ', line).strip()
    return line


def _rg_markdown_items(text, day, direct_url):
    source = {'name': 'Resmî Gazete', 'group': 'Resmî Gazete', 'official': True}
    out = []
    seen = set()
    for raw in text.splitlines():
        title = _plain_markdown_line(raw)
        if len(title) < 18 or len(title) > 520:
            continue
        low = title.casefold()
        if not any(k in low for k in KEYWORDS):
            continue
        if title in seen:
            continue
        seen.add(title)
        link_match = re.search(r'\[[^\]]+\]\((https?://[^)]+)\)', raw)
        href = link_match.group(1) if link_match else direct_url
        item = {
            'title': title[:260],
            'url': href,
            'date': day.strftime('%Y-%m-%d'),
            'summary': title[:700],
            'source': 'Resmî Gazete',
            'source_name': 'Resmî Gazete',
            'official': True,
            'severity': severity(title),
            'category': category(title),
        }
        out.append(item)
    return out[:60]


def _fetch_rg_day(day):
    ymd = day.strftime('%Y%m%d')
    direct_urls = [
        f'https://resmigazete.gov.tr/{day:%d.%m.%Y}',
        f'https://www.resmigazete.gov.tr/{day:%d.%m.%Y}',
        f'https://resmigazete.gov.tr/eskiler/{day:%Y}/{day:%m}/{ymd}.htm',
    ]
    errors = []

    # 1) Direct official-site attempt.
    for url in direct_urls:
        try:
            r = requests.get(url, headers=RG_HEADERS, timeout=3.2, allow_redirects=True)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            if len(r.text) < 500:
                continue
            src = {'name': 'Resmî Gazete', 'group': 'Resmî Gazete', 'official': True, 'url': r.url or url}
            return candidates(src, r.text), (r.url or url), 'direct', errors
        except Exception as exc:
            errors.append(f'direct {url}: {str(exc)[:100]}')

    # 2) Fallback through Jina Reader when cloud IP -> Resmî Gazete times out.
    direct_url = direct_urls[0]
    proxy_urls = [
        f'https://r.jina.ai/https://resmigazete.gov.tr/{day:%d.%m.%Y}',
        f'https://r.jina.ai/http://resmigazete.gov.tr/{day:%d.%m.%Y}',
    ]
    for proxy_url in proxy_urls:
        try:
            r = requests.get(
                proxy_url,
                headers={'User-Agent': RG_HEADERS['User-Agent'], 'Accept': 'text/plain'},
                timeout=8,
            )
            if r.status_code in (404, 422):
                continue
            r.raise_for_status()
            txt = r.text
            lower = txt.casefold()
            if len(txt) < 200 or 'target url returned error 404' in lower:
                continue
            return _rg_markdown_items(txt, day, direct_url), direct_url, 'proxy', errors
        except Exception as exc:
            errors.append(f'proxy {proxy_url}: {str(exc)[:100]}')

    return [], direct_url, None, errors


def resmi_gazete_live(days=11):
    now = datetime.now(timezone.utc)
    found = []
    reached = 0
    direct_days = 0
    proxy_days = 0
    all_errors = []

    # Parallel date checks keep the live scan fast even when the official site times out.
    dates = [now - timedelta(days=i) for i in range(days)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = {pool.submit(_fetch_rg_day, day): day for day in dates}
        for job in as_completed(jobs):
            day = jobs[job]
            try:
                items, used_url, mode, errors = job.result()
            except Exception as exc:
                all_errors.append(str(exc)[:160])
                continue
            all_errors.extend(errors)
            if mode:
                reached += 1
                if mode == 'direct':
                    direct_days += 1
                else:
                    proxy_days += 1
            for item in items:
                item['source'] = 'Resmî Gazete'
                item['source_name'] = 'Resmî Gazete'
                item['official'] = True
                if not item.get('date'):
                    item['date'] = day.strftime('%Y-%m-%d')
                found.append(item)

    # Deduplicate across daily/proxy responses.
    unique = {}
    for item in found:
        key = (item.get('title', '').casefold(), item.get('date'))
        unique[key] = item
    found = list(unique.values())

    status = {
        'source': 'Resmî Gazete',
        'source_name': 'Resmî Gazete',
        'ok': reached > 0,
        'count': len(found),
        'checked_at': now.isoformat(),
        'days_reached': reached,
        'direct_days': direct_days,
        'proxy_days': proxy_days,
    }
    if reached == 0 and all_errors:
        status['error'] = all_errors[-1][:180]
    elif proxy_days > 0:
        status['note'] = f'Doğrudan bağlantı kısıtlı; {proxy_days} gün güvenli metin fallback ile okundu.'
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
        rg_items, rg_status = resmi_gazete_live(days=11)
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
        'version': 5,
    }
