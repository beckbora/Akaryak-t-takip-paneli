import json, re, hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

SOURCES = [
    {"name":"EPDK Duyurular","url":"https://www.epdk.gov.tr/Detay/Icerik/4-0-1/duyurular","group":"EPDK","official":True},
    {"name":"EPDK Petrol","url":"https://www.epdk.gov.tr/Detay/Icerik/5-0-4-0-6/petrol-piyasasi","group":"EPDK","official":True},
    {"name":"PÜİS Haberler","url":"https://www.puis.org.tr/haberler/","group":"PÜİS","official":False},
    {"name":"TABGİS Duyurular","url":"https://tabgis.org.tr/duyurular/","group":"TABGİS","official":False},
    {"name":"PETDER Mevzuat","url":"https://www.petder.org.tr/tr-TR/mevzuat/628766","group":"PETDER","official":False},
    {"name":"Türkiye LPG Derneği","url":"https://www.tlpgder.org.tr/default.aspx","group":"LPG Derneği","official":False},
    {"name":"UTTS","url":"https://www.utts.gov.tr/","group":"UTTS","official":True}
]

KEYWORDS = [
    "akaryakıt","akaryakit","petrol","motorin","benzin","lpg","otogaz","istasyon","bayi","dağıtıcı","dagitici",
    "utts","tts","tto","tim","özk","okc","ödeme kaydedici","pos","pompa","tabanca","lisans","epdk","resmi gazete",
    "kurul kararı","kurul karari","tebliğ","teblig","yönetmelik","yonetmelik","ceza","tarife","ötv","otv","stok","sıfır atık","sifir atik"
]
CRITICAL = ["önemli","kritik","ceza","son tarih","zorunlu","yükümlülük","yukumluluk","ötv","otv","lisans","utts","okc","ödeme kaydedici","pos","sıfır atık","sifir atik"]
DATE_RE = re.compile(r"(?:\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b|\b(\d{1,2})\s+(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)\s+(20\d{2})\b)", re.I)
MONTHS = {"ocak":1,"şubat":2,"mart":3,"nisan":4,"mayıs":5,"haziran":6,"temmuz":7,"ağustos":8,"eylül":9,"ekim":10,"kasım":11,"aralık":12}
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; AkaryakitTakip/1.0; +https://github.com/beckbora/Akaryak-t-takip-paneli)"}

def clean(s): return re.sub(r"\s+"," ",s or "").strip()
def norm(s): return clean(s).casefold()
def relevant(text):
    t=norm(text)
    return any(k in t for k in KEYWORDS)
def severity(text):
    t=norm(text)
    return "critical" if any(k in t for k in CRITICAL) else "normal"
def parse_date(text):
    m=DATE_RE.search(text or "")
    if not m: return None
    try:
        if m.group(1): d,mo,y=map(int,m.group(1,2,3))
        else: d=int(m.group(4)); mo=MONTHS[m.group(5).casefold()]; y=int(m.group(6))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    except Exception: return None

def candidates(source, html):
    soup=BeautifulSoup(html,"html.parser")
    out=[]
    seen=set()
    for a in soup.find_all("a", href=True):
        title=clean(a.get_text(" ",strip=True))
        href=urljoin(source["url"],a["href"])
        if not href.startswith("http") or href in seen: continue
        context=clean(a.parent.get_text(" ",strip=True) if a.parent else title)
        text=f"{title} {context}"
        if len(title)<8 or title.casefold() in {"devamını oku","detay","haberin devamı","tıklayınız","tüm haberler","tüm duyurular"}: continue
        if not relevant(text): continue
        seen.add(href)
        out.append({"title":title[:240],"url":href,"date":parse_date(context) or parse_date(title),"summary":context[:500],"source":source["group"],"source_name":source["name"],"official":source["official"],"severity":severity(text)})
    # Fallback: dated text blocks for pages whose detail links have generic labels.
    for node in soup.find_all(["tr","li","article","div"]):
        txt=clean(node.get_text(" ",strip=True))
        if len(txt)<25 or len(txt)>800 or not relevant(txt): continue
        dt=parse_date(txt)
        if not dt: continue
        title=re.sub(DATE_RE,"",txt, count=1).strip(" -|:")[:240]
        if len(title)<12: continue
        key=(title,source["group"])
        if key in seen: continue
        seen.add(key)
        out.append({"title":title,"url":source["url"],"date":dt,"summary":txt[:500],"source":source["group"],"source_name":source["name"],"official":source["official"],"severity":severity(txt)})
    return out

def uid(item):
    base=f"{item['source']}|{item['url']}|{item['title']}".encode("utf-8")
    return hashlib.sha1(base).hexdigest()[:16]

def main():
    path=Path("data.json")
    old={}
    if path.exists():
        try: old=json.loads(path.read_text(encoding="utf-8"))
        except Exception: old={}
    existing={i.get("id"):i for i in old.get("items",[]) if i.get("id")}
    now=datetime.now(timezone.utc).isoformat()
    found=[]; status=[]
    for src in SOURCES:
        try:
            r=requests.get(src["url"],headers=HEADERS,timeout=25)
            r.raise_for_status()
            items=candidates(src,r.text)
            for item in items:
                item["id"]=uid(item)
                item["first_seen"]=existing.get(item["id"],{}).get("first_seen",now)
                item["last_seen"]=now
                found.append(item)
            status.append({"source":src["group"],"ok":True,"count":len(items),"checked_at":now})
        except Exception as e:
            status.append({"source":src["group"],"ok":False,"count":0,"checked_at":now,"error":str(e)[:160]})
    merged={i["id"]:i for i in existing.values()}
    for i in found: merged[i["id"]]=i
    items=list(merged.values())
    items.sort(key=lambda x:(x.get("date") or "0000-00-00",x.get("first_seen") or ""), reverse=True)
    items=items[:500]
    data={"updated_at":now,"items":items,"sources":status,"version":1}
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Wrote {len(items)} items from {len(SOURCES)} sources")

if __name__=="__main__": main()
