import json
from pathlib import Path

from price_expectation import scan_price_expectation
from price_expectation_state import enrich_with_pump_realization

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'price_expectation.json'


if __name__ == '__main__':
    data = enrich_with_pump_realization(scan_price_expectation())
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Expectation snapshot updated:', data.get('checked_at'))
