from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DEBUG = DATA / "debug"
SOURCES = json.loads((ROOT / "radar" / "sources.json").read_text(encoding="utf-8"))

COLLECTOR_VERSION = "portal-capture-v12.0"

PRICE_RE = re.compile(r"(?:€\s*)?([\d\.\,]+)\s*(k|K)?(?:\s*€)?|([\d\.\,]+)\s*(k|K)?\s*€", re.I)
AREA_RE = re.compile(r"(?:superficie|superficie commerciale|mq|m²|m2)\s*[:\-]?\s*(\d{2,4}(?:[.,]\d+)?)|\b(\d{2,4}(?:[.,]\d+)?)\s*m(?:²|2)\b", re.I)
ROOM_RE = re.compile(r"\b(\d+)\s*(?:camere\s*(?:da\s*letto)?|camere|stanze|locali|rooms?)\b", re.I)
BEDROOM_RE = re.compile(r"\b(\d+)\s*(?:camere\s*da\s*letto|camere|bedrooms?)\b", re.I)
FLOOR_RE = re.compile(r"(?:piano|floor)\s*(?:numero\s*)?([A-Za-z0-9°\-]+)", re.I)
ENERGY_RE = re.compile(r"(?:classe\s*energetica|classe|energia)\s*[:\-]?\s*(A4|A3|A2|A1|A|B|C|D|E|F|G)\b", re.I)
UNIT_RE = re.compile(r"\b(?:unità|unita|interno|lotto|unit)\s*[:#]?\s*([A-Z]?\d+(?:[.\-]\d+)?)\b", re.I)

PROPERTY_PATH_HINTS = (
    "/annunci/", "/immobili/", "/immobile/", "/case/", "/appartamenti/",
    "/ville/", "/residenze/", "/residence/", "/nuove-costruzioni/",
    "/nuove_costruzioni/", "/vendita/", "/vendita-case/", "/vendita-nuove",
    "/nuova-costruzione/", "/project/", "/progetti/", "/nuove-costruzioni",
)
BAD_PATH_HINTS = (
    "/login", "/registr", "/contatti", "/contact", "/privacy", "/cookie",
    "/agenzie", "/agenzia", "/search", "/ricerca", "/map", "/mappa",
    "/franchising", "/newsletter", "/lavora-con-noi", "/faq", "/blog",
)
ASSET_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".css", ".js", ".xml", ".ico")

PROMO_TERMS = (
    "ribassato", "ribasso", "prezzo precedente", "sconto", "offerta",
    "offerta speciale", "promozione", "promo", "occasione", "prezzo speciale",
    "ultimo prezzo", "ridotto", "last units", "special price", "price reduced",
)

FEATURE_TERMS = {
    "parking": ("posto auto", "parcheggio", "parking"),
    "garage": ("garage", "box auto", "autorimessa"),
    "pool": ("piscina",),
    "elevator": ("ascensore", "elevatore"),
    "air_conditioning": ("aria condizionata", "climatizzazione", "clima"),
    "pv_present": ("pannelli fotovoltaici", "fotovoltaico", "fotovoltaica", "impianto fotovoltaico", "pannelli solari"),
    "heat_pump": ("pompa di calore", "pompe di calore"),
    "sea_view": ("vista mare", "vista sul mare", "fronte mare", "mare"),
    "terrace": ("terrazza", "terrazzo", "ampio terrazzo"),
    "garden": ("giardino", "area verde", "verde privato"),
    "ev_charging": ("ricarica auto elettrica", "wallbox", "colonnina di ricarica", "ricarica elettrica"),
}

