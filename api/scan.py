import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scrape import SOURCES, HEADERS, candidates, uid, merge_seen, resmi_gazete_candidates, category, severity


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
        return items, {'source': src['group'], 'source_name': src['name'], 'ok': True, 'count': len(items), 'checked_at': now}
    except Exception as e:
        return [], {'source': src['group'], 'source_name': src['name'], 'ok': False, 'count': 0, 'checked_at': now, 'error': str(e)[:180]}


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
            except Exception as e:
                items, status = [], {'source': src['group'], 'source_name': src['name'], 'ok': False, 'count': 0, 'checked_at': now, 'error': str(e)[:180]}
            statuses.append(status)
            for item in items:
                item['source_url'] = src['url']
                item_id = uid(item)
                fresh.append(merge_seen(item, existing.get(item_id), now))

    try:
        rg_items, rg_status = resmi_gazete_candidates(days=3)
        statuses.append(rg_status)
        for item in rg_items:
            item['source_url'] = 'https://www.resmigazete.gov.tr/'
            item_id = uid(item)
            fresh.append(merge_seen(item, existing.get(item_id), now))
    except Exception as e:
        statuses.append({'source': 'Resmî Gazete', 'source_name': 'Resmî Gazete', 'ok': False, 'count': 0, 'checked_at': now, 'error': str(e)[:180]})

    merged = {i['id']: i for i in existing.values()}
    for item in fresh:
        merged[item['id']] = item

    items = list(merged.values())
    for item in items:
        item.setdefault('category', category(f"{item.get('title','')} {item.get('summary','')}"))
        if item.get('severity') not in {'critical', 'important', 'normal'}:
            item['severity'] = severity(f"{item.get('title','')} {item.get('summary','')}")
        item.setdefault('revision', 0)

    items.sort(key=lambda x: (x.get('date') or '0000-00-00', x.get('changed_at') or x.get('first_seen') or ''), reverse=True)
    return {'updated_at': now, 'live': True, 'items': items[:700], 'sources': statuses, 'version': 3}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            body = json.dumps(scan_now(), ensure_ascii=False).encode('utf-8')
            self.send_response(200)
        except Exception as e:
            body = json.dumps({'error': str(e)[:300]}, ensure_ascii=False).encode('utf-8')
            self.send_response(500)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, max-age=0')
        self.end_headers()
        self.wfile.write(body)
