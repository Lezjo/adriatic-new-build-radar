from __future__ import annotations
import hashlib,json,re,time
from pathlib import Path
from urllib.parse import urljoin,urlparse,parse_qsl,urlencode,urlunparse
from playwright.sync_api import sync_playwright,TimeoutError as PlaywrightTimeoutError

ROOT=Path(__file__).resolve().parents[2]
SOURCES=json.loads((ROOT/"radar"/"sources.json").read_text(encoding="utf-8"))
PRICE_RE=re.compile(r"(?:€\s*)?([\d\.\,]+)\s*(k|K)?(?:\s*€)?",re.I)
AREA_RE=re.compile(r"(\d{2,4}(?:[.,]\d+)?)\s*m(?:²|2)",re.I)
ROOM_RE=re.compile(r"(\d+)\s*(?:locali|camere|rooms?|stanze)",re.I)
FLOOR_RE=re.compile(r"(?:piano|floor)\s*(?:numero\s*)?([A-Za-z0-9°\-]+)",re.I)

PROPERTY_PATH_HINTS=("/annunci/","/immobili/","/immobile/","/case/","/appartamenti/","/ville/","/residenze/","/residence/","/nuove-costruzioni/","/nuove_costruzioni/","/vendita/","/vendita-case/","/vendita-nuove","/nuova-costruzione/","/project/","/progetti/")
BAD_PATH_HINTS=("/login","/registr","/contatti","/contact","/privacy","/cookie","/agenzie","/agenzia","/search","/ricerca","/map","/mappa","/franchising","/newsletter","/lavora-con-noi")
JBC_HOST="jbcimmobiliare.it"
JBC_TERMS=("nuova costruzione","nuove costruzioni","nuovo","nuova","residenza","residence","progetto","cantiere","appartamento","trilocale","quadrilocale","bilocale","attico","villa","villette","fronte mare","vista mare","green","jhills")

def canonical(url):
    p=urlparse(url); q=[(k,v) for k,v in parse_qsl(p.query) if k.lower() not in {"utm_source","utm_medium","utm_campaign","utm_content","utm_term"}]
    return urlunparse((p.scheme,p.netloc,p.path.rstrip("/"),"",urlencode(q),""))

def parse_price(text):
    if not text:return None
    m=re.search(r"€\s*([\d\.\,]+)\s*(k|K)?|([\d\.\,]+)\s*(k|K)?\s*€",text.replace("\xa0"," "),re.I) or PRICE_RE.search(text)
    if not m:return None
    g=[x for x in m.groups() if x is not None]
    if not g:return None
    n=g[0]; suf=next((x for x in g[1:] if x.lower()=="k"),None)
    try:
        if "." in n and "," in n:n=n.replace(".","").replace(",",".")
        elif "," in n:n=n.replace(",","") if len(n.rsplit(",",1)[1])==3 else n.replace(",",".")
        elif "." in n and len(n.rsplit(".",1)[1])==3:n=n.replace(".","")
        v=float(n)*(1000 if suf else 1)
        return int(v) if 10000<=v<=10000000 else None
    except:return None

def parse_area(text):
    m=AREA_RE.search(text or "")
    if not m:return None
    try:
        v=float(m.group(1).replace(".","").replace(",","."))
        return v if 15<=v<=5000 else None
    except:return None

def parse_rooms(text):
    m=ROOM_RE.search(text or "")
    return int(m.group(1)) if m else None

def parse_floor(text):
    m=FLOOR_RE.search(text or "")
    return m.group(1) if m else None

def get_text(anchor):
    try:
        return " ".join((anchor.evaluate("""el=>{let n=el,b='';for(let i=0;i<6&&n;i++,n=n.parentElement){let t=(n.innerText||'').replace(/\s+/g,' ').trim();if(t.length>b.length&&t.length<=3500)b=t}return b}""") or "").split())
    except:
        try:return " ".join((anchor.inner_text(timeout=1500) or "").split())
        except:return ""

def generic_url(u,base):
    p=urlparse(u); b=urlparse(base)
    if p.scheme not in ("http","https") or p.netloc.lower()!=b.netloc.lower():return False
    path=p.path.lower()
    if any(x in path for x in BAD_PATH_HINTS):return False
    if any(x in path for x in PROPERTY_PATH_HINTS):return True
    last=path.rstrip("/").split("/")[-1]
    return len(last)>=12 and "-" in last