MICRO_RULES = (
    ("jesolo", "jesolo paese", ("jesolo paese", "jesolo centro storico", "via roma", "piazza primo maggio")),
    ("jesolo", "lido di jesolo", ("lido di jesolo", "jesolo lido")),
    ("jesolo", "ca' gamba", ("ca' gamba", "ca gamba", "cà gamba")),
    ("jesolo", "cortellazzo", ("cortellazzo",)),
    ("jesolo", "piazza nember / faro", ("piazza nember", "piazza nember/faro", "faro di jesolo", "faro")),
    ("jesolo", "pineta", ("pineta",)),
    ("jesolo", "piazza mazzini", ("piazza mazzini",)),
    ("jesolo", "piazza brescia", ("piazza brescia",)),
    ("jesolo", "piazza trieste", ("piazza trieste",)),
    ("jesolo", "piazza drago", ("piazza drago",)),
    ("cavallino-treporti", "ca' savio", ("ca' savio", "ca savio")),
    ("cavallino-treporti", "ca' vio", ("ca' vio", "ca vio")),
    ("cavallino-treporti", "punta sabbioni", ("punta sabbioni",)),
    ("cavallino-treporti", "treporti", ("treporti",)),
    ("cavallino-treporti", "cavallino", ("cavallino",)),
    ("san-dona-di-piave", "mussetta", ("mussetta",)),
    ("san-dona-di-piave", "mussetta di sopra", ("mussetta di sopra",)),
    ("san-dona-di-piave", "calvecchia", ("calvecchia",)),
    ("san-dona-di-piave", "fiorentina", ("fiorentina",)),
    ("san-dona-di-piave", "fossà", ("fossà", "fossa")),
    ("san-dona-di-piave", "chiesanuova", ("chiesanuova",)),
    ("treviso", "santa maria del rovere", ("santa maria del rovere",)),
    ("treviso", "selvana", ("selvana",)),
    ("treviso", "fiera", ("fiera",)),
    ("treviso", "ghirada", ("ghirada",)),
    ("treviso", "monigo", ("monigo",)),
    ("treviso", "san zeno", ("san zeno",)),
    ("treviso", "sant'antonino", ("sant'antonino", "sant antonino")),
    ("treviso", "canizzano", ("canizzano",)),
    ("treviso", "casier", ("casier",)),
    ("caorle", "porto santa margherita", ("porto santa margherita",)),
    ("caorle", "lido altanea", ("lido altanea",)),
    ("caorle", "duna verde", ("duna verde",)),
    ("caorle", "brussa", ("brussa",)),
    ("caorle", "ponente", ("ponente",)),
    ("caorle", "levante", ("levante",)),
)

LOCATION_TERMS = (
    # Most specific municipalities first. Generic "jesolo" must never win over
    # an explicit Caorle/Cavallino/San Dona/Treviso municipality.
    ("cavallino-treporti", (
        "cavallino-treporti", "cavallino treporti", "cavallino-treporti",
        "ca' savio", "ca savio", "ca' vio", "ca vio", "punta sabbioni",
        "treporti"
    )),
    ("san-dona-di-piave", (
        "san donà di piave", "san dona di piave", "san dona",
    )),
    ("caorle", (
        "caorle", "porto santa margherita", "lido altanea", "duna verde",
    )),
    ("treviso", (
        "treviso", "santa maria del rovere", "selvana", "monigo",
        "canizzano", "sant'antonino", "san zeno", "fiera",
    )),
    ("jesolo", (
        "jesolo", "jesolo lido", "lido di jesolo", "jesolo paese",
        "ca' gamba", "ca gamba", "cortellazzo", "piazza mazzini",
        "piazza brescia", "piazza trieste", "piazza drago",
        "piazza nember", "faro di jesolo",
    )),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(url: str) -> str:
    p = urlparse(url)
    q = [
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "gclid"}
    ]
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", urlencode(q), ""))


def host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def normalize_text(value: str) -> str:
    return " ".join((value or "").replace("\xa0", " ").split())


def parse_price(text: str):
    if not text:
        return None
    m = PRICE_RE.search(text.replace("\xa0", " "))
    if not m:
        return None
    number = next((x for x in m.groups() if x and re.search(r"[\d]", x)), None)
    suffix = next((x for x in m.groups() if isinstance(x, str) and x.lower() == "k"), None)
    if not number:
        return None
    try:
        n = number
        if "." in n and "," in n:
            n = n.replace(".", "").replace(",", ".")
        elif "," in n:
            tail = n.rsplit(",", 1)[1]
            n = n.replace(",", "") if len(tail) == 3 else n.replace(",", ".")
        elif "." in n and len(n.rsplit(".", 1)[1]) == 3:
            n = n.replace(".", "")
        value = float(n) * (1000 if suffix else 1)
        return int(value) if 10000 <= value <= 10000000 else None
    except (TypeError, ValueError):
        return None


