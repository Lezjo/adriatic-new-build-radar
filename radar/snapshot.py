"""
Adriatic New Build Radar — snapshot.py
Version: 6.1-safe-partial-publish

Builds the canonical radar snapshot from portal_capture.run().

Design rules:
- Every execution gets an immutable data/runs/<run_id>/ directory.
- Daily data/history/YYYY-MM-DD.json remains as a compact summary.
- Source coverage is evidence, not an assumption.
- A partial/broken mandatory capture NEVER gets published as FULL INVENTORY.
- Project -> Unit -> Listing -> Observation is preserved.
- Rows without source_url are written to rejections.json/run rejections.
- Price history is evidence-based and exposes 1/7/30/90/180/365/all-time metrics.
- Existing historical files remain readable.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from radar.collectors.portal_capture import run as capture_run


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

CURRENT = DATA / "current.json"
OBJECTS = DATA / "objects.json"
PROJECTS = DATA / "projects.json"
UNITS = DATA / "units.json"
LISTINGS = DATA / "listings.json"
OBSERVATIONS = DATA / "observations.json"
REJECTIONS = DATA / "rejections.json"
PRICE_HISTORY = DATA / "price_history.json"

HISTORY = DATA / "history"
RUNS = DATA / "runs"
DEBUG = DATA / "debug"

SCHEMA_VERSION = "6.1"
RETENTION_DAYS = 365
BUDGET_TARGET = 400_000

MANDATORY_SOURCE_NAMES = {
    "immobiliare_new_build",
    "idealista_new_build",
    "idealista_projects",
    "casa_new_build",
    "jbc_direct",
}

PRICE_WINDOWS = (1, 7, 30, 90, 180, 365)

DISCOUNT_PATTERNS = (
    "ribassato",
    "ribasso",
    "prezzo ribassato",
    "prezzo precedente",
    "sconto",
    "offerta",
    "offerta speciale",
    "promozione",
    "promo",
    "occasione",
    "prezzo speciale",
    "ultimo prezzo",
    "ridotto",
)

LOCATION_REGISTRY: dict[str, dict[str, Any]] = {
    "jesolo": {
        "comune": "Jesolo",
        "province": "Venezia",
        "region": "Veneto",
        "micro_aliases": {
            "jesolo-paese": "Jesolo Paese",
            "jesolo-lido": "Jesolo Lido",
            "lido-di-jesolo": "Lido di Jesolo",
            "ca-gamba": "Ca' Gamba",
            "cortellazzo": "Cortellazzo",
            "pineta": "Pineta",
            "piazza-nember": "Piazza Nember / Faro",
            "faro": "Piazza Nember / Faro",
            "piazza-mazzini": "Piazza Mazzini",
            "piazza-brescia": "Piazza Brescia",
            "piazza-trieste": "Piazza Trieste",
            "piazza-drago": "Piazza Drago",
        },
    },
    "jesolo-paese": {
        "comune": "Jesolo",
        "province": "Venezia",
        "region": "Veneto",
        "micro_aliases": {"jesolo-paese": "Jesolo Paese"},
    },
    "caorle": {
        "comune": "Caorle",
        "province": "Venezia",
        "region": "Veneto",
        "micro_aliases": {
            "porto-santa-margherita": "Porto Santa Margherita",
            "lido-altanea": "Lido Altanea",
            "duna-verde": "Duna Verde",
            "caorle-centro": "Caorle Centro",
            "spiaggia-di-levante": "Spiaggia di Levante",
            "spiaggia-di-ponente": "Spiaggia di Ponente",
        },
    },
    "cavallino-treporti": {
        "comune": "Cavallino-Treporti",
        "province": "Venezia",
        "region": "Veneto",
        "micro_aliases": {
            "ca-savio": "Ca' Savio",
            "ca-vio": "Ca' Vio",
            "punta-sabbioni": "Punta Sabbioni",
            "treporti": "Treporti",
            "cavallino": "Cavallino",
        },
    },
    "san-dona-di-piave": {
        "comune": "San Donà di Piave",
        "province": "Venezia",
        "region": "Veneto",
        "micro_aliases": {
            "san-dona-centro": "San Donà Centro",
            "mussetta": "Mussetta",
            "mussetta-di-sopra": "Mussetta di Sopra",
            "calvecchia": "Calvecchia",
            "fiorentina": "Fiorentina",
            "fossa": "Fossà",
            "chiesanuova": "Chiesanuova",
        },
    },
    "treviso": {
        "comune": "Treviso",
        "province": "Treviso",
        "region": "Veneto",
        "micro_aliases": {
            "santa-maria-del-rovere": "Santa Maria del Rovere",
            "selvana": "Selvana",
            "fiera": "Fiera",
            "ghirada": "Ghirada",
            "monigo": "Monigo",
            "san-zeno": "San Zeno",
            "sant-antonino": "Sant'Antonino",
            "canizzano": "Canizzano",
            "casier": "Casier",
        },
    },
}

LOCATION_ALIASES = {
    "jesolo paese": "jesolo-paese",
    "jesolo": "jesolo",
    "jesolo lido": "jesolo-lido",
    "lido di jesolo": "jesolo",
    "caorle": "caorle",
    "cavallino treporti": "cavallino-treporti",
    "cavallino-treporti": "cavallino-treporti",
    "san dona di piave": "san-dona-di-piave",
    "san donà di piave": "san-dona-di-piave",
    "treviso": "treviso",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def run_id_for(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H-%M-%SZ")


def load(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    value = str(url).strip()
    return value.rstrip("/") if value else None


def slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[’'`]", "", text)
    text = re.sub(r"[^a-z0-9à-ÿ]+", "-", text)
    return text.strip("-")


def normalize_location(value: Any) -> str:
    raw = " ".join(
        str(value or "unknown")
        .strip()
        .lower()
        .replace("–", "-")
        .split()
    )
    return LOCATION_ALIASES.get(raw, raw.replace(" ", "-"))


def municipality_from_row(row: dict[str, Any]) -> str:
    for key in ("comune", "municipality", "location"):
        value = row.get(key)
        if value:
            normalized = normalize_location(value)
            if normalized in LOCATION_REGISTRY:
                return normalized
    return "unknown"


def normalize_micro_location(
    value: Any,
    municipality: str,
) -> str | None:
    if not value:
        return None
    raw = slug(value)
    aliases = LOCATION_REGISTRY.get(municipality, {}).get(
        "micro_aliases",
        {},
    )
    if raw in aliases:
        return aliases[raw]
    cleaned = " ".join(str(value).strip().split())
    return cleaned or None


def location_metadata(row: dict[str, Any]) -> dict[str, Any]:
    municipality = municipality_from_row(row)
    registry = LOCATION_REGISTRY.get(municipality, {})
    micro = normalize_micro_location(
        row.get("micro_location"),
        municipality,
    )

    status = row.get("location_verification_status")
    confidence = row.get("location_verification_confidence")

    if municipality == "unknown":
        status = status or "UNVERIFIED"
        confidence = confidence if confidence is not None else 0.0
    else:
        status = status or "SOURCE_DECLARED"
        confidence = confidence if confidence is not None else 0.70

    return {
        "region": registry.get("region"),
        "province": registry.get("province"),
        "comune": registry.get("comune"),
        "municipality": municipality,
        "macro_zone": municipality,
        "micro_location": micro,
        "location_verification_status": status,
        "location_verification_confidence": confidence,
        "address_evidence": str(row.get("raw_text") or "")[:1000],
    }


def normalized_title(title: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(title or "untitled listing"),
    ).strip()[:300]


def project_name_from_title(title: str) -> str:
    value = re.sub(
        r"\b(?:appartamento|trilocale|quadrilocale|bilocale|attico|"
        r"villa|villetta|penthouse|house)\b.*",
        "",
        title,
        flags=re.I,
    ).strip(" -|,:")
    return value if len(value) >= 5 else title


def stable_project_id(
    row: dict[str, Any],
    location: dict[str, Any],
) -> str:
    explicit = row.get("project_id") or row.get("project_id_candidate")
    if explicit:
        return str(explicit)

    municipality = location["municipality"]
    micro = slug(location.get("micro_location") or "")
    project_name = project_name_from_title(
        normalized_title(row.get("listing_title"))
    ).lower()

    return "project:" + sha1(
        f"{municipality}|{micro}|{project_name}"
    )


def stable_unit_id(
    row: dict[str, Any],
    project_id: str,
) -> str | None:
    source_unit = row.get("unit_id")
    if source_unit:
        return "unit:" + sha1(
            f"{project_id}|{str(source_unit).strip().lower()}"
        )

    fields = (
        row.get("floor"),
        row.get("area_m2"),
        row.get("bedrooms"),
        row.get("rooms"),
        bool(row.get("parking")),
        bool(row.get("garage")),
        bool(row.get("terrace")),
    )

    if any(value is not None for value in fields):
        return "unit-candidate:" + sha1(
            f"{project_id}|{fields}"
        )

    return None


def stable_listing_id(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "unknown").lower()
    source_listing_id = str(row.get("listing_id") or "").strip()

    # Collector listing_id is already URL-derived, but keeping the
    # source namespace prevents cross-portal collisions.
    if source_listing_id:
        return "listing:" + sha1(
            f"{source}|{source_listing_id}"
        )

    url = canonical_url(row.get("source_url")) or ""
    return "listing:" + sha1(f"{source}|{url}")


def extract_bedrooms(row: dict[str, Any]) -> int | None:
    value = row.get("bedrooms")
    if isinstance(value, (int, float)):
        return int(value)

    text = str(row.get("raw_text") or "")
    match = re.search(
        r"(\d+)\s*(?:camere da letto|camere|bedrooms?|stanze da letto)",
        text,
        re.I,
    )
    return int(match.group(1)) if match else None


def feature_bool(
    row: dict[str, Any],
    *names: str,
) -> bool:
    for name in names:
        if name in row and row.get(name) is True:
            return True

    features = {
        str(value).lower()
        for value in (row.get("features") or [])
    }
    text = str(row.get("raw_text") or "").lower()

    terms = {
        "parking": ("parcheggio", "posto auto"),
        "garage": ("garage", "box auto", "autorimessa"),
        "terrace": ("terrazza", "terrazzo", "balcone"),
        "pool": ("piscina",),
        "sea_view": ("vista mare", "fronte mare"),
        "pv": (
            "pannelli fotovoltaici",
            "fotovoltaico",
            "fotovoltaica",
            "pannelli solari",
        ),
        "heat_pump": ("pompa di calore", "pompe di calore"),
        "ev_charging": (
            "ricarica auto elettrica",
            "colonnina",
            "ev charging",
            "wallbox",
        ),
    }

    selected: list[str] = []
    for name in names:
        selected.extend(terms.get(name, ()))

    return any(
        term in features or term in text
        for term in selected
    )


def discount_evidence(row: dict[str, Any]) -> dict[str, Any]:
    text = str(row.get("raw_text") or "")
    low = text.lower()

    keywords = sorted(
        {
            pattern
            for pattern in DISCOUNT_PATTERNS
            if pattern in low
        }
    )

    old_price = row.get("old_price")
    new_price = row.get("price")

    try:
        old_price = int(float(old_price)) if old_price is not None else None
    except Exception:
        old_price = None

    try:
        new_price = int(float(new_price)) if new_price is not None else None
    except Exception:
        new_price = None

    amount = None
    percent = None
    if old_price and new_price and old_price > new_price:
        amount = old_price - new_price
        percent = round(amount / old_price * 100, 2)

    return {
        "signal": bool(
            amount is not None
            or (keywords and old_price is not None and new_price is not None)
        ),
        "keywords": keywords,
        "old_price": old_price,
        "new_price": new_price,
        "amount": amount,
        "percent": percent,
        "evidence_text": next(iter(keywords), None),
    }


def make_entities(
    rows: list[dict[str, Any]],
    captured_at: str,
    run_id: str,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    projects: dict[str, dict[str, Any]] = {}
    units: dict[str, dict[str, Any]] = {}
    listings: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for row in rows:
        url = canonical_url(row.get("source_url"))
        if not url:
            rejected.append({
                "run_id": run_id,
                "source": row.get("source"),
                "reason": "missing_source_url",
                "row": row,
            })
            continue

        location = location_metadata(row)
        title = normalized_title(row.get("listing_title"))
        project_id = stable_project_id(row, location)
        unit_id = stable_unit_id(row, project_id)
        listing_id = stable_listing_id(row)
        source = str(row.get("source") or "unknown")
        bedrooms = extract_bedrooms(row)
        promo = discount_evidence(row)

        if project_id not in projects:
            projects[project_id] = {
                "project_id": project_id,
                "name": project_name_from_title(title),
                "region": location["region"],
                "province": location["province"],
                "comune": location["comune"],
                "municipality": location["municipality"],
                "macro_zone": location["macro_zone"],
                "micro_location": location["micro_location"],
                "location_verification_status": location[
                    "location_verification_status"
                ],
                "location_verification_confidence": location[
                    "location_verification_confidence"
                ],
                "sources": [],
                "listing_ids": [],
                "unit_ids": [],
                "first_seen": captured_at,
                "last_seen": captured_at,
            }

        project = projects[project_id]
        if source not in project["sources"]:
            project["sources"].append(source)
        if listing_id not in project["listing_ids"]:
            project["listing_ids"].append(listing_id)
        if unit_id and unit_id not in project["unit_ids"]:
            project["unit_ids"].append(unit_id)
        project["last_seen"] = captured_at

        if unit_id:
            units.setdefault(
                unit_id,
                {
                    "unit_id": unit_id,
                    "project_id": project_id,
                    "source_unit_id": row.get("unit_id"),
                    "unit_label": row.get("unit_id"),
                    "bedrooms": bedrooms,
                    "rooms": row.get("rooms"),
                    "area_m2": row.get("area_m2"),
                    "floor": row.get("floor"),
                    "energy_class": row.get("energy_class"),
                    "parking": feature_bool(row, "parking"),
                    "garage": feature_bool(row, "garage"),
                    "terrace": feature_bool(row, "terrace"),
                    "pool": feature_bool(row, "pool"),
                    "pv_present": feature_bool(row, "pv"),
                    "heat_pump": feature_bool(row, "heat_pump"),
                    "ev_charging": feature_bool(row, "ev_charging"),
                    "first_seen": captured_at,
                    "last_seen": captured_at,
                },
            )
            unit = units[unit_id]
            for key, value in (
                ("bedrooms", bedrooms),
                ("rooms", row.get("rooms")),
                ("area_m2", row.get("area_m2")),
                ("floor", row.get("floor")),
                ("energy_class", row.get("energy_class")),
            ):
                if unit.get(key) is None and value is not None:
                    unit[key] = value
            unit["last_seen"] = captured_at

        existing_listing = listings.get(listing_id)
        observed_urls = (
            list(existing_listing.get("observed_urls", []))
            if existing_listing
            else []
        )
        if url not in observed_urls:
            observed_urls.append(url)

        listings[listing_id] = {
            "listing_id": listing_id,
            "project_id": project_id,
            "unit_id": unit_id,
            "source": source,
            "source_listing_id": row.get("listing_id"),
            "source_url": url,
            "observed_urls": observed_urls,
            "listing_title": title,
            "status": row.get("status") or "ACTIVE",
            "first_seen": (
                existing_listing.get("first_seen", captured_at)
                if existing_listing
                else captured_at
            ),
            "last_seen": captured_at,
            **location,
        }

        price = row.get("price")
        area = row.get("area_m2")
        eur_m2 = (
            round(price / area)
            if isinstance(price, (int, float))
            and isinstance(area, (int, float))
            and area > 0
            else None
        )

        observation_id = "obs:" + sha1(
            f"{run_id}|{listing_id}|{url}"
        )

        observations.append({
            "observation_id": observation_id,
            "run_id": run_id,
            "captured_at": captured_at,
            "listing_id": listing_id,
            "project_id": project_id,
            "unit_id": unit_id,
            "source": source,
            "source_url": url,
            "title": title,
            "price": price,
            "old_price": row.get("old_price"),
            "area_m2": area,
            "eur_m2": eur_m2,
            "bedrooms": bedrooms,
            "rooms": row.get("rooms"),
            "floor": row.get("floor"),
            "energy_class": row.get("energy_class"),
            "parking": feature_bool(row, "parking"),
            "garage": feature_bool(row, "garage"),
            "terrace": feature_bool(row, "terrace"),
            "pool": feature_bool(row, "pool"),
            "sea_view": feature_bool(row, "sea_view"),
            "pv_present": feature_bool(row, "pv"),
            "heat_pump": feature_bool(row, "heat_pump"),
            "ev_charging": feature_bool(row, "ev_charging"),
            "delivery": row.get("delivery"),
            "stage": row.get("stage"),
            "status": row.get("status") or "ACTIVE",
            "record_type": row.get("record_type"),
            "raw_capture": row.get("raw_capture"),
            "raw_artifact": row.get("raw_artifact"),
            "raw_text": str(row.get("raw_text") or "")[:12000],
            **location,
            "promotion": promo,
            "features": row.get("features") or [],
        })

        legacy.append({
            "object_id": "obj:" + sha1(listing_id),
            "location": location["municipality"],
            "comune": location["comune"],
            "municipality": location["municipality"],
            "project_id": project_id,
            "unit_id": unit_id,
            "listing_id": listing_id,
            "listing_title": title,
            "price": price,
            "old_price": row.get("old_price"),
            "area_m2": area,
            "eur_m2": eur_m2,
            "bedrooms": bedrooms,
            "rooms": row.get("rooms"),
            "floor": row.get("floor"),
            "energy_class": row.get("energy_class"),
            "parking": feature_bool(row, "parking"),
            "garage": feature_bool(row, "garage"),
            "terrace": feature_bool(row, "terrace"),
            "pool": feature_bool(row, "pool"),
            "sea_view": feature_bool(row, "sea_view"),
            "pv_present": feature_bool(row, "pv"),
            "heat_pump": feature_bool(row, "heat_pump"),
            "ev_charging": feature_bool(row, "ev_charging"),
            "status": row.get("status") or "ACTIVE",
            "source": source,
            "source_url": url,
            "micro_location": location["micro_location"],
            "location_verification_status": location[
                "location_verification_status"
            ],
            "location_verification_confidence": location[
                "location_verification_confidence"
            ],
            "first_seen": captured_at,
            "last_seen": captured_at,
            "discount_signal": promo["signal"],
            "discount_keywords": promo["keywords"],
        })

    return (
        projects,
        units,
        listings,
        observations,
        legacy,
        rejected,
    )


def load_price_history() -> dict[str, Any]:
    payload = load(
        PRICE_HISTORY,
        {
            "schema_version": "3.0",
            "retention_days": RETENTION_DAYS,
            "listings": {},
        },
    )
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_version", "3.0")
    payload.setdefault("retention_days", RETENTION_DAYS)
    payload.setdefault("listings", {})
    return payload


def update_price_history(
    payload: dict[str, Any],
    observations: list[dict[str, Any]],
    captured_at: str,
) -> dict[str, Any]:
    listings = payload.setdefault("listings", {})

    for obs in observations:
        price = obs.get("price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue

        listing_id = obs["listing_id"]
        history = listings.setdefault(
            listing_id,
            {
                "observations": [],
                "first_seen": captured_at,
                "last_seen": captured_at,
            },
        )

        history["observations"].append({
            "captured_at": captured_at,
            "price": price,
            "eur_m2": obs.get("eur_m2"),
            "source": obs.get("source"),
            "source_url": obs.get("source_url"),
            "run_id": obs.get("run_id"),
        })

        history["first_seen"] = min(
            history.get("first_seen", captured_at),
            captured_at,
        )
        history["last_seen"] = max(
            history.get("last_seen", captured_at),
            captured_at,
        )

        history["observations"] = sorted(
            history["observations"],
            key=lambda item: item.get("captured_at", ""),
        )

    # Do not destructively prune price history here. The immutable run
    # archive is retention-managed separately, while price history should
    # remain an evidence ledger for as long as the repository retains it.
    # This also prevents the field named "first" from silently becoming
    # "first observation within the last 365 days".
    return payload


def price_metrics(
    history: dict[str, Any],
    captured_at: str,
) -> dict[str, Any]:
    observations = history.get("observations", [])
    current = observations[-1] if observations else None

    prices = [
        item["price"]
        for item in observations
        if isinstance(item.get("price"), (int, float))
    ]

    result: dict[str, Any] = {
        "current": current.get("price") if current else None,
        "first_recorded": prices[0] if prices else None,
        "low_recorded": min(prices) if prices else None,
        "high_recorded": max(prices) if prices else None,
        "ledger_change": None,
        "ledger_change_percent": None,
        "observations_count": len(prices),
    }

    if len(prices) >= 2 and prices[0]:
        result["ledger_change"] = prices[-1] - prices[0]
        result["ledger_change_percent"] = round(
            (prices[-1] - prices[0]) / prices[0] * 100,
            2,
        )

    captured_dt = datetime.fromisoformat(
        captured_at.replace("Z", "+00:00")
    )

    for days in PRICE_WINDOWS:
        cutoff = captured_dt - timedelta(days=days)
        candidates = [
            item
            for item in observations
            if item.get("captured_at")
            and datetime.fromisoformat(
                item["captured_at"].replace("Z", "+00:00")
            ) >= cutoff
        ]

        key = f"{days}d"
        if candidates:
            oldest = candidates[0].get("price")
            latest = candidates[-1].get("price")
            result[key] = {
                "oldest": oldest,
                "latest": latest,
                "change": (
                    latest - oldest
                    if isinstance(oldest, (int, float))
                    and isinstance(latest, (int, float))
                    else None
                ),
                "change_percent": (
                    round((latest - oldest) / oldest * 100, 2)
                    if isinstance(oldest, (int, float))
                    and isinstance(latest, (int, float))
                    and oldest
                    else None
                ),
                "observations": len(candidates),
            }
        else:
            result[key] = {
                "oldest": None,
                "latest": None,
                "change": None,
                "change_percent": None,
                "observations": 0,
            }

    return result


def coverage_key(report: dict[str, Any]) -> tuple[str, str]:
    return (
        str(report.get("location") or "").lower(),
        str(report.get("source") or "").lower(),
    )


def evaluate_coverage(
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    reports = list(coverage)

    mandatory_expected: set[tuple[str, str]] = set()
    for location, specs in load_sources().items():
        for spec in specs:
            source = str(spec.get("name") or "")
            if source in MANDATORY_SOURCE_NAMES:
                mandatory_expected.add(
                    (str(location).lower(), source.lower())
                )

    # JBC is intentionally captured globally once. Its per-location SKIPPED
    # reports are not failures when the GLOBAL crawl itself passed.
    jbc_global = any(
        r.get("source") == "jbc_direct"
        and r.get("location") == "GLOBAL"
        and r.get("status") == "PASS"
        for r in reports
    )

    mandatory_results = []
    missing = []
    broken = []

    for location, source in sorted(mandatory_expected):
        if source == "jbc_direct" and jbc_global:
            mandatory_results.append({
                "location": location,
                "source": source,
                "status": "PASS",
                "reason": "global_jbc_capture_passed",
            })
            continue

        matching = [
            r for r in reports
            if coverage_key(r) == (location, source)
        ]

        if not matching:
            missing.append({
                "location": location,
                "source": source,
            })
            mandatory_results.append({
                "location": location,
                "source": source,
                "status": "MISSING",
                "reason": "no_source_run_report",
            })
            continue

        report = matching[-1]
        status = str(report.get("status") or "BROKEN").upper()

        if status != "PASS":
            broken.append({
                "location": location,
                "source": source,
                "status": status,
                "coverage": report.get("coverage"),
                "rejection_reasons": report.get(
                    "rejection_reasons",
                    {},
                ),
            })

        mandatory_results.append({
            "location": location,
            "source": source,
            "status": "PASS" if status == "PASS" else status,
            "reason": (
                "source_run_passed"
                if status == "PASS"
                else "source_run_not_pass"
            ),
        })

    all_mandatory_pass = not missing and not broken

    return {
        "mandatory_expected": sorted(
            [
                {
                    "location": location,
                    "source": source,
                }
                for location, source in mandatory_expected
            ],
            key=lambda x: (x["location"], x["source"]),
        ),
        "mandatory_results": mandatory_results,
        "missing": missing,
        "broken": broken,
        "mandatory_complete": all_mandatory_pass,
        "coverage_status": (
            "COMPLETE"
            if all_mandatory_pass
            else "INCOMPLETE"
        ),
        "full_inventory_allowed": all_mandatory_pass,
    }


def load_sources() -> dict[str, list[dict[str, Any]]]:
    path = ROOT / "radar" / "sources.json"
    payload = load(path, {})
    return payload if isinstance(payload, dict) else {}


def source_summary(
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {}

    for report in coverage:
        source = str(report.get("source") or "unknown")
        item = summary.setdefault(
            source,
            {
                "runs": 0,
                "pass": 0,
                "partial": 0,
                "broken": 0,
                "skipped": 0,
                "records_seen": 0,
                "records_parsed": 0,
                "records_normalized": 0,
                "records_published": 0,
                "records_rejected": 0,
            },
        )

        item["runs"] += 1
        status = str(report.get("status") or "").lower()
        if status in item:
            item[status] += 1

        for key in (
            "records_seen",
            "records_parsed",
            "records_normalized",
            "records_published",
            "records_rejected",
        ):
            item[key] += int(report.get(key) or 0)

    return summary


def build_inventory_metrics(
    projects: dict[str, dict[str, Any]],
    units: dict[str, dict[str, Any]],
    listings: dict[str, dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    prices = [
        o["price"]
        for o in observations
        if isinstance(o.get("price"), (int, float))
    ]

    return {
        "projects": len(projects),
        "units": len(units),
        "listings": len(listings),
        "observations": len(observations),
        "priced_observations": len(prices),
        "locations": sorted(
            {
                o.get("municipality")
                for o in observations
                if o.get("municipality")
            }
        ),
        "energy_class": {
            "A4": sum(
                1 for o in observations
                if str(o.get("energy_class") or "").upper() == "A4"
            ),
            "A3": sum(
                1 for o in observations
                if str(o.get("energy_class") or "").upper() == "A3"
            ),
            "A2": sum(
                1 for o in observations
                if str(o.get("energy_class") or "").upper() == "A2"
            ),
            "A1": sum(
                1 for o in observations
                if str(o.get("energy_class") or "").upper() == "A1"
            ),
        },
        "priority_profile": {
            "3_bedrooms": sum(
                1 for o in observations
                if o.get("bedrooms") == 3
            ),
            "pv_present": sum(
                1 for o in observations
                if o.get("pv_present") is True
            ),
            "heat_pump": sum(
                1 for o in observations
                if o.get("heat_pump") is True
            ),
            "parking_or_garage": sum(
                1 for o in observations
                if o.get("parking") is True
                or o.get("garage") is True
            ),
            "terrace": sum(
                1 for o in observations
                if o.get("terrace") is True
            ),
        },
        "price": {
            "min": min(prices) if prices else None,
            "max": max(prices) if prices else None,
            "median": (
                sorted(prices)[len(prices) // 2]
                if prices
                else None
            ),
            "budget_target": BUDGET_TARGET,
            "within_budget": sum(
                1 for price in prices
                if price <= BUDGET_TARGET
            ),
        },
    }


def purge_old_runs() -> None:
    cutoff = utc_now() - timedelta(days=RETENTION_DAYS)
    if not RUNS.exists():
        return

    for child in RUNS.iterdir():
        if not child.is_dir():
            continue

        try:
            run_dt = datetime.strptime(
                child.name,
                "%Y-%m-%dT%H-%M-%SZ",
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if run_dt < cutoff:
            for path in sorted(
                child.rglob("*"),
                reverse=True,
            ):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            child.rmdir()


def main() -> int:
    started = utc_now()
    run_id = run_id_for(started)
    captured_at = iso(started)

    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    try:
        rows, coverage = capture_run()
    except Exception as exc:
        failure = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "started_at": captured_at,
            "finished_at": iso(utc_now()),
            "status": "FAILED",
            "error": repr(exc),
        }
        write_json(run_dir / "run.json", failure)
        write_json(
            DEBUG / f"{run_id}_FAILED.json",
            failure,
        )
        raise

    projects, units, listings, observations, legacy, rejected = (
        make_entities(
            rows,
            captured_at,
            run_id,
        )
    )

    coverage_eval = evaluate_coverage(coverage)
    inventory_metrics = build_inventory_metrics(
        projects,
        units,
        listings,
        observations,
    )

    # Price history is updated only with actually captured observations.
    price_history = update_price_history(
        load_price_history(),
        observations,
        captured_at,
    )

    price_metrics_by_listing = {}
    for listing_id, history in price_history["listings"].items():
        price_metrics_by_listing[listing_id] = price_metrics(
            history,
            captured_at,
        )

    for observation in observations:
        observation["price_history_metrics"] = (
            price_metrics_by_listing.get(
                observation["listing_id"],
                {},
            )
        )

    run_payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": captured_at,
        "finished_at": iso(utc_now()),
        "status": "SUCCESS",
        "inventory_mode": (
            "FULL_INVENTORY"
            if coverage_eval["full_inventory_allowed"]
            else "PARTIAL_CAPTURE"
        ),
        "coverage_status": coverage_eval["coverage_status"],
        "full_inventory_allowed": coverage_eval[
            "full_inventory_allowed"
        ],
        "source_summary": source_summary(coverage),
        "coverage": coverage,
        "coverage_evaluation": coverage_eval,
        "metrics": inventory_metrics,
        "records": {
            "rows_captured": len(rows),
            "projects": len(projects),
            "units": len(units),
            "listings": len(listings),
            "observations": len(observations),
            "rejected": len(rejected),
        },
    }

    # Decide publication against the last known-good canonical snapshot.
    previous_current = load(CURRENT, {})
    previous_full = (
        isinstance(previous_current, dict)
        and previous_current.get("full_inventory_allowed") is True
        and previous_current.get("inventory_mode") == "FULL_INVENTORY"
    )
    publish_inventory = (
        run_payload["full_inventory_allowed"]
        or not previous_full
    )
    run_payload["public_inventory_action"] = (
        "PUBLISH_CURRENT_RUN"
        if publish_inventory
        else "KEEP_LAST_KNOWN_GOOD"
    )
    run_payload["last_known_good_run_id"] = (
        run_id
        if run_payload["full_inventory_allowed"]
        else previous_current.get("last_known_good_run_id")
    )

    # Immutable run artifacts.
    write_json(run_dir / "run.json", run_payload)
    write_json(
        run_dir / "projects.json",
        list(projects.values()),
    )
    write_json(
        run_dir / "units.json",
        list(units.values()),
    )
    write_json(
        run_dir / "listings.json",
        list(listings.values()),
    )
    write_json(
        run_dir / "observations.json",
        observations,
    )
    write_json(
        run_dir / "objects.json",
        legacy,
    )
    write_json(
        run_dir / "rejections.json",
        rejected,
    )
    write_json(
        run_dir / "coverage.json",
        coverage,
    )
    write_json(
        run_dir / "price_history.json",
        price_history,
    )

    # IMPORTANT: a partial capture must never replace the last known-good
    # canonical inventory. We still persist the partial run and its newly
    # observed price evidence, but the public inventory remains unchanged.
    previous_current = load(CURRENT, {})
    previous_full = (
        isinstance(previous_current, dict)
        and previous_current.get("full_inventory_allowed") is True
        and previous_current.get("inventory_mode") == "FULL_INVENTORY"
    )

    publish_inventory = (
        run_payload["full_inventory_allowed"]
        or not previous_full
    )

    if publish_inventory:
        write_json(PROJECTS, list(projects.values()))
        write_json(UNITS, list(units.values()))
        write_json(LISTINGS, list(listings.values()))
        write_json(OBSERVATIONS, observations)
        write_json(OBJECTS, legacy)
        write_json(REJECTIONS, rejected)
    else:
        # Keep the last complete inventory files intact. Save partial data
        # exclusively under the immutable run directory and expose its
        # status through current.json below.
        pass
    write_json(UNITS, list(units.values()))
    write_json(LISTINGS, list(listings.values()))
    write_json(OBSERVATIONS, observations)
    write_json(OBJECTS, legacy)
    write_json(REJECTIONS, rejected)
    write_json(PRICE_HISTORY, price_history)

    daily_summary = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "date": started.date().isoformat(),
        "captured_at": captured_at,
        "inventory_mode": run_payload["inventory_mode"],
        "coverage_status": run_payload["coverage_status"],
        "full_inventory_allowed": run_payload[
            "full_inventory_allowed"
        ],
        "metrics": inventory_metrics,
        "records": run_payload["records"],
        "source_summary": run_payload["source_summary"],
        "coverage_evaluation": coverage_eval,
        "immutable_run": f"data/runs/{run_id}/run.json",
    }

    # Keep the old daily location while making each execution immutable.
    write_json(
        HISTORY / f"{started.date().isoformat()}.json",
        daily_summary,
    )

    # Current is a presentation-oriented manifest, not a source of truth.
    if publish_inventory:
        public_projects = list(projects.values())
        public_units = list(units.values())
        public_listings = list(listings.values())
        public_observations = observations
        public_rejections = rejected
        public_metrics = inventory_metrics
    else:
        public_projects = previous_current.get(
            "projects",
            load(PROJECTS, []),
        )
        public_units = previous_current.get(
            "units",
            load(UNITS, []),
        )
        public_listings = previous_current.get(
            "listings",
            load(LISTINGS, []),
        )
        public_observations = previous_current.get(
            "observations",
            load(OBSERVATIONS, []),
        )
        public_rejections = previous_current.get(
            "rejections",
            load(REJECTIONS, []),
        )
        public_metrics = previous_current.get(
            "metrics",
            {},
        )

    current = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(utc_now()),
        "run_id": run_id,
        "inventory_mode": run_payload["inventory_mode"],
        "coverage_status": run_payload["coverage_status"],
        "full_inventory_allowed": run_payload[
            "full_inventory_allowed"
        ],
        "public_inventory_source": (
            "current_run"
            if publish_inventory
            else "last_known_good_full_run"
        ),
        "last_known_good_run_id": (
            run_id
            if publish_inventory and run_payload["full_inventory_allowed"]
            else previous_current.get("last_known_good_run_id")
        ),
        "budget_target": BUDGET_TARGET,
        "retention_days": RETENTION_DAYS,
        "metrics": public_metrics,
        "current_run_metrics": inventory_metrics,
        "source_summary": run_payload["source_summary"],
        "coverage_evaluation": coverage_eval,
        "projects": public_projects,
        "units": public_units,
        "listings": public_listings,
        "observations": public_observations,
        "rejections": public_rejections,
        "price_history": price_metrics_by_listing,
        "immutable_run": {
            "run_id": run_id,
            "path": f"data/runs/{run_id}/run.json",
        },
    }

    write_json(CURRENT, current)

    # Human-readable capture summary for Actions diagnostics.
    write_json(
        DEBUG / "capture_debug.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "inventory_mode": run_payload["inventory_mode"],
            "coverage_status": run_payload["coverage_status"],
            "full_inventory_allowed": run_payload[
                "full_inventory_allowed"
            ],
            "coverage_evaluation": coverage_eval,
            "source_summary": run_payload["source_summary"],
            "metrics": inventory_metrics,
            "immutable_run": f"data/runs/{run_id}/run.json",
        },
    )

    purge_old_runs()

    print(
        json.dumps(
            {
                "run_id": run_id,
                "inventory_mode": run_payload["inventory_mode"],
                "coverage_status": run_payload["coverage_status"],
                "full_inventory_allowed": run_payload[
                    "full_inventory_allowed"
                ],
                "projects": len(projects),
                "units": len(units),
                "listings": len(listings),
                "observations": len(observations),
                "rejected": len(rejected),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
