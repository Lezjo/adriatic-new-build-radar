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
    """JBC V10: robust homepage/category discovery + direct detail capture.

    V10 fixes the V9 failure mode where property URLs were classified as
    navigation hubs and detail pages could be discarded before normalization.
    It also handles sitemap indexes, direct property slugs and JS/lazy links.
    """
    source = spec["name"]
    max_hubs = max(5, int(spec.get("max_hub_pages", 20)))
    max_details = max(20, int(spec.get("max_detail_pages", 250)))
    max_sitemap_urls = max(100, int(spec.get("max_sitemap_urls", 1000)))
    JBC_HOME = "https://www.jbcimmobiliare.it/"

    starts = list(dict.fromkeys([
        canonical(spec.get("url") or JBC_HOME),
        canonical(JBC_HOME),
    ]))

    ctx = browser.new_context(
        locale="it-IT",
        user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        extra_http_headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.7"},
    )
    ctx.set_default_timeout(12000)
    page = ctx.new_page()
    page.set_default_navigation_timeout(35000)

    hub_queue, queued_hubs, visited_hubs = [], set(), set()
    detail_urls, errors = {}, []
    pages = 0
    last_status = None
    last_title = None

    HUB_PATHS = (
        "/immobili", "/vendita", "/cerca", "/ricerca",
        "/cantieri", "/cantiere", "/progetti", "/progetto",
        "/nuove-costruzioni", "/nuove_costruzioni",
        "/nuova-costruzione",
    )
    HUB_TEXT = (
        "scopri gli immobili", "scopri gli immobili",
        "immobili in città", "immobili al mare",
        "tutti i cantieri", "i nostri cantieri",
        "immobili in vendita", "cerca", "ricerca",
        "cantieri", "progetti", "residenze", "residence",
    )
    EXPLICIT_HUB_SLUGS = {
        "", "index", "index.php", "home", "home.php",
        "investimento-immobiliare.php", "chi-siamo.php",
        "about-us.php", "contatti.php", "contact.php",
        "404.php",
    }

    def internal(u):
        try:
            return (
                urlparse(canonical(u)).netloc.lower().removeprefix("www.")
                == JBC_HOST
            )
        except Exception:
            return False

    def bad(u):
        path = urlparse(canonical(u)).path.lower()
        return (
            any(x in path for x in BAD_PATH_HINTS)
            or path.endswith((
                ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
                ".pdf", ".css", ".js", ".xml", ".ico"
            ))
        )

    def is_property_url(u, label=""):
        if not internal(u) or bad(u):
            return False
        path = urlparse(u).path.strip("/").lower()
        if path in EXPLICIT_HUB_SLUGS:
            return False
        if path.endswith(".php"):
            return False

        # Real JBC property pages use descriptive slugs such as:
        # /home-b61-nuovo-bilocale-a-jesolo-jesolo
        # /magnolia-trilocale-con-ampi-spazi-terrazza-e-privacy-a-jesolo-paese-jesolo
        slug = path.split("/")[-1]
        descriptive = len(slug) >= 18 and slug.count("-") >= 2
        property_signal = any(t in f"{slug} {label}".lower()
                              for t in JBC_TERMS)
        return descriptive or property_signal

    def is_hub_url(u, label=""):
        if not internal(u) or bad(u):
            return False
        path = urlparse(u).path.lower()
        if path.rstrip("/") in ("", "/404.php"):
            return True
        hay = f"{path} {label}".lower()
        return (
            any(x in path for x in HUB_PATHS)
            or any(x in hay for x in HUB_TEXT)
        )

    def add_hub(u, label=""):
        u = canonical(u)
        if not internal(u) or bad(u):
            return
        if u in visited_hubs or u in queued_hubs:
            return
        if is_hub_url(u, label) and len(queued_hubs) < max_hubs:
            queued_hubs.add(u)
            hub_queue.append(u)

    def add_detail(u, label=""):
        u = canonical(u)
        if not is_property_url(u, label):
            return
        detail_urls.setdefault(u, " ".join(str(label).split())[:500])

    def inspect_links(base):
        """Extract normal, lazy and data-* links without confusing properties with hubs."""
        rows = []
        try:
            rows = page.locator("a[href]").evaluate_all(
                """els => els.map(el => ({
                    href: el.getAttribute('href') || '',
                    text: (el.innerText || el.getAttribute('aria-label') ||
                           el.getAttribute('title') || '').trim()
                }))"""
            )
        except Exception as exc:
            errors.append(f"anchor-extract: {type(exc).__name__}: {exc!r}")

        raw = []
        for row in rows:
            raw.append((row.get("href", ""), row.get("text", "")))

        try:
            html = page.content()
            raw += [
                (h, "")
                for h in re.findall(
                    r'(?:href|data-href|data-url|data-link)\s*=\s*["\']([^"\']+)["\']',
                    html, flags=re.I
                )
            ]
        except Exception as exc:
            errors.append(f"html-link-extract: {type(exc).__name__}: {exc!r}")

        seen = set()
        for href, label in raw:
            href = str(href or "").strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            u = canonical(urljoin(base, href))
            if u in seen or not internal(u) or bad(u):
                continue
            seen.add(u)

            # Critical V10 ordering: classify as PROPERTY first.
            # A property title often contains "nuova", "residence", etc.;
            # V9 incorrectly promoted those links to hubs.
            if is_property_url(u, label):
                add_detail(u, label)
            elif is_hub_url(u, label):
                add_hub(u, label)

    def parse_sitemap_xml(text):
        urls = re.findall(
            r"<loc>\s*(https?://[^<\s]+)\s*</loc>",
            text or "", flags=re.I
        )
        if not urls:
            urls = re.findall(
                r"https?://(?:www\.)?jbcimmobiliare\.it/[^\s\"'<>]+",
                text or "", flags=re.I
            )
        return [canonical(x.replace("&amp;", "&")) for x in urls]

    def inspect_sitemaps():
        nonlocal pages, last_status, last_title
        found = 0
        queue = [
            "https://www.jbcimmobiliare.it/sitemap.xml",
            "https://www.jbcimmobiliare.it/sitemap_index.xml",
        ]
        visited_sm = set()

        while queue and found < max_sitemap_urls:
            sm = canonical(queue.pop(0))
            if sm in visited_sm:
                continue
            visited_sm.add(sm)
            try:
                response = page.goto(sm, wait_until="domcontentloaded", timeout=20000)
                last_status = response.status if response else last_status
                if response and response.status >= 400:
                    continue

                text = ""
                try:
                    text = page.locator("body").inner_text(timeout=5000) or ""
                except Exception:
                    pass
                if not text:
                    try:
                        text = page.content()
                    except Exception:
                        text = ""

                urls = parse_sitemap_xml(text)
                for u in urls[:max_sitemap_urls]:
                    if not internal(u) or bad(u):
                        continue
                    path = urlparse(u).path.lower()
                    # Sitemap indexes can point to other sitemap XML files.
                    if path.endswith(".xml") or "sitemap" in path:
                        if u not in visited_sm and len(queue) < 30:
                            queue.append(u)
                        continue
                    if is_property_url(u):
                        before = len(detail_urls)
                        add_detail(u, "sitemap")
                        found += int(len(detail_urls) > before)
            except Exception as exc:
                errors.append(f"sitemap {sm}: {type(exc).__name__}: {exc!r}")

        return found

    def inspect_hub(u):
        nonlocal pages, last_status, last_title
        try:
            response = page.goto(u, wait_until="domcontentloaded", timeout=35000)
            last_status = response.status if response else None
            last_title = page.title() or ""
            pages += 1
            page.wait_for_timeout(800)

            # Trigger lazy cards.
            for _ in range(3):
                try:
                    page.mouse.wheel(0, 1800)
                    page.wait_for_timeout(400)
                except Exception:
                    break

            inspect_links(u)

            nxt = next_page(page, u, visited_hubs)
            if nxt:
                add_hub(nxt, "pagination")
        except PlaywrightTimeoutError:
            errors.append(f"hub {u}: TIMEOUT")
        except Exception as exc:
            errors.append(f"hub {u}: {type(exc).__name__}: {exc!r}")

    # Homepage must be crawled even if sitemap navigation moves the browser.
    for s in starts:
        add_hub(s, "start")

    # Sitemap is supplementary, never the only discovery mechanism.
    sitemap_found = inspect_sitemaps()

    while hub_queue and len(visited_hubs) < max_hubs:
        u = hub_queue.pop(0)
        if u in visited_hubs:
            continue
        visited_hubs.add(u)
        inspect_hub(u)

    # Always re-open homepage after sitemap processing. This avoids V9's
    # browser being left on sitemap XML before normal navigation discovery.
    if canonical(JBC_HOME) not in visited_hubs and len(visited_hubs) < max_hubs:
        visited_hubs.add(canonical(JBC_HOME))
        inspect_hub(canonical(JBC_HOME))

    records = []
    visited_details = []

    # Detail capture is deliberately tolerant. A valid property page is kept
    # even if some fields cannot be parsed.
    idx = 0
    while idx < len(detail_urls) and len(visited_details) < max_details:
        u = list(detail_urls.keys())[idx]
        fallback = detail_urls.get(u, "")
        idx += 1
        if u in visited_details:
            continue
        visited_details.append(u)

        try:
            response = page.goto(u, wait_until="domcontentloaded", timeout=35000)
            status = response.status if response else None
            if status is not None and status >= 400:
                errors.append(f"detail {u}: HTTP {status}")
                continue

            page.wait_for_timeout(700)

            try:
                body = " ".join(
                    (page.locator("body").inner_text(timeout=8000) or "").split()
                )
            except Exception:
                body = ""

            try:
                title = page.title() or ""
            except Exception:
                title = ""

            # Some pages expose content only after a short render delay.
            if len(body) < 80:
                try:
                    page.wait_for_timeout(1500)
                    body = " ".join(
                        (page.locator("body").inner_text(timeout=8000) or "").split()
                    )
                except Exception:
                    pass

            low = f"{title} {body}".lower()

            if any(x in low for x in (
                "pagina non trovata", "errore 404", "page not found",
                "privacy policy", "cookie policy"
            )):
                continue

            # V10 does NOT require a price/area/rooms field to accept a
            # property. The normalization layer can retain partial records.
            if len(body) < 30 and not title:
                errors.append(f"detail {u}: empty body")
                continue

            records.append(detail(
                page, source, location, u,
                fallback=fallback or title
            ))

            # Discover related property pages from detail cards.
            inspect_links(u)

        except PlaywrightTimeoutError:
            errors.append(f"detail {u}: TIMEOUT")
        except Exception as exc:
            errors.append(f"detail {u}: {type(exc).__name__}: {exc!r}")

    ded = {r["source_url"]: r for r in records if r.get("source_url")}

    manifest = {
        "version": "JBC-V10-ROBUST-DISCOVERY",
        "source": source,
        "location": location,
        "start_urls": starts,
        "sitemap_urls_found": sitemap_found,
        "hub_pages_visited": len(visited_hubs),
        "hub_pages": sorted(visited_hubs),
        "candidate_detail_urls": len(detail_urls),
        "detail_pages_visited": len(visited_details),
        "records_captured": len(ded),
        "unit_records": sum(
            1 for r in ded.values() if r.get("record_type") == "UNIT"
        ),
        "project_records": sum(
            1 for r in ded.values() if r.get("record_type") == "PROJECT"
        ),
        "last_http_status": last_status,
        "last_title": last_title,
        "errors": errors[:150],
        "candidate_urls": list(detail_urls.keys())[:max_details],
    }

    try:
        debug.mkdir(parents=True, exist_ok=True)
        (debug / f"{location}__jbc_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        errors.append(f"manifest-write: {type(exc).__name__}: {exc!r}")

    ctx.close()

    return (
        list(ded.values()),
        pages,
        last_status,
        last_title,
        None if ded else "JBC V10 adapter found 0 detail records",
    )

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
