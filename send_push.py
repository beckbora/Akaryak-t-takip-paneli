import argparse
import base64
import json
import os
import tempfile
from pathlib import Path

import requests
from pywebpush import WebPushException, webpush


def env_value(name):
    value = os.getenv(name, '').strip()
    # GitHub Secrets sometimes receive a complete .env line by mistake.
    # Accept both raw values and NAME=value / NEXT_PUBLIC_NAME=value forms.
    if '=' in value and value.startswith(('SUPABASE_', 'NEXT_PUBLIC_SUPABASE_', 'VAPID_')):
        value = value.split('=', 1)[1].strip()
    return value


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def item_map(data):
    return {x.get('fuel_key'): x for x in (data.get('items') or []) if x.get('fuel_key')}


def signature(item):
    if not item:
        return ''
    return '|'.join([
        str(item.get('status') or ''),
        str(item.get('amount') if item.get('amount') is not None else ''),
        str(item.get('effective_date') or ''),
    ])


def message_for(item):
    fuel = item.get('fuel') or ('MOTORİN' if item.get('fuel_key') == 'diesel' else 'BENZİN')
    status = item.get('status') or ''
    amount = item.get('amount')
    amount_text = ''
    if amount is not None:
        try:
            amount_text = f'{float(amount):.2f}'.replace('.', ',') + ' TL '
        except Exception:
            amount_text = ''
    if status == 'down':
        return f'🔻 {fuel}: {amount_text}indirim bekleniyor'
    if status == 'up':
        return f'🔺 {fuel}: {amount_text}zam bekleniyor'
    if status == 'realized_down':
        return f'✅ {fuel}: {amount_text}indirim gerçekleşti'
    if status == 'realized_up':
        return f'✅ {fuel}: {amount_text}zam gerçekleşti'
    if status == 'cancel_down':
        return f'⏸️ {fuel}: beklenen indirim iptal edildi'
    if status == 'cancel_up':
        return f'⏸️ {fuel}: beklenen zam iptal edildi'
    return item.get('line') or f'{fuel} fiyat durumu güncellendi'


def supabase_headers(secret):
    return {
        'apikey': secret,
        'Authorization': f'Bearer {secret}',
        'Content-Type': 'application/json',
    }


def subscriptions(base_url, secret):
    url = base_url.rstrip('/') + '/rest/v1/push_subscriptions'
    params = {
        'enabled': 'eq.true',
        'select': 'endpoint_hash,endpoint,p256dh,auth',
    }
    r = requests.get(url, headers=supabase_headers(secret), params=params, timeout=12)
    r.raise_for_status()
    rows = r.json()
    return rows if isinstance(rows, list) else []


def delete_subscription(base_url, secret, endpoint_hash):
    if not endpoint_hash:
        return
    url = base_url.rstrip('/') + '/rest/v1/push_subscriptions'
    requests.delete(
        url,
        headers=supabase_headers(secret),
        params={'endpoint_hash': f'eq.{endpoint_hash}'},
        timeout=10,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--previous', required=True)
    parser.add_argument('--current', required=True)
    args = parser.parse_args()

    base_url = env_value('SUPABASE_URL')
    secret = env_value('SUPABASE_SECRET_KEY')
    private_b64 = env_value('VAPID_PRIVATE_KEY_B64')
    subject = env_value('VAPID_SUBJECT') or 'https://petrol-piyasasi-takip.vercel.app'

    if not (base_url and secret and private_b64):
        print('Web Push not configured; skipping sender.')
        return

    previous = item_map(read_json(args.previous))
    current = item_map(read_json(args.current))
    events = []
    for fuel_key, item in current.items():
        if item.get('status') == 'none':
            continue
        if signature(item) == signature(previous.get(fuel_key)):
            continue
        events.append(item)

    # Validate the Supabase sender connection on every scheduled run, even
    # when there is no new fuel event. This makes configuration problems visible
    # before the first real alert is needed.
    subs = subscriptions(base_url, secret)
    print(f'Web Push backend ready: active_subscriptions={len(subs)}')

    if not events:
        print('No new push-worthy fuel event.')
        return

    if not subs:
        print('No active push subscriptions.')
        return

    private_pem = base64.b64decode(private_b64.encode('ascii'))
    with tempfile.NamedTemporaryFile('wb', suffix='.pem', delete=True) as keyfile:
        keyfile.write(private_pem)
        keyfile.flush()

        sent = 0
        removed = 0
        failed = 0
        for item in events:
            payload = json.dumps({
                'title': 'Petrol Piyasası Takip',
                'body': message_for(item),
                'tag': 'fuel-' + str(item.get('fuel_key') or 'price') + '-' + str(item.get('status') or 'update'),
                'url': '/fiyatlar',
                'fuel_key': item.get('fuel_key'),
                'status': item.get('status'),
            }, ensure_ascii=False)
            for sub in subs:
                info = {
                    'endpoint': sub.get('endpoint'),
                    'keys': {
                        'p256dh': sub.get('p256dh'),
                        'auth': sub.get('auth'),
                    },
                }
                try:
                    webpush(
                        subscription_info=info,
                        data=payload,
                        vapid_private_key=keyfile.name,
                        vapid_claims={'sub': subject},
                        timeout=12,
                    )
                    sent += 1
                except WebPushException as exc:
                    code = getattr(getattr(exc, 'response', None), 'status_code', None)
                    if code in (404, 410):
                        delete_subscription(base_url, secret, sub.get('endpoint_hash'))
                        removed += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1

    print(f'Web Push: events={len(events)} subscriptions={len(subs)} sent={sent} removed={removed} failed={failed}')


if __name__ == '__main__':
    main()
