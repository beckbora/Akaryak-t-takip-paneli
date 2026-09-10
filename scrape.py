import json
import re
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

SOURCES = [
    {"key":"epdk","name":"EPDK Duyurular","url":"https://www.epdk.gov.tr/Detay/Icerik/4-0-1/duyurular","group":"EPDK","official":True},
    {"key":"gib","name":"GİB Yeni Nesil ÖKC","url":"https://ynokc.gib.gov.tr/Home/DuyuruArsiv","group":"GİB / YN ÖKC","official":True},
    {"key":"darphane","name":"Darphane Duyurular","url":"https://www.darphane.gov.tr/","group":"Darphane / UTTS","official":True},
    {"key":"utts","name":"UTTS","url":"https://www.utts.gov.tr/","group":"UTTS","official":True},
    {"key":"puis","name":"PÜİS Haberler","url":"https://www.puis.org.tr/haberler/","group":"PÜİS","official":False},
    {"key":"tabgis","name":"TABGİS Duyurular","url":"https://tabgis.org.tr/duyurular/","group":"TABGİS","official":False},
    {"key":"petder","name":"PETDER Mevzuat","url":"https://www.petder.org.tr/tr-TR/mevzuat/628766","group":"PETDER","official":False},
    {"key":"lpgder","name":"Türkiye LPG Derneği","url":"https://www.tlpgder.org.tr/default.aspx","group":"LPG Derneği","official":False},
    {"key":"tobb","name":"TOBB Sektör Haberleri","url":"https://www.tobb.org.tr/Sayfalar/Arsiv.php?csn=TOBB&kategori=&lst=2&s5=50","group":"TOBB","official":False},
]

KEYWORDS = [
    "akaryakıt","akaryakit","petrol","motorin","benzin","lpg","otogaz","tüplügaz","tuplugaz",
    "istasyon","bayi","dağıtıcı","dagitici","utts","ulusal taşıt tanıma","ulusal tasit tanima",
    "tto","tim","ttb","tts","özk","okc","ö.k.c","ödeme kaydedici","odeme kaydedici","yn ökc",
    "pos","pompa","tabanca","lisans","epdk","kurul kararı","kurul karari","tebliğ","teblig",
    "yönetmelik","yonetmelik","ceza","tarife","ötv","otv","zorunlu petrol stoku","sıfır atık","sifir atik",
    "petrol ürünleri","petrol urunleri","sorumlu müdür","sorumlu mudur"
]

CRITICAL_WORDS = [
    "ceza","son tarih","süre uzat","sure uzat","zorunlu","yükümlülük","yukumluluk","ötv","otv",
    "resen","iptal","yürürlük","yururluk","31/12/","30/6/","30/06/","lisans iptal","uygun bulunmuştur"
]
IMPORTANT_WORDS = [
    "utts","ulusal taşıt tanıma","ulusal tasit tanima","okc","ödeme kaydedici","odeme kaydedici","pos",
    "kurul kararı","kurul karari","tebliğ","teblig","yönetmelik","yonetmelik","tarife","lisans",
    "denetim","sıfır atık","sifir atik","zorunlu petrol stoku"
]

DATE_RE = re.compile(
    r"(?:\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b|\b(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s+(20\d{2})\b)",
    re.I,
)
MONTHS = {"ocak":1,"şubat":2,"mart":3,"nisan":4,"mayıs":5,"haziran":6,"temmuz":7,"ağustos":8,"eylül":9,"ekim":10,"kasım":11,"aralık":12}
HEADERS = {
    "User-Agent":"Mozilla/5.0 (compatible; AkaryakitTakip/2.0; +https://github.com/beckbora/Akaryak-t-takip-paneli)",
    "Accept-Language":"tr-TR,tr;q=0.9,en;q=0.5",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def norm(s):
    return clean(s).casefold()


def relevant(text):
    t = norm(text)
    return any(k in t for k in KEYWORDS)


def severity(text):
    t = norm(text)
    if any(k in t for k in CRITICAL_WORDS):
        return "critical"
    if any(k in t for k in IMPORTANT_WORDS):
        return "important"
    return "normal"


def category(text):
    t = norm(text)
    if any(k in t for k in ["utts","ulusal taşıt tanıma","ulusal tasit tanima","tto","tim","ttb"]):
        return "UTTS"
    if any(k in t for k in ["okc","ödeme kaydedici","odeme kaydedici","yn ökc","pos"]):
        return "ÖKC / POS"
    if any(k in t for k in ["lpg","otogaz","tüplügaz","tuplugaz"]):
        return "LPG"
    if any(k in t for k in ["ötv","otv","vergi","gib"]):
        return "Vergi / ÖTV"
    if any(k in t for k in ["lisans","denetim","ceza"]):
        return "Lisans / Denetim"
    if any(k in t for k in ["kurul kararı","kurul karari","tebliğ","teblig","yönetmelik","yonetmelik","resmî gazete","resmi gazete"]):
        return "Mevzuat"
    return "Akaryakıt"


def parse_date(text):
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        if m.group(1):
            d, mo, y = map(int, m.group(1, 2, 3))
        else:
            d = int(m.group(4))
            mo = MONTHS[m.group(5).casefold()]
            y = int(m.group(6))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except Exception:
        return None


def canonical_url(url):
    try:
        p = urlsplit(url)
        return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip("/"), p.query, ""))
    except Exception:
        return url


def make_item(source, title, href, context):
    text = clean(f"{title} {context}")
    return {
        "title": clean(title)[:260],
        "url": canonical_url(href),
        "date": parse_date(context) or parse_date(title),
        "summary": clean(context)[:700],
        "source": source["group"],
        "source_name": source["name"],
        "official": source["official"],
        "severity": severity(text),
        "category": category(text),
    }


