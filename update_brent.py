import json
from pathlib import Path
from brent_scan import scan_brent

PATH = Path("brent.json")

def read_saved():
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def main():
    data = scan_brent(saved=read_saved())
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Brent updated:", (data.get("brent") or {}).get("price_usd_bbl"), "|", (data.get("turkey_model") or {}).get("label"), (data.get("turkey_model") or {}).get("estimated_pump_impact_tl_l"), "TL/L")

if __name__ == "__main__":
    main()
