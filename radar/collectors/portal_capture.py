from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


ROOT = Path(__file__).resolve().parents[2]
SOURCES = json.loads((ROOT / "radar" / "sources.json").read_text(encoding="utf-8"))

PRICE_RE = re.compile(
    r"(?:€\s*)?([\d\.\,]+)\s*(k|K)?(?:\s*€)?",
    re.I,
)
AREA_RE = re.compile(r"(\d{2,4}(?:[.,]\d+)?)\s*m(?:²|2)", re.I)
ROOM_RE = re.compile(
    r"(\d+)\s*(?:locali|camere|rooms?|stanze)",
    re.I,
)

# Paths which commonly identify real property/listing pages.
PROPERTY_PATH_HINTS = (
    "/annunci/",
    "/immobili/",
    "/immobile/",
    "/case/",
    "/appartamenti/",
    "/ville/",
    "/residenze/",
    "/residence/",
    "/nuove-costruzioni/",
    "/nuove_costruzioni/",
    "/vendita/",
    "/vendita-case/",
    "/vendita-nuove",
    "/nuova-costruzione/",
    "/nuove-costruzioni",
    "/project/",
    "/progetti/",
)

# Navigation/utility links that should never become listings.
BAD_PATH_HINTS = (
    "/login",
    "/registr",
    "/contatti",
    "/contact",
    "/privacy",
    "/cookie",
    "/agenzie",
    "/agenzia",
    "/search",
    "/ricerca",
    "/map",
    "/mappa",
)


def canonical(url: str) -> str:
    p = urlparse(url)
    q = [
        (k, v)
        for k, v in parse_qsl(p.query)
        if k.lower()
        not in {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_content",
            "utm_term",
        }
    ]
    return urlunparse(
        (
            p.scheme,
            p.netloc,
            p.path.rstrip("/"),
            "",
            urlencode(q),
            "",
        )
    )


def parse_price(text: str):
    if not text:
        return None

    clean = text.replace("\xa0", " ")
    # Prefer amounts explicitly followed by euro.
    euro = re.search(
        r"€\s*([\d\.\,]+)\s*(k|K)?|([\d\.\,]+)\s*(k|K)?\s*€",
        clean,
        re.I,
    )
    m = euro or PRICE_RE.search(clean)
    if not m:
        return None

    groups = [g for g in m.groups() if g is not None]
    if not groups:
        return None

    number = groups[0]
    suffix = next((g for g in groups[1:] if g.lower() == "k"), None)

    try:
        # Italian price notation:
        # 399.000 -> 399000
        # 399,5 -> 399.5
        if "." in number and "," in number:
            number = number.replace(".", "").replace(",", ".")
        elif "," in number:
            if len(number.rsplit(",", 1)[1]) == 3:
                number = number.replace(",", "")
            else:
                number = number.replace(",", ".")
        elif "." in number:
            if len(number.rsplit(".", 1)[1]) == 3:
                number = number.replace(".", "")

        value = float(number)
        if suffix:
            value *= 1000

        # Reject obvious non-price values.
        if value < 10000 or value > 10000000:
            return None

        return int(value)
    except Exception:
        return None


def parse_area(text: str):
    if not text:
        return None

    m = AREA_RE.search(text)
    if not m:
        return None

    try:
        value = float(m.group(1).replace(".", "").replace(",", "."))
        if 15 <= value <= 5000:
            return value
    except Exception:
        pass

    return None


def parse_rooms(text: str):
    if not text:
        return None
    m = ROOM_RE.search(text)
    return int(m.group(1)) if m else None


def looks_like_property_url(url: str, source_url: str) -> bool:
    p = urlparse(url)
    base = urlparse(source_url)

    if p.scheme not in ("http", "https"):
        return False

    # Stay on the source's domain. This prevents social/external links.
    if p.netloc and base.netloc and p.netloc.lower() != base.netloc.lower():
        return False

    path = p.path.lower()

    if any(x in path for x in BAD_PATH_HINTS):
        return False

    if any(x in path for x in PROPERTY_PATH_HINTS):
        return True

    # Generic detail pages often have a long slug or numeric ID.
    # Do not require keywords in visible anchor text.
    last = path.rstrip("/").split("/")[-1]
    if re.search(r"\d", last) and len(last) >= 5:
        return True

    return False


