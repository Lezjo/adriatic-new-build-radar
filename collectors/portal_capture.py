from __future__ import annotations
import json, re, time, hashlib
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[2]
SOURCES = json.loads((ROOT/"radar/sources.json").read_text(encoding="utf-8"))

PRICE_RE = re.compile(r"(?:€\s*)?([\d\.\,]+)\s*(k|K)?(?:\s*€)?")
AREA_RE = re.compile(r"(\d{2,4}(?:[.,]\d+)?)\s*m(?:²|2)")
ROOM_RE = re.compile(r"(\d+)\s*(?:locali|camere|rooms?|stanze)", re.I)

def canonical(url: str) -> str:
    p=urlparse(url)
    q=[(k,v) for k,v in parse_qsl(p.query) if k.lower() not in {"utm_source","utm_medium","utm_campaign","utm_content","utm_term"}]
    return urlunparse((p.scheme,p.netloc,p.path.rstrip("/"),"",urlencode(q),""))

def parse_price(text):
    m=PRICE_RE.search(text.replace("\xa0"," "))
    if not m: return None
    n=float(m.group(1).replace(".","").replace(",","."))
    if m.group(2): n*=1000
    return int(n)

def parse_area(text):
    m=AREA_RE.search(text)
    return float(m.group(1).replace(".","").replace(",", ".")) if m else None

def extract_cards(page, source_name, location, page_url):
    # Generic extraction intentionally favors actual listing/property anchors.
    data=[]
    anchors=page.locator("a[href]").all()
    seen=set()
    for a in anchors:
        try:
            href=a.get_attribute("href")
            txt=" ".join((a.inner_text(timeout=1500) or "").split())
        except Exception:
            continue
        if not href or not txt: continue
        u=canonical(urljoin(page_url,href))
        if u in seen: continue
        low=(txt+" "+u).lower()
        # Heuristics for property cards.
        if not any(k in low for k in ("immobil","appart","ville","residen","nuova","costruzion","attico","bilocale","trilocale","quadrilocale")):
            continue
        if len(txt)<8: continue
        data.append({
            "source":source_name,
            "location":location,
            "listing_id":"url:"+hashlib.sha1(u.encode()).hexdigest(),
            "source_url":u,
            "listing_title":txt[:300],
            "price":parse_price(txt),
            "area_m2":parse_area(txt),
            "raw_text":txt[:1000],
            "captured_at":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        })
        seen.add(u)
    return data

def next_page_url(page, current, seen):
    # Prefer rel/aria pagination, then common page query patterns.
    candidates=[]
    for sel in [
        'a[rel="next"]','a[aria-label*="Successiva"]','a[aria-label*="Next"]',
        'a[title*="Successiva"]','a[title*="Next"]'
    ]:
        try:
            for a in page.locator(sel).all():
                href=a.get_attribute("href")
                if href: candidates.append(canonical(urljoin(current,href)))
        except Exception: pass
    for u in candidates:
        if u not in seen: return u
    return None

def capture_source(browser, location, spec):
    context=browser.new_context(
        locale="it-IT",
        user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    )
    page=context.new_page()
    results=[]
    seen_pages=set()
    url=spec["url"]
    for _ in range(spec.get("max_pages",100)):
        url=canonical(url)
        if url in seen_pages: break
        seen_pages.add(url)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            # Light scroll to trigger lazy-loaded cards.
            for _ in range(3):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(500)
            results.extend(extract_cards(page,spec["name"],location,url))
            nxt=next_page_url(page,url,seen_pages)
            if not nxt: break
            url=nxt
        except PlaywrightTimeoutError:
            # Keep already captured pages; do not fabricate data.
            break
        except Exception:
            break
    context.close()
    # Deduplicate within source by canonical URL.
    out={}
    for r in results: out[r["source_url"]]=r
    return list(out.values()), len(seen_pages)

def run():
    all_records=[]
    coverage=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        for location, specs in SOURCES.items():
            for spec in specs:
                rows,pages=capture_source(browser,location,spec)
                all_records.extend(rows)
                coverage.append({
                    "location":location,"source":spec["name"],"start_url":spec["url"],
                    "pages_captured":pages,"records_captured":len(rows)
                })
        browser.close()
    return all_records,coverage