def jbc_url(u,text,base):
    p=urlparse(u); b=urlparse(base)
    if p.scheme not in ("http","https") or p.netloc.lower()!=b.netloc.lower():return False
    path=p.path.lower()
    if not path or path=="/" or any(x in path for x in BAD_PATH_HINTS):return False
    hay=(path+" "+text).lower()
    if any(t in hay for t in JBC_TERMS):return True
    last=path.rstrip("/").split("/")[-1]
    return len(last)>=28 and last.count("-")>=3

def jsonld(page):
    out=[]
    try:
        for s in page.locator('script[type="application/ld+json"]').all():
            try:
                o=json.loads(s.text_content(timeout=800) or "")
                out.extend(o if isinstance(o,list) else [o])
            except:pass
    except:pass
    return out

def detail(page,source,location,u,fallback=""):
    try:title=page.title() or ""
    except:title=""
    try:body=" ".join((page.locator("body").inner_text(timeout=3000) or "").split())
    except:body=fallback
    js=jsonld(page); name=None; price=None
    for o in js:
        if isinstance(o,dict) and o.get("@type") in ("Product","Residence") and o.get("name"):name=str(o["name"])
        if isinstance(o,dict) and o.get("@type")=="Offer" and o.get("price"):
            try:price=int(float(o["price"]))
            except:pass
    price=price or parse_price(body)
    area=parse_area(body); rooms=parse_rooms(body); floor=parse_floor(body)
    um=re.search(r"\bUnità\s+([A-Z]?\d+(?:\.\d+)?)\b",body,re.I)
    unit=um.group(1).upper() if um else None
    energy=None
    em=re.search(r"(?:energetica|energia|classe)\s*[:\-]?\s*([A-G][1-4]?)",body,re.I)
    if em:energy=em.group(1).upper()
    loc=location
    for c in ("jesolo paese","jesolo","caorle","cavallino-treporti","san donà di piave","treviso"):
        if c in body.lower():loc=c;break
    features=[x for x in ("garage","parcheggio","posto auto","piscina","ascensore","aria condizionata","pannelli solari","pannelli fotovoltaici","vista mare","fronte mare","terrazza","giardino","area fitness","posto spiaggia","pompa di calore") if x in body.lower()]
    status="PLANNED" if re.search(r"consegna\s+(?:primavera|estate|autunno|inverno|\w+)\s+202[6-9]",body,re.I) else "ACTIVE"
    rid="url:"+hashlib.sha1(canonical(u).encode()).hexdigest()
    return {"source":source,"source_url":canonical(u),"location":loc,"listing_id":rid,"listing_title":re.sub(r"\s+"," ",(name or title or u.rsplit("/",1)[-1].replace("-"," "))).strip()[:300],"price":price,"area_m2":area,"rooms":rooms,"floor":floor,"energy_class":energy,"unit_id":unit,"record_type":"UNIT" if unit else "PROJECT","status":status,"features":features,"raw_text":body[:5000],"captured_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}

def generic_extract(page,source,location,url):
    out=[];seen=set()
    try:anchors=page.locator("a[href]").all()
    except:return out
    for a in anchors:
        try:h=a.get_attribute("href")
        except:continue
        if not h:continue
        u=canonical(urljoin(url,h))
        if u in seen or not generic_url(u,url):continue
        t=get_text(a) or u.rsplit("/",1)[-1].replace("-"," ")
        out.append({"source":source,"location":location,"listing_id":"url:"+hashlib.sha1(u.encode()).hexdigest(),"source_url":u,"listing_title":t[:300],"price":parse_price(t),"area_m2":parse_area(t),"rooms":parse_rooms(t),"raw_text":t[:2000],"captured_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())})
        seen.add(u)
    return out

def capture_jbc(browser,location,spec,debug):
    source=spec["name"]; starts=[spec["url"]]
    ctx=browser.new_context(locale="it-IT",user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    hub=ctx.new_page(); det=ctx.new_page(); discovered={}; hubs=set(); errors=[]; pages=0; status=None; title=None
    def inspect(url):
        nonlocal pages,status,title
        url=canonical(url)
        if url in hubs:return
        hubs.add(url)
        try:
            r=hub.goto(url,wait_until="domcontentloaded",timeout=60000);status=r.status if r else None;title=hub.title();hub.wait_for_timeout(1800)
            for _ in range(4):hub.mouse.wheel(0,1800);hub.wait_for_timeout(450)
            pages+=1
            for a in hub.locator("a[href]").all():
                try:h=a.get_attribute("href")
                except:continue
                if not h:continue
                u=canonical(urljoin(url,h));t=get_text(a)
                label=t.lower()
                if jbc_url(u,t,url) or "vedi immobile" in label:
                    discovered[u]=t
                elif "vedi tutti i cantieri" in label or "nostri cantieri" in label:
                    inspect(u)
        except Exception as e:errors.append(f"hub {url}: {type(e).__name__}: {e!r}")
    for s in starts:inspect(s)
    rows=[]
    for u,t in list(discovered.items()):
        try:
            r=det.goto(u,wait_until="domcontentloaded",timeout=60000);det.wait_for_timeout(900)
            body=" ".join((det.locator("body").inner_text(timeout=3000) or "").split())
            if r and r.status>=400 or len(body)<80:continue
            low=(det.title()+" "+body).lower()
            if not any(x in low for x in JBC_TERMS):continue
            rows.append(detail(det,source,location,u,t))
        except Exception as e:errors.append(f"detail {u}: {type(e).__name__}: {e!r}")
    ded={x["source_url"]:x for x in rows}
    try:(debug/f"{location}__jbc_manifest.json").write_text(json.dumps({"source":source,"location":location,"start_urls":starts,"hub_pages_captured":pages,"candidate_detail_urls":len(discovered),"records_captured":len(ded),"last_http_status":status,"last_title":title,"errors":errors[:50],"candidate_urls":sorted(discovered)},ensure_ascii=False,indent=2),encoding="utf-8")
    except:pass
    ctx.close();return list(ded.values()),pages,status,title,(None if ded else "JBC adapter found 0 detail records")

def next_page(page,current,seen):
    for sel in ('a[rel="next"]','a[aria-label*="Successiva"]','a[aria-label*="successiva"]','a[aria-label*="Next"]','a[title*="Successiva"]','a[title*="successiva"]','a[title*="Next"]'):
        try:
            for a in page.locator(sel).all():
                h=a.get_attribute("href")
                if h:
                    u=canonical(urljoin(current,h))
                    if u not in seen:return u
        except:pass
    return None

def capture_source(browser,location,spec):
    debug=ROOT/"data"/"debug";debug.mkdir(parents=True,exist_ok=True)
    if spec.get("name")=="jbc_direct" or "jbcimmobiliare.it" in spec.get("url","").lower():
        rows,pages,status,title,error=capture_jbc(browser,location,spec,debug)
    else:
        ctx=browser.new_context(locale="it-IT",user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
        page=ctx.new_page();rows=[];seen=set();url=spec["url"];error=None;status=None;title=None
        for _ in range(spec.get("max_pages",100)):
            url=canonical(url)
            if url in seen:break
            seen.add(url)
            try:
                r=page.goto(url,wait_until="domcontentloaded",timeout=60000);status=r.status if r else None;title=page.title();page.wait_for_timeout(2500)
                for _ in range(5):page.mouse.wheel(0,1800);page.wait_for_timeout(600)
                rows.extend(generic_extract(page,spec["name"],location,url));nxt=next_page(page,url,seen)
                if not nxt:break
                url=nxt
            except PlaywrightTimeoutError as e:error=f"TIMEOUT: {e!r}";break
            except Exception as e:error=f"{type(e).__name__}: {e!r}";break
        pages=len(seen)
        if not rows:
            safe=re.sub(r"[^a-zA-Z0-9_-]+","_",f"{location}__{spec['name']}")[:120]
            try:
                (debug/f"{safe}.html").write_text(page.content(),encoding="utf-8");page.screenshot(path=str(debug/f"{safe}.png"),full_page=False)
            except Exception as e:error=(error or "")+f" | debug_save: {e!r}"
        ctx.close()
    try:
        f=debug/"capture_debug.json";old=json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        old.append({"location":location,"source":spec["name"],"url":spec["url"],"pages_captured":pages,"records_captured":len(rows),"last_http_status":status,"last_title":title,"error":error})
        f.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding="utf-8")
    except:pass
    ded={x["source_url"]:x for x in rows}
    return list(ded.values()),pages

def run():
    all_records=[];coverage=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        for location,specs in SOURCES.items():
            for spec in specs:
                rows,pages=capture_source(browser,location,spec);all_records.extend(rows)
                coverage.append({"location":location,"source":spec["name"],"start_url":spec["url"],"pages_captured":pages,"records_captured":len(rows)})
        browser.close()
    return all_records,coverage

if __name__=="__main__":
    records,coverage=run();print(json.dumps({"records":len(records),"coverage":coverage},ensure_ascii=False,indent=2))