def get_card_text(anchor, page):
    """
    Anchor text alone is frequently empty or only contains an image.
    Walk up a few ancestors and take the largest useful text block.
    """
    try:
        text = anchor.evaluate(
            """el => {
                let node = el;
                let best = "";
                for (let i = 0; i < 5 && node; i++, node = node.parentElement) {
                    const t = (node.innerText || "").replace(/\\s+/g, " ").trim();
                    if (t.length > best.length && t.length <= 2500) best = t;
                }
                return best;
            }"""
        )
        return " ".join((text or "").split())
    except Exception:
        try:
            return " ".join((anchor.inner_text(timeout=1000) or "").split())
        except Exception:
            return ""


def extract_cards(page, source_name, location, page_url):
    data = []
    seen = set()

    try:
        anchors = page.locator("a[href]").all()
    except Exception:
        return data

    for anchor in anchors:
        try:
            href = anchor.get_attribute("href")
        except Exception:
            continue

        if not href:
            continue

        u = canonical(urljoin(page_url, href))

        if u in seen:
            continue

        if not looks_like_property_url(u, page_url):
            continue

        text = get_card_text(anchor, page)

        # Some JS portals expose a URL but almost no card text.
        # The URL itself is still useful; title can be generated later.
        if len(text) < 5:
            text = u.rsplit("/", 1)[-1].replace("-", " ").strip()

        # Avoid capturing generic category pages.
        if len(text) < 5:
            continue

        price = parse_price(text)
        area = parse_area(text)
        rooms = parse_rooms(text)

        # A detail/listing URL is enough to retain the object even when
        # the portal hides price/area behind JS. Do not discard it.
        data.append(
            {
                "source": source_name,
                "location": location,
                "listing_id": "url:" + hashlib.sha1(u.encode()).hexdigest(),
                "source_url": u,
                "listing_title": text[:300],
                "price": price,
                "area_m2": area,
                "rooms": rooms,
                "raw_text": text[:2000],
                "captured_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            }
        )
        seen.add(u)

    return data


def next_page_url(page, current, seen):
    selectors = [
        'a[rel="next"]',
        'a[aria-label*="Successiva"]',
        'a[aria-label*="successiva"]',
        'a[aria-label*="Next"]',
        'a[title*="Successiva"]',
        'a[title*="successiva"]',
        'a[title*="Next"]',
    ]

    candidates = []

    for selector in selectors:
        try:
            for anchor in page.locator(selector).all():
                href = anchor.get_attribute("href")
                if href:
                    candidates.append(canonical(urljoin(current, href)))
        except Exception:
            pass

    for candidate in candidates:
        if candidate not in seen:
            return candidate

    return None


def capture_source(browser, location, spec):
    context = browser.new_context(
        locale="it-IT",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
    )

    page = context.new_page()
    results = []
    seen_pages = set()
    url = spec["url"]

    for _ in range(spec.get("max_pages", 100)):
        url = canonical(url)

        if url in seen_pages:
            break

        seen_pages.add(url)

        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            # Give client-side portals time to hydrate.
            page.wait_for_timeout(2500)

            # Trigger lazy-loaded result cards.
            for _ in range(5):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(600)

            results.extend(
                extract_cards(
                    page,
                    spec["name"],
                    location,
                    url,
                )
            )

            nxt = next_page_url(page, url, seen_pages)

            if not nxt:
                break

            url = nxt

        except PlaywrightTimeoutError:
            # Keep anything captured before the timeout.
            break

        except Exception:
            break

    context.close()

    # Deduplicate within a source.
    dedup = {}
    for row in results:
        dedup[row["source_url"]] = row

    return list(dedup.values()), len(seen_pages)


def run():
    all_records = []
    coverage = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        for location, specs in SOURCES.items():
            for spec in specs:
                rows, pages = capture_source(
                    browser,
                    location,
                    spec,
                )

                all_records.extend(rows)

                coverage.append(
                    {
                        "location": location,
                        "source": spec["name"],
                        "start_url": spec["url"],
                        "pages_captured": pages,
                        "records_captured": len(rows),
                    }
                )

        browser.close()

    return all_records, coverage