def parse_area(text: str):
    if not text:
        return None
    m = AREA_RE.search(text)
    if not m:
        return None
    value = m.group(1) or m.group(2)
    try:
        n = float(value.replace(".", "").replace(",", "."))
        return n if 15 <= n <= 5000 else None
    except ValueError:
        return None


def parse_rooms(text: str):
    m = ROOM_RE.search(text or "")
    return int(m.group(1)) if m else None


def parse_bedrooms(text: str):
    m = BEDROOM_RE.search(text or "")
    if m:
        return int(m.group(1))
    # Common Italian listing wording: "3 camere".
    m = re.search(r"\b(\d+)\s+camere\b", text or "", re.I)
    return int(m.group(1)) if m else None


def parse_floor(text: str):
    m = FLOOR_RE.search(text or "")
    return m.group(1) if m else None


def parse_energy(text: str):
    m = ENERGY_RE.search(text or "")
    return m.group(1).upper() if m else None


def parse_unit_id(text: str):
    m = UNIT_RE.search(text or "")
    return m.group(1).upper() if m else None


def parse_old_new_prices(text: str):
    old_price = None
    new_price = parse_price(text)
    patterns = (
        r"(?:prezzo\s+precedente|prima|anziché|anziche|da)\s*[:€]?\s*([\d\.\,]+)\s*€?",
        r"([\d\.\,]+)\s*€\s*(?:invece di|anziché|anziche)\s*([\d\.\,]+)\s*€",
    )
    for pattern in patterns:
        m = re.search(pattern, text or "", re.I)
        if not m:
            continue
        try:
            nums = [parse_price(x + " €") for x in m.groups() if x]
            if len(nums) == 1:
                old_price = nums[0]
            elif len(nums) >= 2:
                new_price, old_price = nums[0], nums[1]
            break
        except Exception:
            pass
    if old_price is None:
        # Look for a crossed/previous price near promotion wording.
        m = re.search(r"(?:ribassato|prezzo precedente|sconto).*?([\d\.\,]+)\s*€", text or "", re.I)
        if m:
            old_price = parse_price(m.group(1) + " €")
    return old_price, new_price


def jsonld(page):
    result = []
    try:
        for node in page.locator('script[type="application/ld+json"]').all():
            try:
                value = json.loads(node.text_content(timeout=1200) or "")
                result.extend(value if isinstance(value, list) else [value])
            except Exception:
                continue
    except Exception:
        pass
    return result


def page_body(page) -> str:
    try:
        return normalize_text(page.locator("body").inner_text(timeout=5000))
    except Exception:
        return ""


def title_from_page(page, body: str, url: str) -> str:
    try:
        title = normalize_text(page.title() or "")
    except Exception:
        title = ""
    for node in jsonld(page):
        if isinstance(node, dict) and node.get("name"):
            kind = node.get("@type")
            if kind in ("Product", "Residence", "Apartment", "House", "SingleFamilyResidence", "RealEstateListing"):
                return normalize_text(str(node["name"]))[:300]
    if title:
        return title[:300]
    return normalize_text(url.rstrip("/").rsplit("/", 1)[-1].replace("-", " "))[:300]


def extract_meta(page) -> dict:
    data = {}
    try:
        for selector, key in (("meta[property='og:title']", "title"), ("meta[property='og:description']", "description"), ("meta[name='description']", "description")):
            loc = page.locator(selector).first
            if loc.count():
                value = loc.get_attribute("content")
                if value:
                    data[key] = normalize_text(value)
    except Exception:
        pass
    return data


