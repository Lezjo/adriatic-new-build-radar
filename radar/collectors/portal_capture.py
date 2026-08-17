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
        return " ".join((anchor.evaluate("""el=>{let n=el,b='';for(let i=0;i<6&&n;i++,n=n.parentElement){let t=(n.innerText||'').replace(/\\s+/g,' ').trim();if(t.length>b.length&&t.length<=3500)b=t}return b}""") or "").split())
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

def capture_jbc(browser, location, spec, debug):
    """JBC V6: bounded crawler with robust link extraction.

    JBC currently exposes property/project cards on the homepage and a
    separate "Vedi Tutti i Cantieri" catalog. Some links may be rendered by
    the frontend, so extraction checks href/data-* attributes and onclick,
    then falls back to clicking the visible CTA when necessary.
    """
    source = spec["name"]
    max_hubs = min(int(spec.get("max_hub_pages", 4)), 4)
    max_details = min(int(spec.get("max_detail_pages", 80)), 80)

    ctx = browser.new_context(
        locale="it-IT",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    )
    ctx.set_default_timeout(9000)
    page = ctx.new_page()
    page.set_default_navigation_timeout(15000)

    start_url = canonical(spec["url"])
    hub_queue = [start_url]
    queued_hubs = {start_url}
    visited_hubs = set()
    detail_urls = {}
    errors = []
    pages = 0
    last_status = None
    last_title = None

    def internal(u):
        try:
            p = urlparse(canonical(u))
            return p.netloc.lower().removeprefix("www.") == JBC_HOST
        except Exception:
            return False

    def bad(u):
        path = urlparse(canonical(u)).path.lower()
        return any(x in path for x in BAD_PATH_HINTS)

    def add_hub(u):
        u = canonical(u)
        if (
            internal(u) and not bad(u)
            and u not in queued_hubs
            and len(hub_queue) < max_hubs
        ):
            queued_hubs.add(u)
            hub_queue.append(u)

    def add_detail(u, label=""):
        u = canonical(u)
        if (
            internal(u) and not bad(u)
            and u.rstrip("/") != start_url.rstrip("/")
            and len(detail_urls) < max_details
        ):
            detail_urls.setdefault(u, label)

    def extract_dom_links(base):
        """Extract all internal href/data-url/on-click destinations.
        We intentionally do not depend on URL slugs or card text: JBC's
        frontend can use unconventional routes and JS-generated anchors.
        """
        try:
            rows = page.locator("a, [data-href], [data-url]").evaluate_all(
                """els => els.map(el => ({
                    href: el.getAttribute('href') || '',
                    dataHref: el.getAttribute('data-href') || '',
                    dataUrl: el.getAttribute('data-url') || '',
                    onclick: el.getAttribute('onclick') || '',
                    text: (el.innerText || el.getAttribute('aria-label') || '').trim()
                }))"""
            )
        except Exception as exc:
            errors.append(f"dom-links: {type(exc).__name__}: {exc!r}")
            return

        for row in rows:
            text = " ".join((row.get("text") or "").split())
            candidates = [row.get("href"), row.get("dataHref"), row.get("dataUrl")]
            onclick = row.get("onclick") or ""
            # Common JS patterns: location.href='...', window.open('...'),
            # window.location='...'.
            candidates += re.findall(
                r"(?:location(?:\.href)?|window\.open)\s*\(?'?([^'\")]+)",
                onclick,
                flags=re.I,
            )

            for raw in candidates:
                if not raw:
                    continue
                raw = str(raw).strip()
                if raw.startswith("javascript:"):
                    continue
                u = canonical(urljoin(base, raw))
                if not internal(u) or bad(u):
                    continue
                path = urlparse(u).path.lower()
                low = text.lower()

                if (
                    "vedi tutti i cantieri" in low
                    or "vedi tutti cantieri" in low
                    or "i nostri cantieri" in low
                    or "/cantieri" in path
                    or "/cantiere" in path
                    or "nuove costruzioni" in low
                ):
                    add_hub(u)
                elif (
                    "vedi immobile" in low
                    or "scopri immobile" in low
                    or "dettagli" in low
                    or "immobile" in low
                    or "cantiere" in low
                    or any(t in (text + " " + path).lower() for t in JBC_TERMS)
                    or path.count("-") >= 2
                ):
                    add_detail(u, text)

    def click_ctas():
        """Fallback for frontend links that have no usable href in DOM."""
        try:
            ctas = page.get_by_text("Vedi Immobile", exact=True)
            count = min(ctas.count(), max_details - len(detail_urls))
            for i in range(count):
                try:
                    ctas.nth(i).click(timeout=2500)
                    page.wait_for_timeout(250)
                    current = canonical(page.url)
                    if internal(current) and current.rstrip("/") != start_url.rstrip("/"):
                        add_detail(current, "Vedi Immobile")
                    page.go_back(wait_until="domcontentloaded", timeout=8000)
                except Exception:
                    try:
                        page.goto(start_url, wait_until="domcontentloaded", timeout=8000)
                    except Exception:
                        pass
        except Exception:
            pass

    def inspect_hub(u):
        nonlocal pages, last_status, last_title
        try:
            response = page.goto(u, wait_until="domcontentloaded", timeout=15000)
            last_status = response.status if response else None
            try:
                last_title = page.title()
            except Exception:
                last_title = None
            page.wait_for_timeout(600)
            pages += 1
            extract_dom_links(u)
            click_ctas()
        except PlaywrightTimeoutError:
            errors.append(f"hub {u}: TIMEOUT")
        except Exception as exc:
            errors.append(f"hub {u}: {type(exc).__name__}: {exc!r}")

    while hub_queue and len(visited_hubs) < max_hubs:
        u = hub_queue.pop(0)
        if u in visited_hubs:
            continue
        visited_hubs.add(u)
        inspect_hub(u)

    records = []
    visited_details = []
    for u, fallback in list(detail_urls.items())[:max_details]:
        visited_details.append(u)
        try:
            response = page.goto(u, wait_until="domcontentloaded", timeout=15000)
            if response and response.status >= 400:
                errors.append(f"detail {u}: HTTP {response.status}")
                continue
            page.wait_for_timeout(350)
            try:
                body = " ".join((page.locator("body").inner_text(timeout=3500) or "").split())
            except Exception:
                body = fallback
            title = page.title() or ""
            low = f"{title} {body}".lower()
            if len(body) < 50:
                continue
            if any(x in low for x in ("privacy policy", "cookie policy", "lavora con noi", "franchising")):
                continue
            # Accept a property page if it has a property signal OR a useful
            # descriptive title/slug. Do not require price/area to exist.
            path = urlparse(u).path.lower()
            if not (
                any(t in low for t in JBC_TERMS)
                or path.count("-") >= 2
                or len(title.strip()) >= 12
            ):
                continue
            records.append(detail(page, source, location, u, fallback))
        except PlaywrightTimeoutError:
            errors.append(f"detail {u}: TIMEOUT")
        except Exception as exc:
            errors.append(f"detail {u}: {type(exc).__name__}: {exc!r}")

    ded = {r["source_url"]: r for r in records}
    manifest = {
        "version": "JBC-V6-ROBUST",
        "source": source,
        "location": location,
        "start_url": start_url,
        "hub_pages_visited": len(visited_hubs),
        "hub_pages": sorted(visited_hubs),
        "candidate_detail_urls": len(detail_urls),
        "detail_pages_visited": len(visited_details),
        "records_captured": len(ded),
        "unit_records": sum(1 for r in ded.values() if r.get("record_type") == "UNIT"),
        "project_records": sum(1 for r in ded.values() if r.get("record_type") == "PROJECT"),
        "projects_detected": len({r.get("project_id") for r in ded.values() if r.get("project_id")}),
        "last_http_status": last_status,
        "last_title": last_title,
        "errors": errors[:100],
        "candidate_urls": list(detail_urls.keys()),
    }
    try:
        (debug / f"{location}__jbc_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        errors.append(f"manifest: {type(exc).__name__}: {exc!r}")
    ctx.close()
    return list(ded.values()), pages, last_status, last_title, None if ded else "JBC adapter found 0 detail records"

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
