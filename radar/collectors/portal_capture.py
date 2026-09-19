"""
Adriatic New Build Radar — portal_capture.py
Version: 14.0-source-specific-recovery

Purpose
-------
Recover as much NEW-BUILD inventory as possible from the configured portals
without silently converting blocked/empty captures into successful inventory.

The collector keeps four separate concepts:
  1. discovery       = URL was found
  2. fetch           = detail/search page was reachable
  3. parse           = listing fields were extracted
  4. publish         = normalized row is safe to pass to snapshot.py

Important:
- 403/429/CAPTCHA/empty pages are explicit PARTIAL/BROKEN states.
- Search-engine fallback is evidence-based discovery; it never fabricates
  missing fields.
- JBC is crawled once globally and classified by municipality afterwards.
- Raw HTML is retained per immutable source run.
- Every source run has records_seen/parsed/normalized/published/rejected.
- Portal-specific URL patterns and selectors are used before generic logic.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import (
    parse_qsl,
    quote_plus,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DEBUG = DATA / "debug"
RAW = DATA / "raw"

SOURCES = json.loads(
    (ROOT / "radar" / "sources.json").read_text(encoding="utf-8")
)

COLLECTOR_VERSION = "portal-capture-v14.0"

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "fbclid",
    "gclid",
    "campaign",
    "source",
}

BAD_PATHS = (
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
    "/faq",
    "/blog",
    "/chi-siamo",
)

ASSET_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
    ".css",
    ".js",
    ".xml",
    ".ico",
)

PRICE_RE = re.compile(
    r"(?:€\s*)?([\d\.,]+)\s*(k)?|([\d\.,]+)\s*k?\s*€",
    re.I,
)
AREA_RE = re.compile(
    r"(?:superficie|superficie commerciale|mq|m²|m2)"
    r"\s*[:\-]?\s*(\d{2,4}(?:[.,]\d+)?)"
    r"|\b(\d{2,4}(?:[.,]\d+)?)\s*m(?:²|2)\b",
    re.I,
)
ROOM_RE = re.compile(
    r"\b(\d+)\s*(?:camere|stanze|locali|rooms?)\b",
    re.I,
)
BED_RE = re.compile(
    r"\b(\d+)\s*(?:camere\s*da\s*letto|camere|bedrooms?)\b",
    re.I,
)
FLOOR_RE = re.compile(
    r"(?:piano|floor)\s*(?:numero\s*)?([A-Za-z0-9°\-]+)",
    re.I,
)
ENERGY_RE = re.compile(
    r"(?:classe\s*energetica|classe|energia)"
    r"\s*[:\-]?\s*(A4|A3|A2|A1|A|B|C|D|E|F|G)\b",
    re.I,
)
UNIT_RE = re.compile(
    r"\b(?:unità|unita|interno|lotto|unit)"
    r"\s*[:#]?\s*([A-Z]?\d+(?:[.\-]\d+)?)\b",
    re.I,
)

PROMO_TERMS = (
    "ribassato",
    "ribasso",
    "prezzo precedente",
    "anziché",
    "anziche",
    "sconto",
    "offerta",
    "promozione",
    "promo",
    "occasione",
    "prezzo speciale",
    "ultimo prezzo",
    "ridotto",
)

FEATURES = {
    "parking": ("posto auto", "parcheggio", "parking"),
    "garage": ("garage", "box auto", "autorimessa"),
    "pool": ("piscina",),
    "elevator": ("ascensore", "elevatore"),
    "pv_present": (
        "pannelli fotovoltaici",
        "fotovoltaico",
        "fotovoltaica",
        "impianto fotovoltaico",
        "pannelli solari",
    ),
    "heat_pump": (
        "pompa di calore",
        "pompe di calore",
    ),
    "sea_view": (
        "vista mare",
        "vista sul mare",
        "fronte mare",
    ),
    "terrace": (
        "terrazza",
        "terrazzo",
        "balcone",
    ),
    "garden": (
        "giardino",
        "verde privato",
    ),
    "ev_charging": (
        "wallbox",
        "colonnina di ricarica",
        "ricarica elettrica",
    ),
    "air_conditioning": (
        "aria condizionata",
        "climatizzazione",
    ),
}

# Municipality detection is deliberately conservative:
# exact/compound municipality names first, then micro-location.
LOCATION_RULES = (
    (
        "cavallino-treporti",
        (
            "cavallino-treporti",
            "cavallino treporti",
            "ca' savio",
            "ca savio",
            "ca' vio",
            "ca vio",
            "punta sabbioni",
            "treporti",
        ),
    ),
    (
        "san-dona-di-piave",
        (
            "san donà di piave",
            "san dona di piave",
            "san dona",
        ),
    ),
    (
        "caorle",
        (
            "porto santa margherita",
            "lido altanea",
            "duna verde",
            "brussa",
            "caorle",
        ),
    ),
    (
        "treviso",
        (
            "treviso",
            "santa maria del rovere",
            "selvana",
            "monigo",
            "canizzano",
            "sant'antonino",
            "san zeno",
            "fiera",
        ),
    ),
    (
        "jesolo",
        (
            "jesolo",
            "jesolo lido",
            "lido di jesolo",
            "jesolo paese",
            "ca' gamba",
            "ca gamba",
            "cortellazzo",
            "piazza mazzini",
            "piazza brescia",
            "piazza trieste",
            "piazza drago",
            "piazza nember",
            "faro di jesolo",
        ),
    ),
)

MICRO_RULES = (
    ("jesolo", "Jesolo Paese", ("jesolo paese", "centro storico")),
    ("jesolo", "Lido di Jesolo", ("lido di jesolo", "jesolo lido")),
    ("jesolo", "Ca' Gamba", ("ca' gamba", "ca gamba")),
    ("jesolo", "Cortellazzo", ("cortellazzo",)),
    ("jesolo", "Piazza Nember / Faro", ("piazza nember", "faro di jesolo")),
    ("jesolo", "Pineta", ("pineta",)),
    ("jesolo", "Piazza Mazzini", ("piazza mazzini",)),
    ("jesolo", "Piazza Brescia", ("piazza brescia",)),
    ("jesolo", "Piazza Trieste", ("piazza trieste",)),
    ("jesolo", "Piazza Drago", ("piazza drago",)),
    ("cavallino-treporti", "Ca' Savio", ("ca' savio", "ca savio")),
    ("cavallino-treporti", "Ca' Vio", ("ca' vio", "ca vio")),
    ("cavallino-treporti", "Punta Sabbioni", ("punta sabbioni",)),
    ("cavallino-treporti", "Treporti", ("treporti",)),
    ("cavallino-treporti", "Cavallino", ("cavallino",)),
    ("san-dona-di-piave", "Mussetta", ("mussetta",)),
    ("san-dona-di-piave", "Calvecchia", ("calvecchia",)),
    ("san-dona-di-piave", "Fiorentina", ("fiorentina",)),
    ("treviso", "Santa Maria del Rovere", ("santa maria del rovere",)),
    ("treviso", "Selvana", ("selvana",)),
    ("treviso", "Monigo", ("monigo",)),
    ("treviso", "Canizzano", ("canizzano",)),
    ("treviso", "Sant'Antonino", ("sant'antonino",)),
    ("caorle", "Porto Santa Margherita", ("porto santa margherita",)),
    ("caorle", "Lido Altanea", ("lido altanea",)),
    ("caorle", "Duna Verde", ("duna verde",)),
    ("caorle", "Brussa", ("brussa",)),
    ("caorle", "Ponente", ("ponente",)),
    ("caorle", "Levante", ("levante",)),
)

PORTAL_RULES = {
    "immobiliare": {
        "hosts": ("immobiliare.it",),
        "hints": (
            "/annunci/",
            "/nuove-costruzioni/",
            "/nuova-costruzione/",
        ),
    },
    "idealista": {
        "hosts": ("idealista.it",),
        "hints": (
            "/immobile/",
            "/in-vendita/",
            "/nuove-costruzioni/",
            "/project/",
        ),
    },
    "casa": {
        "hosts": ("casa.it",),
        "hints": (
            "/immobile/",
            "/annuncio/",
            "/vendita/",
            "/nuove-costruzioni/",
        ),
    },
    "jbc": {
        "hosts": ("jbcimmobiliare.it",),
        "hints": (
            "/immobili/",
            "/immobile/",
            "/cantiere/",
            "/cantieri/",
            "/progetto/",
            "/progetti/",
            "/nuove-costruzioni/",
        ),
    },
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def make_run_id() -> str:
    return utc_now().strftime("%Y-%m-%dT%H-%M-%SZ")


def norm(value: str | None) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def canon(url: str) -> str:
    p = urlparse(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(
            p.query,
            keep_blank_values=True,
        )
        if key.lower() not in TRACKING_PARAMS
    ]
    return urlunparse(
        (
            p.scheme.lower(),
            p.netloc.lower(),
            p.path.rstrip("/"),
            "",
            urlencode(query),
            "",
        )
    )


def parse_num(value: str | None) -> float | None:
    if not value:
        return None

    try:
        text = value.strip()
        if "." in text and "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            suffix = text.rsplit(",", 1)[1]
            text = (
                text.replace(",", "")
                if len(suffix) == 3
                else text.replace(",", ".")
            )
        elif "." in text and len(text.rsplit(".", 1)[1]) == 3:
            text = text.replace(".", "")
        return float(text)
    except (ValueError, TypeError):
        return None


def parse_price(text: str | None) -> int | None:
    match = PRICE_RE.search(text or "")
    if not match:
        return None

    candidate = next(
        (
            item
            for item in match.groups()
            if item and re.search(r"\d", item)
        ),
        None,
    )
    if not candidate:
        return None

    value = parse_num(candidate)
    if value is None:
        return None

    if any(
        isinstance(item, str) and item.lower() == "k"
        for item in match.groups()
    ):
        value *= 1000

    return int(value) if 10_000 <= value <= 10_000_000 else None


def parse_area(text: str | None) -> float | None:
    match = AREA_RE.search(text or "")
    if not match:
        return None

    value = parse_num(match.group(1) or match.group(2))
    return value if value and 15 <= value <= 5000 else None


def parse_int(regex: re.Pattern[str], text: str | None) -> int | None:
    match = regex.search(text or "")
    return int(match.group(1)) if match else None


def parse_floor(text: str | None) -> str | None:
    match = FLOOR_RE.search(text or "")
    return match.group(1) if match else None


def parse_energy(text: str | None) -> str | None:
    match = ENERGY_RE.search(text or "")
    return match.group(1).upper() if match else None


def parse_unit(text: str | None) -> str | None:
    match = UNIT_RE.search(text or "")
    return match.group(1).upper() if match else None


def portal_type(source: str) -> str:
    value = source.lower()
    if "immobiliare" in value:
        return "immobiliare"
    if "idealista" in value:
        return "idealista"
    if "casa" in value:
        return "casa"
    if "jbc" in value:
        return "jbc"
    return "generic"


def is_bad_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return (
        any(fragment in path for fragment in BAD_PATHS)
        or path.endswith(ASSET_SUFFIXES)
    )


def is_listing_url(url: str, base_url: str, source: str) -> bool:
    if host(url) != host(base_url) or is_bad_url(url):
        return False

    path = urlparse(url).path.lower()
    if not path or path == "/":
        return False

    rules = PORTAL_RULES.get(
        portal_type(source),
        {"hints": ()},
    )

    if any(fragment in path for fragment in rules["hints"]):
        return True

    # Conservative generic fallback: long slug-like detail URLs.
    last = path.rstrip("/").split("/")[-1]
    return len(last) >= 18 and "-" in last


def is_pagination_url(url: str, base_url: str) -> bool:
    if host(url) != host(base_url) or is_bad_url(url):
        return False

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    if any(key in query for key in ("page", "pagina", "p", "pag")):
        return True

    return bool(
        re.search(
            r"/(?:page|pagina)[-/]?\d+",
            parsed.path.lower(),
        )
    )


def page_body(page) -> str:
    try:
        return norm(
            page.locator("body").inner_text(timeout=7000)
        )
    except Exception:
        return ""


def page_title(page, url: str) -> str:
    try:
        value = norm(page.title() or "")
    except Exception:
        value = ""

    if value:
        return value[:300]

    return norm(
        url.rstrip("/")
        .rsplit("/", 1)[-1]
        .replace("-", " ")
    )[:300]


def page_anchors(page) -> list[dict[str, str]]:
    try:
        return page.locator("a[href]").evaluate_all(
            """
            els => els.map(e => ({
              href: e.getAttribute('href') || '',
              text: (
                e.innerText ||
                e.getAttribute('aria-label') ||
                e.getAttribute('title') ||
                ''
              ).trim()
            }))
            """
        )
    except Exception:
        return []


def meta_text(page) -> list[str]:
    result: list[str] = []

    selectors = (
        "meta[property='og:title']",
        "meta[property='og:description']",
        "meta[name='description']",
    )

    for selector in selectors:
        try:
            node = page.locator(selector).first
            if node.count():
                content = node.get_attribute("content")
                if content:
                    result.append(norm(content))
        except Exception:
            continue

    return result


def jsonld_objects(page) -> list[dict]:
    result: list[dict] = []

    try:
        scripts = page.locator(
            'script[type="application/ld+json"]'
        ).all()

        for script in scripts:
            try:
                payload = json.loads(
                    script.text_content(timeout=1500) or ""
                )
            except Exception:
                continue

            if isinstance(payload, list):
                result.extend(
                    item for item in payload
                    if isinstance(item, dict)
                )
            elif isinstance(payload, dict):
                result.append(payload)
    except Exception:
        pass

    return result


def embedded_json_text(page) -> str:
    """
    Capture common SPA state without depending on one framework.
    This is especially useful when visible text is incomplete.
    """
    selectors = (
        "script#__NEXT_DATA__",
        "script[type='application/json']",
    )

    chunks: list[str] = []

    for selector in selectors:
        try:
            nodes = page.locator(selector).all()
            for node in nodes[:10]:
                value = node.text_content(timeout=1000)
                if value:
                    chunks.append(norm(value))
        except Exception:
            continue

    return " ".join(chunks)[:30000]


def goto(page, url: str) -> int | None:
    response = page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=35_000,
    )

    try:
        page.wait_for_load_state(
            "networkidle",
            timeout=7000,
        )
    except Exception:
        pass

    return response.status if response else None


def make_context(browser, referer: str | None = None):
    headers = {
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
        ),
        "Upgrade-Insecure-Requests": "1",
    }

    if referer:
        headers["Referer"] = referer

    context = browser.new_context(
        locale="it-IT",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        extra_http_headers=headers,
    )
    context.set_default_timeout(12_000)
    return context


def detect_block(status: int | None, text: str) -> str | None:
    if status in (401, 403):
        return f"http_{status}"

    if status == 429:
        return "http_429"

    low = text.lower()

    block_terms = (
        "captcha",
        "recaptcha",
        "access denied",
        "accesso negato",
        "too many requests",
        "temporarily blocked",
        "unusual traffic",
        "verify you are human",
        "verifica che sei umano",
    )

    for term in block_terms:
        if term in low:
            return f"blocked:{term}"

    return None


def discover_candidates(
    page,
    base_url: str,
    source: str,
) -> tuple[dict[str, str], list[str]]:
    candidates: dict[str, str] = {}
    pagination: list[str] = []
    seen: set[str] = set()

    for anchor in page_anchors(page):
        href = str(anchor.get("href") or "").strip()
        label = norm(str(anchor.get("text") or ""))

        if not href:
            continue

        if href.startswith(
            ("javascript:", "mailto:", "tel:", "#")
        ):
            continue

        url = canon(urljoin(base_url, href))

        if url in seen:
            continue

        seen.add(url)

        if is_pagination_url(url, base_url):
            pagination.append(url)
            continue

        if is_listing_url(url, base_url, source):
            candidates.setdefault(url, label)

    return candidates, pagination


def detect_location(
    text: str,
    fallback: str = "",
    url: str = "",
    title: str = "",
) -> str:
    """
    URL first, title second, body last.

    The ordering prevents a footer mentioning Jesolo from overriding
    a Caorle/Lido Altanea detail URL.
    """
    url_low = url.lower()
    title_low = norm(title).lower()
    body_low = norm(text).lower()

    for municipality, terms in LOCATION_RULES:
        if any(term in url_low for term in terms):
            return municipality

    for municipality, terms in LOCATION_RULES:
        if any(term in title_low for term in terms):
            return municipality

    # Require stronger body evidence than a single generic token.
    for municipality, terms in LOCATION_RULES:
        matches = sum(
            1 for term in terms
            if term in body_low
        )
        if matches >= 1:
            return municipality

    return fallback


def detect_micro_location(
    text: str,
    municipality: str,
) -> tuple[str | None, str, float]:
    low = norm(text).lower()

    for location, micro, terms in MICRO_RULES:
        if location != municipality:
            continue

        if any(term in low for term in terms):
            return micro, "VERIFIED_TEXT", 0.90

    return None, "UNVERIFIED", 0.0


def detect_features(text: str) -> list[str]:
    low = norm(text).lower()

    return [
        name
        for name, terms in FEATURES.items()
        if any(term in low for term in terms)
    ]


def detect_status(text: str) -> str:
    low = norm(text).lower()

    if any(
        term in low
        for term in (
            "pre-lancio",
            "prelaunch",
            "prossima costruzione",
            "in progetto",
        )
    ):
        return "PRE_LAUNCH"

    if any(
        term in low
        for term in (
            "cantiere",
            "lavori in corso",
            "in costruzione",
            "costruzione in corso",
        )
    ):
        return "UNDER_CONSTRUCTION"

    if any(
        term in low
        for term in (
            "progetto approvato",
            "permesso di costruire",
            "pua",
        )
    ):
        return "PLANNED"

    return "ACTIVE"


def jsonld_price(page) -> int | None:
    for item in jsonld_objects(page):
        offers = item.get("offers")

        candidates = []

        if isinstance(offers, dict):
            candidates.append(offers.get("price"))

        if isinstance(item, dict):
            candidates.append(item.get("price"))

        for value in candidates:
            try:
                number = float(
                    str(value)
                    .replace(".", "")
                    .replace(",", ".")
                )
                if 10_000 <= number <= 10_000_000:
                    return int(number)
            except Exception:
                continue

    return None


def promotion_data(
    text: str,
    current_price: int | None,
) -> dict:
    low = norm(text).lower()

    terms = sorted(
        {
            term
            for term in PROMO_TERMS
            if term in low
        }
    )

    old_price = None

    patterns = (
        r"(?:prezzo precedente|anziché|anziche)"
        r".{0,120}?([\d\.,]+)\s*€",
        r"(?:da)\s*([\d\.,]+)\s*€"
        r".{0,80}?(?:a|ora)\s*([\d\.,]+)\s*€",
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.I | re.S,
        )
        if not match:
            continue

        candidates = [
            group
            for group in match.groups()
            if group
        ]

        if candidates:
            old_price = parse_price(
                candidates[0] + " €"
            )
            if old_price:
                break

    amount = None
    percent = None

    if (
        old_price
        and current_price
        and old_price > current_price
    ):
        amount = old_price - current_price
        percent = round(
            amount / old_price * 100,
            2,
        )

    return {
        "detected": bool(
            terms or amount is not None
        ),
        "old_price": old_price,
        "new_price": current_price,
        "amount": amount,
        "percent": percent,
        "evidence_terms": terms,
    }


def raw_capture(
    page,
    run_id: str,
    url: str,
) -> tuple[bool, str | None]:
    listing_hash = hashlib.sha1(
        canon(url).encode("utf-8")
    ).hexdigest()

    target = RAW / run_id / f"{listing_hash}.html"

    try:
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        target.write_text(
            page.content(),
            encoding="utf-8",
        )
        return True, f"data/raw/{run_id}/{listing_hash}.html"
    except Exception:
        return False, None


def extract_listing(
    page,
    source: str,
    fallback_location: str,
    url: str,
    run_id: str,
    evidence: str = "",
    capture_method: str = "browser",
) -> dict:
    title = page_title(page, url)
    visible = page_body(page)
    embedded = embedded_json_text(page)

    combined = norm(
        " ".join(
            meta_text(page)
            + [
                title,
                visible,
                embedded,
                evidence,
            ]
        )
    )

    municipality = detect_location(
        combined,
        fallback=fallback_location,
        url=url,
        title=title,
    )

    micro, location_status, location_confidence = (
        detect_micro_location(
            combined,
            municipality,
        )
    )

    features = detect_features(combined)

    current_price = (
        jsonld_price(page)
        or parse_price(combined)
    )

    promo = promotion_data(
        combined,
        current_price,
    )

    area_m2 = parse_area(combined)
    bedroom_count = parse_int(
        BED_RE,
        combined,
    )
    room_count = parse_int(
        ROOM_RE,
        combined,
    )

    canonical = canon(url)
    listing_hash = hashlib.sha1(
        canonical.encode("utf-8")
    ).hexdigest()

    raw_ok, raw_path = raw_capture(
        page,
        run_id,
        canonical,
    )

    project_title = re.sub(
        r"\b(?:appartamento|trilocale|quadrilocale|"
        r"bilocale|attico|villa|villetta)\b.*",
        "",
        title,
        flags=re.I,
    ).strip().lower()

    if not project_title:
        project_title = title.lower()

    project_id = (
        "candidate-project:"
        + hashlib.sha1(
            f"{municipality}|{project_title}".encode(
                "utf-8"
            )
        ).hexdigest()
    )

    record_type = (
        "UNIT"
        if (
            parse_unit(combined)
            or bedroom_count is not None
            or room_count is not None
        )
        else "PROJECT"
    )

    return {
        "source": source,
        "source_run_id": run_id,
        "source_url": canonical,
        "listing_id": f"listing:{listing_hash}",
        "listing_title": title,
        "location": municipality,
        "micro_location": micro,
        "macro_zone": municipality,
        "location_verification_status": location_status,
        "location_verification_confidence": location_confidence,
        "project_id_candidate": project_id,
        "unit_id": parse_unit(combined),
        "record_type": record_type,
        "status": detect_status(combined),
        "price": current_price,
        "old_price": promo["old_price"],
        "area_m2": area_m2,
        "rooms": room_count,
        "bedrooms": bedroom_count,
        "floor": parse_floor(combined),
        "energy_class": parse_energy(combined),
        "features": features,
        "parking": "parking" in features,
        "garage": "garage" in features,
        "terrace": "terrace" in features,
        "pool": "pool" in features,
        "pv_present": "pv_present" in features,
        "heat_pump": "heat_pump" in features,
        "ev_charging": "ev_charging" in features,
        "sea_view": "sea_view" in features,
        "discount_signal": promo["detected"],
        "discount_keywords": promo["evidence_terms"],
        "promotion_text": (
            " ".join(promo["evidence_terms"])
            if promo["evidence_terms"]
            else None
        ),
        "promotion": promo,
        "raw_text": combined[:20_000],
        "raw_artifact": raw_path,
        "raw_capture": raw_ok,
        "capture_method": capture_method,
        "captured_at": now_iso(),
    }


def reason_counts(items: list[dict]) -> dict[str, int]:
    result: dict[str, int] = {}

    for item in items:
        reason = str(
            item.get("reason") or "unknown"
        )
        result[reason] = (
            result.get(reason, 0) + 1
        )

    return result


def write_source_debug(
    debug_dir: Path,
    source: str,
    location: str,
    report: dict,
) -> None:
    debug_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    safe_source = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        source,
    )
    safe_location = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        location,
    )

    path = (
        debug_dir
        / f"coverage_{safe_location}_{safe_source}.json"
    )

    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def source_search_queries(
    source: str,
    location: str,
) -> list[str]:
    ptype = portal_type(source)

    domain = {
        "immobiliare": "immobiliare.it",
        "idealista": "idealista.it",
        "casa": "casa.it",
    }.get(
        ptype,
        "immobiliare.it",
    )

    place = {
        "jesolo": "Jesolo Venezia",
        "caorle": "Caorle Venezia",
        "cavallino-treporti": (
            "Cavallino Treporti Venezia"
        ),
        "san-dona-di-piave": (
            "San Dona di Piave Venezia"
        ),
        "treviso": "Treviso",
    }.get(
        location,
        location.replace("-", " "),
    )

    return [
        f"site:{domain} {place} nuove costruzioni",
        f"site:{domain} {place} nuova costruzione trilocale",
        f"site:{domain} {place} nuova costruzione quadrilocale",
        f"site:{domain} {place} appartamento classe A4",
        f"site:{domain} {place} appartamento fotovoltaico",
    ]


def google_bing_discovery(
    browser,
    location: str,
    spec: dict,
    run_id: str,
    debug_dir: Path,
) -> tuple[dict[str, str], dict]:
    source = str(spec["name"])
    target = host(spec["url"])

    discovered: dict[str, str] = {}
    rejected: list[dict] = []
    errors: list[str] = []
    search_pages = 0

    context = make_context(browser)
    page = context.new_page()

    try:
        for query in source_search_queries(
            source,
            location,
        ):
            for template in (
                "https://www.google.com/search?q={}&num=100",
                "https://www.bing.com/search?q={}&count=50",
            ):
                try:
                    search_url = template.format(
                        quote_plus(query)
                    )
                    status = goto(
                        page,
                        search_url,
                    )
                    search_pages += 1

                    body_text = page_body(page)

                    if detect_block(
                        status,
                        body_text,
                    ):
                        continue

                    for anchor in page_anchors(page):
                        href = str(
                            anchor.get("href") or ""
                        ).strip()

                        if not href:
                            continue

                        # Search engines sometimes expose their own
                        # redirect URLs. Only accept the real portal host.
                        candidate = canon(href)

                        if (
                            host(candidate) == target
                            and is_listing_url(
                                candidate,
                                spec["url"],
                                source,
                            )
                        ):
                            discovered.setdefault(
                                candidate,
                                norm(
                                    str(
                                        anchor.get(
                                            "text"
                                        )
                                        or ""
                                    )
                                ),
                            )

                except Exception as exc:
                    errors.append(
                        f"search:{type(exc).__name__}:{exc}"
                    )

    finally:
        context.close()

    report = {
        "source": source,
        "location": location,
        "source_run_id": run_id,
        "collector": COLLECTOR_VERSION,
        "parser": portal_type(source),
        "capture_method": "search_engine_discovery",
        "search_pages_visited": search_pages,
        "records_seen": len(discovered),
        "records_parsed": 0,
        "records_normalized": 0,
        "records_published": 0,
        "records_rejected": len(rejected),
        "rejection_reasons": reason_counts(
            rejected
        ),
        "candidate_urls": list(discovered)[:1000],
        "errors": errors[:100],
    }

    write_source_debug(
        debug_dir,
        source,
        f"{location}_search_fallback",
        report,
    )

    return discovered, report


def search_fallback_capture(
    browser,
    location: str,
    spec: dict,
    run_id: str,
    debug_dir: Path,
) -> tuple[list[dict], dict]:
    source = str(spec["name"])

    discovered, discovery_report = (
        google_bing_discovery(
            browser,
            location,
            spec,
            run_id,
            debug_dir,
        )
    )

    max_results = int(
        spec.get(
            "fallback_max_results",
            150,
        )
    )

    rows: list[dict] = []
    rejected: list[dict] = []

    for url, evidence in list(
        discovered.items()
    )[:max_results]:
        context = make_context(
            browser,
            referer=spec["url"],
        )
        page = context.new_page()

        try:
            status = goto(
                page,
                url,
            )
            text = page_body(page)
            block_reason = detect_block(
                status,
                text,
            )

            if (
                block_reason
                or status is None
                or status >= 400
            ):
                # The URL is real discovery evidence,
                # but it is not safe to pretend that the
                # blocked detail page was parsed.
                rejected.append(
                    {
                        "url": url,
                        "reason": (
                            block_reason
                            or f"http_{status}"
                        ),
                        "evidence": evidence,
                    }
                )
                continue

            rows.append(
                extract_listing(
                    page,
                    source,
                    location,
                    url,
                    run_id,
                    evidence=evidence,
                    capture_method=(
                        "browser_fallback"
                    ),
                )
            )

        except Exception as exc:
            rejected.append(
                {
                    "url": url,
                    "reason": (
                        "fallback_detail:"
                        f"{type(exc).__name__}"
                    ),
                }
            )
        finally:
            context.close()

    report = {
        "source": source,
        "location": location,
        "source_run_id": run_id,
        "search_url": spec["url"],
        "collector": COLLECTOR_VERSION,
        "parser": portal_type(source),
        "capture_method": "search_engine_fallback",
        "records_seen": len(discovered),
        "records_parsed": len(rows),
        "records_normalized": len(rows),
        "records_published": len(rows),
        "records_rejected": len(rejected),
        "rejection_reasons": reason_counts(
            rejected
        ),
        "candidate_urls": list(discovered)[:1000],
        "raw_capture": sum(
            bool(row.get("raw_capture"))
            for row in rows
        ),
        "fallback_discovery": discovery_report,
        "status": (
            "PASS"
            if rows and not rejected
            else "PARTIAL"
            if rows
            else "BROKEN"
        ),
        "coverage": (
            "PASS"
            if rows and not rejected
            else "PARTIAL"
            if rows
            else "MISSING"
        ),
        "rejected": rejected[:1000],
    }

    write_source_debug(
        debug_dir,
        source,
        f"{location}_fallback",
        report,
    )

    return rows, report


def generic_portal_capture(
    browser,
    location: str,
    spec: dict,
    run_id: str,
    debug_dir: Path,
) -> tuple[list[dict], dict]:
    source = str(spec["name"])
    start_url = canon(spec["url"])

    context = make_context(browser)
    page = context.new_page()

    queue = [start_url]
    queued = {start_url}
    visited: set[str] = set()

    candidates: dict[str, str] = {}
    rejected: list[dict] = []
    errors: list[str] = []
    statuses: list[int | None] = []

    blocked_reason: str | None = None
    max_pages = int(
        spec.get(
            "max_pages",
            20,
        )
    )

    try:
        while (
            queue
            and len(visited) < max_pages
        ):
            current = queue.pop(0)

            if current in visited:
                continue

            visited.add(current)

            try:
                status = goto(
                    page,
                    current,
                )
                statuses.append(status)

                body_text = page_body(page)

                block_reason = detect_block(
                    status,
                    body_text,
                )

                if block_reason:
                    blocked_reason = block_reason
                    break

                if (
                    status is None
                    or status >= 400
                ):
                    rejected.append(
                        {
                            "url": current,
                            "reason": (
                                f"http_{status}"
                            ),
                        }
                    )
                    continue

                found, pagination = (
                    discover_candidates(
                        page,
                        current,
                        source,
                    )
                )

                for url, label in found.items():
                    candidates.setdefault(
                        url,
                        label,
                    )

                for next_url in pagination:
                    if (
                        next_url not in queued
                        and len(queued)
                        < max_pages * 3
                    ):
                        queued.add(next_url)
                        queue.append(next_url)

            except PlaywrightTimeoutError:
                errors.append(
                    f"timeout:{current}"
                )
            except Exception as exc:
                errors.append(
                    f"page:{type(exc).__name__}:{exc}"
                )

    finally:
        context.close()

    rows: list[dict] = []

    # Detail-page phase is separate from discovery.
    for url, label in list(
        candidates.items()
    )[: int(
        spec.get(
            "max_detail_pages",
            250,
        )
    )]:
        detail_context = make_context(
            browser,
            referer=start_url,
        )
        detail_page = detail_context.new_page()

        try:
            status = goto(
                detail_page,
                url,
            )
            body_text = page_body(
                detail_page
            )

            block_reason = detect_block(
                status,
                body_text,
            )

            if block_reason:
                rejected.append(
                    {
                        "url": url,
                        "reason": block_reason,
                        "label": label,
                    }
                )
                continue

            if (
                status is None
                or status >= 400
            ):
                rejected.append(
                    {
                        "url": url,
                        "reason": (
                            f"http_{status}"
                        ),
                        "label": label,
                    }
                )
                continue

            rows.append(
                extract_listing(
                    detail_page,
                    source,
                    location,
                    url,
                    run_id,
                    evidence=label,
                    capture_method="browser_detail",
                )
            )

        except PlaywrightTimeoutError:
            rejected.append(
                {
                    "url": url,
                    "reason": "detail_timeout",
                    "label": label,
                }
            )
        except Exception as exc:
            rejected.append(
                {
                    "url": url,
                    "reason": (
                        "detail:"
                        f"{type(exc).__name__}"
                    ),
                    "label": label,
                }
            )
        finally:
            detail_context.close()

    # If direct portal access is blocked OR produced no usable rows,
    # recover discovery through Google/Bing.
    fallback_rows: list[dict] = []
    fallback_report: dict = {}

    if (
        blocked_reason
        or not rows
        or len(rows) < int(
            spec.get(
                "fallback_if_below",
                3,
            )
        )
    ):
        (
            fallback_rows,
            fallback_report,
        ) = search_fallback_capture(
            browser,
            location,
            spec,
            run_id,
            debug_dir,
        )

        rows.extend(fallback_rows)

    # Exact source+URL dedup inside this source run.
    unique_rows: dict[
        tuple[str, str],
        dict,
    ] = {}

    for row in rows:
        url = row.get("source_url")
        if not url:
            continue

        unique_rows[
            (
                str(row.get("source")),
                str(url),
            )
        ] = row

    rows = list(unique_rows.values())

    direct_status = (
        "PASS"
        if rows
        and not blocked_reason
        and not fallback_rows
        else "PARTIAL"
        if rows
        else "BROKEN"
    )

    report = {
        "source": source,
        "location": location,
        "source_run_id": run_id,
        "search_url": start_url,
        "collector": COLLECTOR_VERSION,
        "parser": portal_type(source),
        "capture_method": (
            "browser_detail"
            if not fallback_rows
            else "browser_plus_search_fallback"
        ),
        "pages_visited": len(visited),
        "detail_candidates": len(candidates),
        "records_seen": len(candidates),
        "records_parsed": len(rows),
        "records_normalized": len(rows),
        "records_published": len(rows),
        "records_rejected": len(rejected),
        "rejection_reasons": reason_counts(
            rejected
        ),
        "raw_capture": sum(
            bool(row.get("raw_capture"))
            for row in rows
        ),
        "blocked_reason": blocked_reason,
        "http_statuses": statuses,
        "candidate_urls": list(
            candidates
        )[:1000],
        "errors": errors[:100],
        "fallback": fallback_report,
        "status": direct_status,
        "coverage": (
            "PASS"
            if direct_status == "PASS"
            else "PARTIAL"
            if rows
            else "MISSING"
        ),
        "manifest": True,
        "rejected": rejected[:1000],
    }

    write_source_debug(
        debug_dir,
        source,
        location,
        report,
    )

    return rows, report


def jbc_capture(
    browser,
    specs: list[dict],
    run_id: str,
    debug_dir: Path,
) -> tuple[list[dict], dict]:
    source = "jbc_direct"

    spec = specs[0] if specs else {}
    home = canon(
        spec.get(
            "url",
            "https://www.jbcimmobiliare.it/",
        )
    )

    context = make_context(browser)
    page = context.new_page()

    queue = [
        home,
        "https://www.jbcimmobiliare.it/nuove-costruzioni/",
    ]
    queued = set(queue)
    visited: set[str] = set()

    details: dict[str, str] = {}
    errors: list[str] = []
    rejected: list[dict] = []
    statuses: list[int | None] = []

    max_hub_pages = int(
        spec.get(
            "max_hub_pages",
            40,
        )
    )

    def is_jbc_detail(
        url: str,
        label: str,
    ) -> bool:
        if host(url) != "jbcimmobiliare.it":
            return False

        if is_bad_url(url):
            return False

        path = urlparse(url).path.lower()
        haystack = (
            path
            + " "
            + label.lower()
        )

        return (
            len(
                path.rstrip("/")
                .split("/")[-1]
            ) >= 16
            and (
                path.count("-") >= 2
                or any(
                    token in haystack
                    for token in (
                        "appartamento",
                        "trilocale",
                        "quadrilocale",
                        "bilocale",
                        "attico",
                        "villa",
                        "residenza",
                        "nuovo",
                        "progetto",
                    )
                )
            )
        )

    try:
        while (
            queue
            and len(visited) < max_hub_pages
        ):
            current = queue.pop(0)

            if current in visited:
                continue

            visited.add(current)

            try:
                status = goto(
                    page,
                    current,
                )
                statuses.append(status)

                if (
                    detect_block(
                        status,
                        page_body(page),
                    )
                    or (
                        status is not None
                        and status >= 400
                    )
                ):
                    continue

                for anchor in page_anchors(page):
                    href = str(
                        anchor.get("href") or ""
                    ).strip()
                    label = norm(
                        str(
                            anchor.get("text")
                            or ""
                        )
                    )

                    if (
                        not href
                        or href.startswith(
                            (
                                "javascript:",
                                "mailto:",
                                "tel:",
                                "#",
                            )
                        )
                    ):
                        continue

                    candidate = canon(
                        urljoin(
                            current,
                            href,
                        )
                    )

                    if host(candidate) != (
                        "jbcimmobiliare.it"
                    ):
                        continue

                    if is_bad_url(candidate):
                        continue

                    if is_jbc_detail(
                        candidate,
                        label,
                    ):
                        details.setdefault(
                            candidate,
                            label,
                        )
                        continue

                    path = urlparse(
                        candidate
                    ).path.lower()

                    if any(
                        token in path
                        for token in (
                            "immobili",
                            "vendita",
                            "cantiere",
                            "cantieri",
                            "progetti",
                            "progetto",
                            "nuove-costruzioni",
                        )
                    ):
                        if (
                            candidate not in queued
                            and len(queued)
                            < max_hub_pages * 3
                        ):
                            queued.add(candidate)
                            queue.append(candidate)

            except PlaywrightTimeoutError:
                errors.append(
                    f"hub_timeout:{current}"
                )
            except Exception as exc:
                errors.append(
                    f"hub:{type(exc).__name__}:{exc}"
                )

    finally:
        context.close()

    rows: list[dict] = []

    for url in list(details)[
        : int(
            spec.get(
                "max_detail_pages",
                400,
            )
        )
    ]:
        detail_context = make_context(
            browser,
            referer=home,
        )
        detail_page = detail_context.new_page()

        try:
            status = goto(
                detail_page,
                url,
            )

            text = page_body(
                detail_page
            )
            block_reason = detect_block(
                status,
                text,
            )

            if block_reason:
                rejected.append(
                    {
                        "url": url,
                        "reason": block_reason,
                    }
                )
                continue

            if (
                status is None
                or status >= 400
            ):
                rejected.append(
                    {
                        "url": url,
                        "reason": (
                            f"http_{status}"
                        ),
                    }
                )
                continue

            row = extract_listing(
                detail_page,
                source,
                "",
                url,
                run_id,
                evidence=details.get(
                    url,
                    "",
                ),
                capture_method="jbc_detail",
            )

            if row.get("location"):
                rows.append(row)
            else:
                rejected.append(
                    {
                        "url": url,
                        "reason": "location_unresolved",
                    }
                )

        except Exception as exc:
            rejected.append(
                {
                    "url": url,
                    "reason": (
                        "detail:"
                        f"{type(exc).__name__}"
                    ),
                }
            )
        finally:
            detail_context.close()

    # JBC is intentionally one global crawl.
    # Never duplicate it once per configured municipality.
    location_counts: dict[str, int] = {}

    for row in rows:
        location_name = (
            row.get("location")
            or "unknown"
        )
        location_counts[location_name] = (
            location_counts.get(
                location_name,
                0,
            )
            + 1
        )

    report = {
        "source": source,
        "location": "GLOBAL",
        "source_run_id": run_id,
        "search_url": home,
        "collector": COLLECTOR_VERSION,
        "parser": "jbc_global",
        "capture_method": "browser_global_crawl",
        "pages_visited": len(visited),
        "detail_candidates": len(details),
        "records_seen": len(details),
        "records_parsed": len(rows),
        "records_normalized": len(rows),
        "records_published": len(rows),
        "records_rejected": len(rejected),
        "rejection_reasons": reason_counts(
            rejected
        ),
        "raw_capture": sum(
            bool(row.get("raw_capture"))
            for row in rows
        ),
        "http_statuses": statuses,
        "location_counts": location_counts,
        "candidate_urls": list(details)[:1500],
        "errors": errors[:100],
        "status": (
            "PASS"
            if rows
            else "BROKEN"
        ),
        "coverage": (
            "PASS"
            if rows
            else "MISSING"
        ),
        "manifest": True,
        "rejected": rejected[:1000],
    }

    write_source_debug(
        debug_dir,
        source,
        "GLOBAL",
        report,
    )

    return rows, report


def source_specs() -> list[tuple[str, dict]]:
    result: list[tuple[str, dict]] = []

    for location, specs in SOURCES.items():
        for spec in specs:
            result.append(
                (
                    location,
                    spec,
                )
            )

    return result


def run():
    started = now_iso()
    run_id = make_run_id()

    debug_dir = (
        DEBUG
        / "capture"
        / run_id
    )
    debug_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    all_rows: list[dict] = []
    coverage: list[dict] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True
        )

        try:
            jbc_specs = [
                spec
                for _, spec in source_specs()
                if spec.get("name")
                == "jbc_direct"
            ]

            jbc_done = False

            for location, spec in source_specs():
                source = str(
                    spec.get(
                        "name",
                        "unknown",
                    )
                )

                try:
                    if (
                        source == "jbc_direct"
                    ):
                        if jbc_done:
                            continue

                        rows, report = jbc_capture(
                            browser,
                            jbc_specs,
                            run_id,
                            debug_dir,
                        )
                        jbc_done = True

                    else:
                        rows, report = (
                            generic_portal_capture(
                                browser,
                                location,
                                spec,
                                run_id,
                                debug_dir,
                            )
                        )

                    all_rows.extend(rows)
                    coverage.append(report)

                except Exception as exc:
                    report = {
                        "source": source,
                        "location": location,
                        "source_run_id": run_id,
                        "search_url": spec.get(
                            "url"
                        ),
                        "collector": COLLECTOR_VERSION,
                        "parser": portal_type(
                            source
                        ),
                        "pages_visited": 0,
                        "records_seen": 0,
                        "records_parsed": 0,
                        "records_normalized": 0,
                        "records_published": 0,
                        "records_rejected": 1,
                        "rejection_reasons": {
                            (
                                "collector_error:"
                                f"{type(exc).__name__}"
                            ): 1
                        },
                        "raw_capture": 0,
                        "manifest": False,
                        "status": "BROKEN",
                        "coverage": "BROKEN",
                        "errors": [repr(exc)],
                    }

                    coverage.append(report)

        finally:
            browser.close()

    # Exact listing URL dedup only. Cross-portal identity is deliberately
    # left to snapshot.py / project matching.
    dedup: dict[
        tuple[str, str],
        dict,
    ] = {}

    for row in all_rows:
        url = row.get("source_url")

        if not url:
            continue

        dedup[
            (
                str(row.get("source")),
                canon(str(url)),
            )
        ] = row

    normalized_rows = list(
        dedup.values()
    )

    manifest = {
        "schema_version": "14.0",
        "collector": COLLECTOR_VERSION,
        "source_run_id": run_id,
        "started_at": started,
        "finished_at": now_iso(),
        "configured_sources": sum(
            len(specs)
            for specs in SOURCES.values()
        ),
        "locations": sorted(
            SOURCES
        ),
        "records_seen": sum(
            int(
                report.get(
                    "records_seen",
                    0,
                )
                or 0
            )
            for report in coverage
        ),
        "records_parsed": sum(
            int(
                report.get(
                    "records_parsed",
                    0,
                )
                or 0
            )
            for report in coverage
        ),
        "records_normalized": len(
            normalized_rows
        ),
        "records_published": len(
            normalized_rows
        ),
        "records_rejected": sum(
            int(
                report.get(
                    "records_rejected",
                    0,
                )
                or 0
            )
            for report in coverage
        ),
        "raw_capture_files": sum(
            int(
                report.get(
                    "raw_capture",
                    0,
                )
                or 0
            )
            for report in coverage
        ),
        "status_counts": {
            status: sum(
                1
                for report in coverage
                if report.get(
                    "status"
                )
                == status
            )
            for status in sorted(
                {
                    report.get("status")
                    for report in coverage
                    if report.get("status")
                }
            )
        },
        "by_source": {},
        "by_location": {},
        "coverage": coverage,
    }

    for report in coverage:
        source = str(
            report.get(
                "source",
                "unknown",
            )
        )

        source_entry = manifest[
            "by_source"
        ].setdefault(
            source,
            {
                "runs": 0,
                "records_seen": 0,
                "records_parsed": 0,
                "records_normalized": 0,
                "records_published": 0,
                "records_rejected": 0,
                "statuses": {},
            },
        )

        source_entry["runs"] += 1

        for key in (
            "records_seen",
            "records_parsed",
            "records_normalized",
            "records_published",
            "records_rejected",
        ):
            source_entry[key] += int(
                report.get(key, 0)
                or 0
            )

        status = str(
            report.get(
                "status",
                "UNKNOWN",
            )
        )

        source_entry["statuses"][status] = (
            source_entry["statuses"].get(
                status,
                0,
            )
            + 1
        )

    for row in normalized_rows:
        location = str(
            row.get(
                "location",
                "unknown",
            )
        )

        location_entry = manifest[
            "by_location"
        ].setdefault(
            location,
            {
                "records": 0,
                "sources": {},
            },
        )

        location_entry["records"] += 1

        source = str(
            row.get(
                "source",
                "unknown",
            )
        )

        location_entry[
            "sources"
        ][source] = (
            location_entry[
                "sources"
            ].get(
                source,
                0,
            )
            + 1
        )

    (debug_dir / "manifest.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (DEBUG / "capture_debug.json").write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return (
        normalized_rows,
        coverage,
    )


if __name__ == "__main__":
    rows, reports = run()

    print(
        json.dumps(
            {
                "collector": COLLECTOR_VERSION,
                "rows": len(rows),
                "source_runs": len(
                    reports
                ),
                "by_status": {
                    status: sum(
                        1
                        for report in reports
                        if report.get(
                            "status"
                        )
                        == status
                    )
                    for status in sorted(
                        {
                            report.get(
                                "status"
                            )
                            for report in reports
                            if report.get(
                                "status"
                            )
                        }
                    )
                },
                "by_source": {
                    source: sum(
                        1
                        for row in rows
                        if row.get(
                            "source"
                        )
                        == source
                    )
                    for source in sorted(
                        {
                            row.get("source")
                            for row in rows
                            if row.get("source")
                        }
                    )
                },
                "by_location": {
                    location: sum(
                        1
                        for row in rows
                        if row.get(
                            "location"
                        )
                        == location
                    )
                    for location in sorted(
                        {
                            row.get("location")
                            for row in rows
                            if row.get("location")
                        }
                    )
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