def detect_location(text: str, fallback: str, url: str = "", title: str = ""):
    """
    Determine the municipality using a strict evidence hierarchy:
      1) URL/path
      2) page title/meta
      3) body/detail text
      4) configured fallback

    This prevents portal navigation/footer text (e.g. "Jesolo") from
    overriding an explicit Caorle/Treviso address.
    """
    fallback = fallback or ""
    url_low = (url or "").lower()
    title_low = normalize_text(title).lower()
    body_low = normalize_text(text).lower()

    # URL is the strongest source because portal detail URLs commonly encode
    # municipality or locality. Never infer from a generic homepage URL.
    url_terms = (
        ("cavallino-treporti", ("cavallino-treporti", "cavallino-treporti", "ca-savio", "ca-vio", "punta-sabbioni", "treporti")),
        ("san-dona-di-piave", ("san-dona-di-piave", "san-dona-di-piave", "mussetta", "calvecchia", "fiorentina")),
        ("caorle", ("caorle", "porto-santa-margherita", "lido-altanea", "duna-verde")),
        ("treviso", ("treviso", "santa-maria-del-rovere", "selvana", "monigo", "canizzano", "sant-antonino")),
        ("jesolo", ("jesolo", "lido-di-jesolo", "jesolo-lido", "jesolo-paese", "ca-gamba", "cortellazzo")),
    )
    for loc, terms in url_terms:
        if any(term in url_low for term in terms):
            return loc

    # Explicit title/address evidence is stronger than arbitrary body text.
    for evidence in (title_low,):
        for loc, terms in LOCATION_TERMS:
            if any(term in evidence for term in terms):
                return loc

    # Body evidence: require explicit municipality/locality signals before
    # falling back. Specific municipalities are ordered before Jesolo.
    for loc, terms in LOCATION_TERMS:
        if any(term in body_low for term in terms):
            return loc

    return fallback


def detect_micro_location(text: str, location: str):
    low = (text or "").lower()
    for loc, micro, terms in MICRO_RULES:
        if loc == location and any(term in low for term in terms):
            return micro, "VERIFIED_TEXT", 0.90
    return None, "UNVERIFIED", 0.0


def detect_features(text: str):
    low = (text or "").lower()
    found = []
    for feature, terms in FEATURE_TERMS.items():
        if any(term in low for term in terms):
            found.append(feature)
    return found


def infer_status(text: str):
    low = (text or "").lower()
    if any(x in low for x in ("pre-lancio", "prelaunch", "pre-lancio", "prossima costruzione", "prossime realizzazioni", "in progetto")):
        return "PRE_LAUNCH"
    if any(x in low for x in ("cantiere", "lavori in corso", "in costruzione", "costruzione in corso")):
        return "UNDER_CONSTRUCTION"
    if any(x in low for x in ("progetto approvato", "permesso di costruire", "pua", "urbanizzazione")):
        return "PLANNED"
    return "ACTIVE"


def is_bad_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(x in path for x in BAD_PATH_HINTS) or path.endswith(ASSET_SUFFIXES)


def generic_listing_url(url: str, base: str) -> bool:
    if host(url) != host(base) or is_bad_url(url):
        return False
    path = urlparse(url).path.lower()
    if not path or path == "/":
        return False
    if any(h in path for h in PROPERTY_PATH_HINTS):
        return True
    slug = path.rstrip("/").split("/")[-1]
    return len(slug) >= 16 and "-" in slug


def pagination_url(url: str, base: str) -> bool:
    if host(url) != host(base) or is_bad_url(url):
        return False
    p = urlparse(url)
    q = dict(parse_qsl(p.query))
    path = p.path.lower()
    return any(k in q for k in ("page", "pagina", "p", "pag")) or bool(re.search(r"/page[-/]?\d+", path))


def anchor_rows(page):
    try:
        return page.locator("a[href]").evaluate_all(
            """els => els.map(el => ({href: el.getAttribute('href') || '', text: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim()}))"""
        )
    except Exception:
        return []


def scroll_page(page):
    try:
        for _ in range(5):
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(350)
    except Exception:
        pass


def accept_cookies(page):
    labels = ("Accetta", "Accetta tutti", "Accept", "Accept all", "OK", "Consenti", "Agree")
    for label in labels:
        try:
            button = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first
            if button.count() and button.is_visible(timeout=500):
                button.click(timeout=1000)
                return
        except Exception:
            continue