def candidates(source, html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen_urls = set()
    seen_text = set()

    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        href = canonical_url(urljoin(source["url"], a["href"]))
        if not href.startswith("http") or href in seen_urls:
            continue
        context_node = a.find_parent(["article", "li", "tr", "div"]) or a.parent
        context = clean(context_node.get_text(" ", strip=True) if context_node else title)
        text = f"{title} {context}"
        if len(title) < 7 or title.casefold() in {"devamını oku","detay","haberin devamı","tıklayınız","tum haberler","tüm haberler","tüm duyurular"}:
            continue
        if not relevant(text):
            continue
        seen_urls.add(href)
        out.append(make_item(source, title, href, context))

    for node in soup.find_all(["tr", "li", "article"]):
        txt = clean(node.get_text(" ", strip=True))
        if len(txt) < 25 or len(txt) > 1100 or not relevant(txt):
            continue
        dt = parse_date(txt)
        if not dt:
            continue
        title = re.sub(DATE_RE, "", txt, count=1).strip(" -|:")[:260]
        key = norm(title)
        if len(title) < 12 or key in seen_text:
            continue
        seen_text.add(key)
        link = node.find("a", href=True)
        href = canonical_url(urljoin(source["url"], link["href"])) if link else source["url"]
        if href in seen_urls:
            continue
        out.append(make_item(source, title, href, txt))

    return out[:80]


def resmi_gazete_candidates(days=10):
    source = {"name":"Resmî Gazete","group":"Resmî Gazete","official":True}
    now = datetime.now(timezone.utc)
    found = []
    ok_days = 0
    errors = []
    for delta in range(days):
        day = now - timedelta(days=delta)
        ymd = day.strftime("%Y%m%d")
        url = f"https://www.resmigazete.gov.tr/eskiler/{day:%Y}/{day:%m}/{ymd}.htm"
        try:
            r = SESSION.get(url, timeout=20)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            ok_days += 1
            src = {**source, "url":url}
            for item in candidates(src, r.text):
                item["source"] = "Resmî Gazete"
                item["source_name"] = "Resmî Gazete"
                item["official"] = True
                if not item.get("date"):
                    item["date"] = day.strftime("%Y-%m-%d")
                found.append(item)
        except Exception as e:
            errors.append(str(e)[:120])
    status = {
        "source":"Resmî Gazete",
        "source_name":"Resmî Gazete",
        "ok":ok_days > 0,
        "count":len(found),
        "checked_at":now.isoformat(),
    }
    if ok_days == 0 and errors:
        status["error"] = errors[0]
    return found, status


def uid(item):
    url = canonical_url(item.get("url") or "")
    if url and url != canonical_url(item.get("source_url") or ""):
        base = f"{item.get('source')}|{url}"
    else:
        base = f"{item.get('source')}|{item.get('title')}|{item.get('date')}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:18]


def fingerprint(item):
    body = "|".join([
        clean(item.get("title")),
        clean(item.get("summary")),
        str(item.get("date") or ""),
        str(item.get("severity") or ""),
        str(item.get("category") or ""),
    ])
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:18]


def merge_seen(item, previous, now):
    item["id"] = uid(item)
    item["fingerprint"] = fingerprint(item)
    if previous:
        item["first_seen"] = previous.get("first_seen", now)
        old_fp = previous.get("fingerprint") or fingerprint(previous)
        if old_fp != item["fingerprint"]:
            item["changed_at"] = now
            item["revision"] = int(previous.get("revision", 0)) + 1
        else:
            item["changed_at"] = previous.get("changed_at")
            item["revision"] = int(previous.get("revision", 0))
    else:
        item["first_seen"] = now
        item["changed_at"] = None
        item["revision"] = 0
    item["last_seen"] = now
    return item


def main():
    path = Path("data.json")
    try:
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        old = {}

    existing = {i.get("id"): i for i in old.get("items", []) if i.get("id")}
    now = datetime.now(timezone.utc).isoformat()
    found = []
    statuses = []

    rg_items, rg_status = resmi_gazete_candidates()
    for item in rg_items:
        item["source_url"] = "https://www.resmigazete.gov.tr/"
        item_id = uid(item)
        found.append(merge_seen(item, existing.get(item_id), now))
    statuses.append(rg_status)

    for src in SOURCES:
        try:
            r = SESSION.get(src["url"], timeout=25)
            r.raise_for_status()
            items = candidates(src, r.text)
            for item in items:
                item["source_url"] = src["url"]
                item_id = uid(item)
                found.append(merge_seen(item, existing.get(item_id), now))
            statuses.append({
                "source":src["group"],
                "source_name":src["name"],
                "ok":True,
                "count":len(items),
                "checked_at":now,
            })
        except Exception as e:
            statuses.append({
                "source":src["group"],
                "source_name":src["name"],
                "ok":False,
                "count":0,
                "checked_at":now,
                "error":str(e)[:180],
            })

    merged = {i["id"]: i for i in existing.values()}
    for item in found:
        merged[item["id"]] = item

    items = list(merged.values())
    for item in items:
        item.setdefault("category", category(f"{item.get('title','')} {item.get('summary','')}"))
        if item.get("severity") not in {"critical", "important", "normal"}:
            item["severity"] = severity(f"{item.get('title','')} {item.get('summary','')}")
        item.setdefault("revision", 0)

    items.sort(
        key=lambda x: (x.get("date") or "0000-00-00", x.get("changed_at") or x.get("first_seen") or ""),
        reverse=True,
    )
    items = items[:700]

    data = {
        "updated_at":now,
        "items":items,
        "sources":statuses,
        "version":2,
        "scan_interval_minutes":15,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(items)} items from {len(statuses)} monitored sources")


if __name__ == "__main__":
    main()
