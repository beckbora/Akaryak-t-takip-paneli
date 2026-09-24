import json
from pathlib import Path

from brent_scan import scan_brent


BRENT_PATH = Path("brent.json")
PRICE_PATH = Path("prices.json")
EXPECTATION_PATH = Path("price_expectation.json")


def read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main():
    data = scan_brent(
        saved=read_json(BRENT_PATH),
        price_data=read_json(PRICE_PATH),
        expectation_data=read_json(EXPECTATION_PATH),
    )
    BRENT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    dm = (data.get("fuel_models") or {}).get("diesel") or {}
    gm = (data.get("fuel_models") or {}).get("gasoline") or {}
    print(
        "Turkey model updated:",
        "diesel", dm.get("estimated_impact_tl_l"),
        "gasoline", gm.get("estimated_impact_tl_l"),
        "diesel_compare", (dm.get("comparison") or {}).get("label"),
        "gasoline_compare", (gm.get("comparison") or {}).get("label"),
    )


if __name__ == "__main__":
    main()