def goto(page, url):
    response = page.goto(url, wait_until="domcontentloaded", timeout=35000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    accept_cookies(page)
    scroll_page(page)
    status = response.status if response else None
    return status


def extract_listing(page, source: str, configured_location: str, url: str, source_run_id: str):
    body = page_body(page)
    meta = extract_meta(page)
    title = title_from_page(page, body, url)
    full_text = normalize_text(" ".join(x for x in (meta.get("title"), meta.get("description"), title, body) if x))

    location = detect_location(body, configured_location, url=url, title=title)
    micro, verification, confidence = detect_micro_location(full_text, location)

    # Preserve the exact observed detail response. This is evidence for later
    # audit/reprocessing and is intentionally written before normalization.
    raw_dir = DATA / "raw" / source_run_id
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_id = hashlib.sha1(canonical(url).encode()).hexdigest()
    raw_path = raw_dir / f"{raw_id}.html"
    try:
        raw_path.write_text(page.content(), encoding="utf-8")
        raw_capture = True
    except Exception:
        raw_capture = False

    price = None
    for node in jsonld(page):
        if isinstance(node, dict):
            offers = node.get("offers")
            if isinstance(offers, dict) and offers.get("price") is not None:
                try:
                    price = int(float(str(offers["price"]).replace(",", ".")))
                    break
                except Exception:
                    pass
            if node.get("price") is not None:
                try:
                    price = int(float(str(node["price"]).replace(",", ".")))
                    break
                except Exception:
                    pass

    price = price or parse_price(full_text)
    area = parse_area(full_text)
    rooms = parse_rooms(full_text)
    bedrooms = parse_bedrooms(full_text)
    floor = parse_floor(full_text)
    energy = parse_energy(full_text)
    unit_id = parse_unit_id(full_text)
    features = detect_features(full_text)
    old_price, detected_new_price = parse_old_new_prices(full_text)
    if detected_new_price is not None:
        price = price or detected_new_price

    discount_keywords = [term for term in PROMO_TERMS if term in full_text.lower()]
    status = infer_status(full_text)
    listing_id = "listing:" + hashlib.sha1(canonical(url).encode()).hexdigest()

    project_key = normalize_text(
        re.sub(
            r"\b(?:appartamento|trilocale|quadrilocale|bilocale|attico|villa|villetta)\b.*",
            "",
            title,
            flags=re.I,
        )
    ).lower()
    if len(project_key) < 8:
        project_key = title.lower()

    project_id = "candidate-project:" + hashlib.sha1(
        f"{location}|{project_key}".encode()
    ).hexdigest()

    raw_ref = f"data/raw/{source_run_id}/{raw_id}.html"

    return {
        "source": source,
        "source_run_id": source_run_id,
        "source_url": canonical(url),
        "listing_id": listing_id,
        "listing_title": title,
        "location": location,
        "micro_location": micro,
        "macro_zone": micro or location,
        "location_verification_status": verification,
        "location_verification_confidence": confidence,
        "project_id_candidate": project_id,
        "unit_id": unit_id,
        "record_type": "UNIT" if unit_id or bedrooms is not None or rooms is not None else "PROJECT",
        "status": status,
        "price": price,
        "old_price": old_price,
        "area_m2": area,
        "rooms": rooms,
        "bedrooms": bedrooms,
        "floor": floor,
        "energy_class": energy,
        "features": features,
        "parking": "parking" in features,
        "garage": "garage" in features,
        "terrace": "terrace" in features,
        "pool": "pool" in features,
        "pv_present": "pv_present" in features,
        "heat_pump": "heat_pump" in features,
        "ev_charging": "ev_charging" in features,
        "sea_view": "sea_view" in features,
        "discount_signal": bool(discount_keywords) or old_price is not None,
        "discount_keywords": discount_keywords,
        "promotion_text": next((term for term in discount_keywords), None),
        "promotion": {
            "detected": bool(discount_keywords) or old_price is not None,
            "old_price": old_price,
            "new_price": price,
            "amount": (old_price - price) if old_price and price and old_price > price else None,
            "percent": round((old_price - price) / old_price * 100, 2)
                if old_price and price and old_price > price else None,
            "evidence_terms": discount_keywords,
        },
        "raw_text": full_text[:12000],
        "raw_artifact": raw_ref if raw_capture else None,
        "raw_capture": raw_capture,
        "captured_at": now_iso(),
    }


def candidate_links(page, base_url: str, source_name: str):
    candidates = []
    pages = []
    seen = set()
    for row in anchor_rows(page):
        href = str(row.get("href") or "").strip()
        label = normalize_text(str(row.get("text") or ""))
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absolute = canonical(urljoin(base_url, href))
        if absolute in seen:
            continue
        seen.add(absolute)
        if pagination_url(absolute, base_url):
            pages.append(absolute)
        elif generic_listing_url(absolute, base_url):
            candidates.append((absolute, label))
    return candidates, pages


def source_parser_kind(source_name: str):
    name = source_name.lower()
    if name == "jbc_direct":
        return "jbc"
    if "immobiliare" in name:
        return "immobiliare"
    if "idealista" in name:
        return "idealista"
    if "casa" in name:
        return "casa"
    return "generic"


def make_context(browser):
    ctx = browser.new_context(
        locale="it-IT",
        user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        extra_http_headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.7"},
    )
    ctx.set_default_timeout(12000)
    return ctx


def capture_generic(browser, location: str, spec: dict, run_id: str, debug_dir: Path):
    source = spec["name"]
    start_url = canonical(spec["url"])
    max_pages = max(1, min(int(spec.get("max_pages", 20)), 100))
    ctx = make_context(browser)
    page = ctx.new_page()
    page.set_default_navigation_timeout(35000)
    queue = [start_url]
    queued = {start_url}
    visited = set()
    candidates = {}
    errors = []
    pages_visited = 0
    http_statuses = []

    while queue and pages_visited < max_pages:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        try:
            status = goto(page, current)
            pages_visited += 1
            http_statuses.append(status)
            links, next_pages = candidate_links(page, current, source)
            for url, label in links:
                candidates.setdefault(url, label)
            for nxt in next_pages:
                if nxt not in queued and len(queued) < max_pages * 2:
                    queued.add(nxt)
                    queue.append(nxt)
        except (PlaywrightTimeoutError, Exception) as exc:
            errors.append(f"search_page:{current}:{type(exc).__name__}:{exc}")

    rows = []
    rejected = []
    for url, label in list(candidates.items()):
        try:
            status = goto(page, url)
            if status and status >= 400:
                rejected.append({"url": url, "reason": f"http_{status}"})
                continue
            row = extract_listing(page, source, location, url, run_id)
            if not row.get("source_url"):
                rejected.append({"url": url, "reason": "missing_source_url"})
                continue
            rows.append(row)
        except (PlaywrightTimeoutError, Exception) as exc:
            rejected.append({"url": url, "reason": f"detail_error:{type(exc).__name__}"})

    ctx.close()
    coverage = {
        "source": source,
        "location": location,
        "source_run_id": run_id,
        "search_url": start_url,
        "collector": COLLECTOR_VERSION,
        "parser": source_parser_kind(source),
        "pages_visited": pages_visited,
        "records_seen": len(candidates),
        "records_parsed": len(rows),
        "records_normalized": len(rows),
        "records_published": len(rows),
        "records_rejected": len(rejected),
        "rejection_reasons": _reason_counts(rejected),
        "raw_capture": sum(1 for r in rows if r.get("raw_capture")),
        "manifest": True,
        "status": "PASS" if rows else ("PARTIAL" if candidates else "BROKEN"),
        "coverage": "PASS" if rows else "MISSING",
        "http_statuses": http_statuses,
        "errors": errors[:30],
        "candidate_urls": list(candidates)[:500],
        "rejected": rejected[:500],
    }
    _write_debug(debug_dir, source, location, coverage)
    return rows, coverage


def capture_jbc_global(browser, specs: list[dict], run_id: str, debug_dir: Path):
    """
    Crawl JBC once per radar run, then classify each detail page by the actual
    municipality. The old implementation crawled the same JBC hub once for
    every configured location, which produced duplicate rows and contaminated
    Jesolo/Caorle classification.
    """
    source = "jbc_direct"
    spec = next((s for s in specs if s.get("name") == source), specs[0])
    home = canonical(spec.get("url") or "https://www.jbcimmobiliare.it/")
    max_hubs = max(5, min(int(spec.get("max_hub_pages", 30)), 100))
    max_details = max(20, min(int(spec.get("max_detail_pages", 300)), 500))

    ctx = make_context(browser)
    page = ctx.new_page()
    page.set_default_navigation_timeout(35000)

    queue = [
        home,
        "https://www.jbcimmobiliare.it/nuove-costruzioni/",
    ]
    queued = set(queue)
    visited = set()
    detail_urls = {}
    errors = []
    rejected = []
    pages_visited = 0
    http_statuses = []

    def internal(u):
        return host(u) == "jbcimmobiliare.it"

    def looks_like_property(u, label=""):
        if not internal(u) or is_bad_url(u):
            return False
        path = urlparse(u).path.strip("/").lower()
        if not path or path.endswith(".php"):
            return False
        hay = f"{path} {label}".lower()
        terms = (
            "nuovo", "nuova", "residenza", "residence", "progetto",
            "cantiere", "appartamento", "trilocale", "quadrilocale",
            "bilocale", "attico", "villa", "villette", "fronte-mare",
            "vista-mare",
        )
        return len(path.split("/")[-1]) >= 18 and (
            path.count("-") >= 2 or any(t in hay for t in terms)
        )

    while queue and pages_visited < max_hubs:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        try:
            status = goto(page, current)
            pages_visited += 1
            http_statuses.append(status)

            for row in anchor_rows(page):
                href = str(row.get("href") or "").strip()
                label = normalize_text(str(row.get("text") or ""))
                if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                    continue

                absolute = canonical(urljoin(current, href))
                if not internal(absolute) or is_bad_url(absolute):
                    continue

                if looks_like_property(absolute, label):
                    detail_urls.setdefault(absolute, label)
                else:
                    path = urlparse(absolute).path.lower()
                    hub_signal = any(
                        x in path for x in (
                            "immobili", "vendita", "cerca", "cantieri",
                            "cantiere", "progetti", "progetto",
                            "nuove-costruzioni", "nuove_costruzioni",
                        )
                    )
                    text_signal = any(
                        x in label.lower() for x in (
                            "immobili", "cantieri", "progetti",
                            "nuove costruzioni", "residenze",
                        )
                    )
                    if (
                        (hub_signal or text_signal)
                        and absolute not in queued
                        and len(queued) < max_hubs * 2
                    ):
                        queued.add(absolute)
                        queue.append(absolute)

        except Exception as exc:
            errors.append(
                f"hub:{current}:{type(exc).__name__}:{exc}"
            )

    rows = []

    for url, label in list(detail_urls.items())[:max_details]:
        try:
            status = goto(page, url)
            if status and status >= 400:
                rejected.append({
                    "url": url,
                    "reason": f"http_{status}",
                })
                continue

            # No configured municipality is passed here. Classification is
            # based on the actual detail page, so Caorle cannot inherit Jesolo.
            row = extract_listing(
                page,
                source,
                "",
                url,
                run_id,
            )

            if not row.get("source_url"):
                rejected.append({
                    "url": url,
                    "reason": "missing_source_url",
                })
                continue

            if not row.get("location"):
                rejected.append({
                    "url": url,
                    "reason": "location_unresolved",
                })
                continue

            rows.append(row)

        except (PlaywrightTimeoutError, Exception) as exc:
            rejected.append({
                "url": url,
                "reason": f"detail_error:{type(exc).__name__}",
            })

    ctx.close()

    by_location = {}
    for row in rows:
        by_location[row["location"]] = by_location.get(row["location"], 0) + 1

    coverage = {
        "source": source,
        "location": "GLOBAL",
        "configured_locations": sorted({
            str(location)
            for location, source_specs in SOURCES.items()
            if any(s.get("name") == source for s in source_specs)
        }),
        "source_run_id": run_id,
        "search_url": home,
        "collector": COLLECTOR_VERSION,
        "parser": "jbc_global",
        "pages_visited": pages_visited,
        "records_seen": len(detail_urls),
        "records_parsed": len(rows),
        "records_normalized": len(rows),
        "records_published": len(rows),
        "records_rejected": len(rejected),
        "rejection_reasons": _reason_counts(rejected),
        "raw_capture": sum(1 for r in rows if r.get("raw_capture")),
        "manifest": True,
        "status": "PASS" if rows else ("PARTIAL" if detail_urls else "BROKEN"),
        "coverage": "PASS" if rows else "MISSING",
        "http_statuses": http_statuses,
        "location_counts": by_location,
        "errors": errors[:30],
        "candidate_urls": list(detail_urls)[:1000],
        "rejected": rejected[:500],
    }

    _write_debug(debug_dir, source, "GLOBAL", coverage)
    return rows, coverage


def _reason_counts(items):
    counts = {}
    for item in items:
        reason = item.get("reason", "unknown") if isinstance(item, dict) else "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value)[:100]


def _write_debug(debug_dir: Path, source: str, location: str, coverage: dict):
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / f"coverage_{_safe_name(location)}_{_safe_name(source)}.json"
    path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")


def run():
    started_at = now_iso()
    run_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    debug_dir = DEBUG / "capture" / run_id
    debug_dir.mkdir(parents=True, exist_ok=True)

    all_records = []
    coverage = []
    jbc_done = False

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            # JBC is a global source. Crawl it exactly once, then classify each
            # detail page into its actual municipality.
            jbc_specs = [
                spec
                for specs in SOURCES.values()
                for spec in specs
                if spec.get("name") == "jbc_direct"
            ]

            for location, specs in SOURCES.items():
                for spec in specs:
                    source = spec.get("name", "unknown")

                    try:
                        if source == "jbc_direct":
                            if jbc_done:
                                coverage.append({
                                    "source": source,
                                    "location": location,
                                    "source_run_id": run_id,
                                    "search_url": spec.get("url"),
                                    "collector": COLLECTOR_VERSION,
                                    "parser": "jbc_global",
                                    "pages_visited": 0,
                                    "records_seen": 0,
                                    "records_parsed": 0,
                                    "records_normalized": 0,
                                    "records_published": 0,
                                    "records_rejected": 0,
                                    "rejection_reasons": {
                                        "global_crawl_already_completed": 1
                                    },
                                    "raw_capture": False,
                                    "manifest": True,
                                    "status": "SKIPPED",
                                    "coverage": "GLOBAL_SOURCE_ALREADY_CRAWLED",
                                })
                                continue

                            rows, report = capture_jbc_global(
                                browser,
                                jbc_specs,
                                run_id,
                                debug_dir,
                            )
                            jbc_done = True
                        else:
                            rows, report = capture_generic(
                                browser,
                                location,
                                spec,
                                run_id,
                                debug_dir,
                            )

                        all_records.extend(rows)
                        coverage.append(report)

                    except Exception as exc:
                        coverage.append({
                            "source": source,
                            "location": location,
                            "source_run_id": run_id,
                            "search_url": spec.get("url"),
                            "collector": COLLECTOR_VERSION,
                            "parser": source_parser_kind(source),
                            "pages_visited": 0,
                            "records_seen": 0,
                            "records_parsed": 0,
                            "records_normalized": 0,
                            "records_published": 0,
                            "records_rejected": 0,
                            "rejection_reasons": {
                                f"collector_error:{type(exc).__name__}": 1
                            },
                            "raw_capture": False,
                            "manifest": False,
                            "status": "BROKEN",
                            "coverage": "BROKEN",
                            "errors": [repr(exc)],
                        })
        finally:
            browser.close()

    # Exact duplicate listing records are removed only by
    # (source, canonical_url). Alternate URLs and cross-portal observations
    # remain available for the next identity/deduplication phase.
    dedup = {}
    for row in all_records:
        url = row.get("source_url")
        if url:
            dedup[(row.get("source"), url)] = row

    finished_at = now_iso()

    status_counts = {}
    for item in coverage:
        status = item.get("status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1

    manifest = {
        "source_run_id": run_id,
        "collector": COLLECTOR_VERSION,
        "started_at": started_at,
        "finished_at": finished_at,
        "configured_sources": sum(len(v) for v in SOURCES.values()),
        "locations": sorted(SOURCES),
        "records_seen": sum(x.get("records_seen", 0) for x in coverage),
        "records_parsed": sum(x.get("records_parsed", 0) for x in coverage),
        "records_normalized": len(dedup),
        "records_published": len(dedup),
        "records_rejected": sum(x.get("records_rejected", 0) for x in coverage),
        "raw_capture_files": sum(x.get("raw_capture", 0) for x in coverage),
        "status_counts": status_counts,
        "coverage": coverage,
    }

    (debug_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (DEBUG / "capture_debug.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return list(dedup.values()), coverage


if __name__ == "__main__":
    rows, coverage = run()
    print(json.dumps({
        "collector": COLLECTOR_VERSION,
        "rows": len(rows),
        "source_runs": len(coverage),
        "by_status": {status: sum(1 for x in coverage if x.get("status") == status) for status in sorted({x.get("status") for x in coverage})},
    }, ensure_ascii=False, indent=2))
