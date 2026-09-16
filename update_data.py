import json
from pathlib import Path

from source_overrides import scan_sector_now


def main():
    data = scan_sector_now()
    data['scan_interval_minutes'] = 10
    Path('data.json').write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print(f"Wrote {len(data.get('items', []))} items from {len(data.get('sources', []))} sources")


if __name__ == '__main__':
    main()
