"""
Adriatic New Build Radar — snapshot.py
Version: 5.0-location-integrity

Purpose
-------
Build the canonical daily/immutable snapshot from portal_capture.run().

Critical rules:
1. comune/municipality and micro_location are NEVER interchangeable.
2. A successful pipeline does NOT mean successful market coverage.
3. Mandatory sources with HTTP 403/empty capture are explicitly BROKEN/MISSING.
4. Project -> Unit -> Listing -> Observation is preserved.
5. Existing history remains readable.
6. Price history is evidence-based; no fabricated baselines.
7. A row without source_url is persisted as a rejection record, never silently dropped.
"""

from __future__ import annotations

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from radar.collectors.portal_capture import run


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

SCHEMA_VERSION = "5.0"
RETENTION_DAYS = 365
BUDGET_TARGET = 400_000

MANDATORY_SOURCE_NAMES = {
    "immobiliare_new_build",
    "idealista_new_build",
    "idealista_projects",
    "casa_new_build",
    "jbc_direct",
}

# Canonical municipality registry.
# IMPORTANT: micro-locations live separately and must never overwrite comune.
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
            "monigo": "Monigo",
            "canizzano": "Canizzano",
            "sant-antonino": "Sant'Antonino",
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

PRICE_WINDOWS = (1, 7, 30, 90, 180, 365)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    """
    Return canonical municipality key.

    Priority:
      1. explicit municipality/comune
      2. collector's location
      3. conservative fallback

    NEVER use micro_location as municipality.
    """
    for key in ("comune", "municipality", "location"):
        value = row.get(key)
        if value:
            normalized = normalize_location(value)
            if normalized in LOCATION_REGISTRY:
                return normalized

    return "unknown"


def normalize_micro_location(value: Any, municipality: str) -> str | None:
    if not value:
        return None

    raw = slug(value)
    registry = LOCATION_REGISTRY.get(municipality, {})
    aliases = registry.get("micro_aliases", {})
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
    return re.sub(r"\s+", " ", str(title or "untitled listing")).strip()[:300]


def project_name_from_title(title: str) -> str:
    value = re.sub(
        r"\b(?:appartamento|trilocale|quadrilocale|bilocale|attico|"
        r"villa|villetta|penthouse|house)\b.*",
        "",
        title,
        flags=re.I,
    ).strip(" -|,:")
    return value if len(value) >= 5 else title


def stable_project_id(row: dict[str, Any], location: dict[str, Any]) -> str:
    explicit = row.get("project_id") or row.get("project_id_candidate")
    if explicit:
        return str(explicit)

    municipality = location["municipality"]
    micro = slug(location.get("micro_location") or "")
    title = normalized_title(row.get("listing_title"))
    project_name = project_name_from_title(title).lower()

    key = f"{municipality}|{micro}|{project_name}"
    return "project:" + sha1(key)


def stable_unit_id(row: dict[str, Any], project_id: str) -> str | None:
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

    if any(x is not None for x in fields):
        return "unit-candidate:" + sha1(f"{project_id}|{fields}")

    return None


def stable_listing_id(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "unknown").lower()
    source_listing_id = str(row.get("listing_id") or "")
    url = canonical_url(row.get("source_url")) or ""

    return "listing:" + sha1(
        f"{source}|{source_listing_id}|{url}"
    )


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


def feature_bool(row: dict[str, Any], *names: str) -> bool:
    for name in names:
        if name in row and row.get(name) is True:
            return True

    features = {str(value).lower() for value in (row.get("features") or [])}
    text = str(row.get("raw_text") or "").lower()

    terms = {
        "parking": ("parcheggio", "posto auto"),
        "garage": ("garage", "box auto"),
        "terrace": ("terrazza", "terrazzo", "balcone"),
        "pool": ("piscina",),
        "sea_view": ("vista mare", "fronte mare"),
        "pv": (
            "pannelli fotovoltaici",
            "fotovoltaico",
            "fotovoltaica",
            "pannelli solari",
        ),
        "heat_pump": ("pompa di calore",),
        "ev_charging": (
            "ricarica auto elettrica",
            "colonnina",
            "ev charging",
        ),
    }

    selected = []
    for name in names:
        if name in terms:
            selected.extend(terms[name])

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

    signal = bool(
        amount is not None
        or (keywords and old_price is not None and new_price is not None)
    )

    return {
        "signal": signal,
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
):
    projects: dict[str, dict[str, Any]] = {}
    units: dict[str, dict[str, Any]] = {}
    listings: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []
    objects: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        url = canonical_url(row.get("source_url"))

        if not url:
            rejections.append({
                "rejection_id": "reject:" + sha1(
                    f"{run_id}|{index}|missing-source-url"
                ),
                "run_id": run_id,
                "captured_at": captured_at,
                "source": row.get("source"),
                "rejected": True,
                "rejection_reason": "missing_source_url",
                "row": row,
            })
            continue

        location = location_metadata(row)
        municipality = location["municipality"]

        title = normalized_title(row.get("listing_title"))
        project_id = stable_project_id(row, location)
        unit_id = stable_unit_id(row, project_id)
        listing_id = stable_listing_id(row)
        source = str(row.get("source") or "unknown")

        if municipality == "unknown":
            rejections.append({
                "rejection_id": "reject:" + sha1(
                    f"{run_id}|{listing_id}|location"
                ),
                "run_id": run_id,
                "captured_at": captured_at,
                "source": source,
                "source_url": url,
                "rejected": True,
                "rejection_reason": "location_unresolved",
                "row": row,
            })

        project = projects.setdefault(
            project_id,
            {
                "project_id": project_id,
                "name": project_name_from_title(title),
                "region": location["region"],
                "province": location["province"],
                "comune": location["comune"],
                "municipality": municipality,
                "macro_zone": municipality,
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
            },
        )

        if source not in project["sources"]:
            project["sources"].append(source)
        if listing_id not in project["listing_ids"]:
            project["listing_ids"].append(listing_id)
        if unit_id and unit_id not in project["unit_ids"]:
            project["unit_ids"].append(unit_id)

        project["last_seen"] = captured_at

        if unit_id:
            unit = units.setdefault(
                unit_id,
                {
                    "unit_id": unit_id,
                    "project_id": project_id,
                    "source_unit_id": row.get("unit_id"),
                    "unit_label": row.get("unit_id"),
                    "bedrooms": extract_bedrooms(row),
                    "rooms": row.get("rooms"),
                    "area_m2": row.get("area_m2"),
                    "floor": row.get("floor"),
                    "first_seen": captured_at,
                    "last_seen": captured_at,
                },
            )

            for field in ("bedrooms", "rooms", "area_m2", "floor"):
                if unit.get(field) is None and row.get(field) is not None:
                    unit[field] = row.get(field)

            unit["last_seen"] = captured_at

        promo = discount_evidence(row)

        listings[listing_id] = {
            "listing_id": listing_id,
            "project_id": project_id,
            "unit_id": unit_id,
            "source": source,
            "source_listing_id": row.get("listing_id"),
            "source_url": url,
            "observed_urls": [url],
            "listing_title": title,
            "status": row.get("status") or "ACTIVE",
            "first_seen": captured_at,
            "last_seen": captured_at,
            **location,
        }

        observations.append({
            "observation_id": "obs:" + sha1(
                f"{run_id}|{listing_id}|{url}"
            ),
            "run_id": run_id,
            "captured_at": captured_at,
            "listing_id": listing_id,
            "project_id": project_id,
            "unit_id": unit_id,
            "source": source,
            "source_url": url,
            "title": title,
            "price": row.get("price"),
            "old_price": row.get("old_price"),
            "area_m2": row.get("area_m2"),
            "eur_m2": (
                round(row["price"] / row["area_m2"])
                if row.get("price") and row.get("area_m2")
                else None
            ),
            "bedrooms": extract_bedrooms(row),
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
            "features": row.get("features") or [],
            "discount": promo,
            **location,
            "raw_capture_ref": row.get("raw_artifact"),
        })

        objects.append({
            "object_id": "obj:" + sha1(listing_id),
            "project_id": project_id,
            "unit_id": unit_id,
            "listing_id": listing_id,
            "listing_title": title,
            "price": row.get("price"),
            "old_price": row.get("old_price"),
            "area_m2": row.get("area_m2"),
            "eur_m2": (
                round(row["price"] / row["area_m2"])
                if row.get("price") and row.get("area_m2")
                else None
            ),
            "bedrooms": extract_bedrooms(row),
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
            "source": source,
            "source_url": url,
            "first_seen": captured_at,
            "last_seen": captured_at,
            "discount_signal": promo["signal"],
            "discount_keywords": promo["keywords"],
            **location,
        })

    return (
        projects,
        units,
        listings,
        observations,
        objects,
        rejections,
    )


def source_coverage_status(report: dict[str, Any]) -> str:
    source = report.get("source")

    if report.get("status") == "SKIPPED":
        return "PASS" if source == "jbc_direct" else "MISSING"

    http_statuses = {
        int(status)
        for status in report.get("http_statuses", [])
        if isinstance(status, (int, float))
    }

    records = int(report.get("records_published", 0) or 0)
    seen = int(report.get("records_seen", 0) or 0)

    if 403 in http_statuses or 401 in http_statuses:
        return "BROKEN"

    if records > 0:
        return "PARTIAL" if report.get("records_rejected", 0) else "PASS"

    if seen > 0:
        return "PARTIAL"

    if report.get("coverage") == "MISSING":
        return "MISSING"

    return "BROKEN"


def build_source_audit(coverage: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []

    for report in coverage:
        item = dict(report)
        item["audit_status"] = source_coverage_status(report)
        item["mandatory"] = report.get("source") in MANDATORY_SOURCE_NAMES
        rows.append(item)

    mandatory = [row for row in rows if row.get("mandatory")]
    broken = [
        row for row in mandatory
        if row["audit_status"] in {"BROKEN", "MISSING"}
    ]
    partial = [
        row for row in mandatory
        if row["audit_status"] == "PARTIAL"
    ]

    if broken:
        overall = "DEGRADED"
    elif partial:
        overall = "PARTIAL"
    else:
        overall = "PASS"

    return {
        "overall_status": overall,
        "mandatory_sources": sorted(MANDATORY_SOURCE_NAMES),
        "mandatory_broken_or_missing": len(broken),
        "mandatory_partial": len(partial),
        "sources": rows,
    }


def merge_observations(
    existing: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {
        str(item.get("observation_id")): item
        for item in existing
        if item.get("observation_id")
    }

    for item in current:
        key = str(item.get("observation_id"))
        if key:
            by_id[key] = item

    return sorted(
        by_id.values(),
        key=lambda item: (
            str(item.get("captured_at") or ""),
            str(item.get("observation_id") or ""),
        ),
    )


def price_history_for_listing(
    listing_id: str,
    observations: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    obs = [
        item for item in observations
        if item.get("listing_id") == listing_id
        and isinstance(item.get("price"), (int, float))
    ]
    obs.sort(key=lambda item: str(item.get("captured_at") or ""))

    if not obs:
        return {
            "first_price": None,
            "lowest_price": None,
            "highest_price": None,
            "current_price": None,
            "first_seen": None,
            "last_seen": None,
            "number_of_price_changes": 0,
            "baselines": {},
        }

    first_price = obs[0]["price"]
    current_price = obs[-1]["price"]

    changes = 0
    previous = None
    for item in obs:
        price = item["price"]
        if previous is not None and price != previous:
            changes += 1
        previous = price

    result = {
        "first_price": first_price,
        "lowest_price": min(item["price"] for item in obs),
        "highest_price": max(item["price"] for item in obs),
        "current_price": current_price,
        "first_seen": obs[0].get("captured_at"),
        "last_seen": obs[-1].get("captured_at"),
        "number_of_price_changes": changes,
        "baselines": {},
    }

    for days in PRICE_WINDOWS:
        target = now - timedelta(days=days)
        eligible = []

        for item in obs:
            try:
                timestamp = datetime.fromisoformat(
                    str(item["captured_at"]).replace("Z", "+00:00")
                )
            except Exception:
                continue

            if timestamp <= target:
                eligible.append((timestamp, item))

        if not eligible:
            result["baselines"][f"{days}d"] = {
                "available": False,
                "target_date": iso(target),
                "actual_baseline_date": None,
                "age_days": None,
                "baseline_price": None,
                "current_price": current_price,
                "delta": None,
                "delta_pct": None,
            }
            continue

        timestamp, baseline = eligible[-1]
        baseline_price = baseline["price"]
        delta = current_price - baseline_price

        result["baselines"][f"{days}d"] = {
            "available": True,
            "target_date": iso(target),
            "actual_baseline_date": timestamp.isoformat().replace("+00:00", "Z"),
            "age_days": round(
                (now - timestamp).total_seconds() / 86400,
                2,
            ),
            "baseline_price": baseline_price,
            "current_price": current_price,
            "delta": delta,
            "delta_pct": (
                round(delta / baseline_price * 100, 2)
                if baseline_price
                else None
            ),
        }

    result["baselines"]["all_time"] = {
        "available": True,
        "target_date": None,
        "actual_baseline_date": obs[0].get("captured_at"),
        "age_days": None,
        "baseline_price": first_price,
        "current_price": current_price,
        "delta": current_price - first_price,
        "delta_pct": (
            round((current_price - first_price) / first_price * 100, 2)
            if first_price
            else None
        ),
    }

    return result


def build_price_history(
    observations: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    listing_ids = {
        item.get("listing_id")
        for item in observations
        if item.get("listing_id")
    }

    payload = {
        "schema_version": "3.0",
        "retention_days": RETENTION_DAYS,
        "generated_at": iso(now),
        "listings": {},
    }

    for listing_id in sorted(listing_ids):
        payload["listings"][listing_id] = price_history_for_listing(
            listing_id,
            observations,
            now,
        )

    return payload


def classify_budget(price: Any) -> str:
    if not isinstance(price, (int, float)):
        return "PRICE_UNKNOWN"
    return "WITHIN_BUDGET" if price <= BUDGET_TARGET else "OVER_BUDGET"


def build_current_objects(
    objects: list[dict[str, Any]],
    price_history: dict[str, Any],
) -> list[dict[str, Any]]:
    out = []

    for obj in objects:
        listing_id = obj.get("listing_id")
        history = price_history.get("listings", {}).get(
            listing_id,
            {},
        )

        item = dict(obj)
        item["budget_status"] = classify_budget(item.get("price"))
        item["price_history"] = history
        out.append(item)

    return sorted(
        out,
        key=lambda item: (
            str(item.get("municipality") or ""),
            str(item.get("micro_location") or ""),
            str(item.get("listing_title") or ""),
        ),
    )


def preserve_entity_history(
    old: list[dict[str, Any]],
    current: list[dict[str, Any]],
    key: str,
) -> list[dict[str, Any]]:
    by_key = {
        str(item.get(key)): item
        for item in old
        if item.get(key)
    }

    for item in current:
        identity = str(item.get(key))
        if not identity:
            continue

        previous = by_key.get(identity)
        if previous:
            merged = dict(previous)
            merged.update(item)

            if previous.get("first_seen"):
                merged["first_seen"] = previous["first_seen"]

            by_key[identity] = merged
        else:
            by_key[identity] = item

    return list(by_key.values())


def write_immutable_run(
    run_id: str,
    started_at: str,
    finished_at: str,
    source_audit: dict[str, Any],
    objects: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    units: list[dict[str, Any]],
    listings: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    rejections: list[dict[str, Any]],
    price_history: dict[str, Any],
) -> None:
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        run_dir / "run.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "snapshot_status": source_audit["overall_status"],
            "records": {
                "objects": len(objects),
                "projects": len(projects),
                "units": len(units),
                "listings": len(listings),
                "observations": len(observations),
                "rejections": len(rejections),
            },
            "source_audit": source_audit,
        },
    )

    write_json(run_dir / "objects.json", objects)
    write_json(run_dir / "projects.json", projects)
    write_json(run_dir / "units.json", units)
    write_json(run_dir / "listings.json", listings)
    write_json(run_dir / "observations.json", observations)
    write_json(run_dir / "rejections.json", rejections)
    write_json(run_dir / "price_history.json", price_history)


def write_daily_summary(
    run_id: str,
    run_date: str,
    source_audit: dict[str, Any],
    objects: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    units: list[dict[str, Any]],
    listings: list[dict[str, Any]],
) -> None:
    HISTORY.mkdir(parents=True, exist_ok=True)

    write_json(
        HISTORY / f"{run_date}.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run_date": run_date,
            "latest_run_id": run_id,
            "snapshot_status": source_audit["overall_status"],
            "source_audit": source_audit,
            "inventory": {
                "objects": len(objects),
                "projects": len(projects),
                "units": len(units),
                "listings": len(listings),
                "priced_objects": sum(
                    1 for item in objects
                    if item.get("price") is not None
                ),
                "within_budget": sum(
                    1 for item in objects
                    if item.get("budget_status") == "WITHIN_BUDGET"
                ),
            },
            "run_references": [run_id],
        },
    )


def build_current_payload(
    run_id: str,
    started_at: str,
    finished_at: str,
    source_audit: dict[str, Any],
    objects: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    units: list[dict[str, Any]],
    listings: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "captured_at": finished_at,
        "last_live_capture": finished_at,
        "snapshot_status": source_audit["overall_status"],
        "data_quality": {
            "green_only_when_all_mandatory_sources_pass": True,
            "mandatory_source_failures": source_audit[
                "mandatory_broken_or_missing"
            ],
            "mandatory_source_partial": source_audit[
                "mandatory_partial"
            ],
        },
        "inventory": {
            "objects": len(objects),
            "projects": len(projects),
            "units": len(units),
            "listings": len(listings),
            "observations": len(observations),
            "priced_objects": sum(
                1 for item in objects
                if item.get("price") is not None
            ),
            "budget_target": BUDGET_TARGET,
        },
        "locations": sorted(
            {
                item.get("municipality")
                for item in objects
                if item.get("municipality")
                and item.get("municipality") != "unknown"
            }
        ),
        "source_audit": source_audit,
        "run_path": f"data/runs/{run_id}/run.json",
        "objects_file": "data/objects.json",
        "projects_file": "data/projects.json",
        "units_file": "data/units.json",
        "listings_file": "data/listings.json",
        "observations_file": "data/observations.json",
        "rejections_file": "data/rejections.json",
        "price_history_file": "data/price_history.json",
        "price_history_retention_days": RETENTION_DAYS,
    }


def write_debug(
    run_id: str,
    source_audit: dict[str, Any],
    objects: list[dict[str, Any]],
    rejections: list[dict[str, Any]],
) -> None:
    debug_run = DEBUG / "snapshot" / run_id
    debug_run.mkdir(parents=True, exist_ok=True)

    write_json(debug_run / "source_audit.json", source_audit)

    write_json(
        debug_run / "inventory_stats.json",
        {
            "objects": len(objects),
            "priced": sum(
                1 for item in objects
                if item.get("price") is not None
            ),
            "unknown_municipality": sum(
                1 for item in objects
                if item.get("municipality") == "unknown"
            ),
            "rejections": len(rejections),
        },
    )


def main() -> None:
    started_dt = utc_now()
    started_at = iso(started_dt)
    run_id = run_id_for(started_dt)

    rows, coverage = run()

    finished_dt = utc_now()
    finished_at = iso(finished_dt)

    source_audit = build_source_audit(coverage)

    (
        current_projects,
        current_units,
        current_listings,
        current_observations,
        current_objects,
        current_rejections,
    ) = make_entities(
        rows,
        finished_at,
        run_id,
    )

    old_projects = load(PROJECTS, [])
    old_units = load(UNITS, [])
    old_listings = load(LISTINGS, [])
    old_observations = load(OBSERVATIONS, [])

    if not isinstance(old_projects, list):
        old_projects = []
    if not isinstance(old_units, list):
        old_units = []
    if not isinstance(old_listings, list):
        old_listings = []
    if not isinstance(old_observations, list):
        old_observations = []

    projects = preserve_entity_history(
        old_projects,
        list(current_projects.values()),
        "project_id",
    )

    units = preserve_entity_history(
        old_units,
        list(current_units.values()),
        "unit_id",
    )

    listings = preserve_entity_history(
        old_listings,
        list(current_listings.values()),
        "listing_id",
    )

    observations = merge_observations(
        old_observations,
        current_observations,
    )

    price_history = build_price_history(
        observations,
        finished_dt,
    )

    objects = build_current_objects(
        current_objects,
        price_history,
    )

    write_immutable_run(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        source_audit=source_audit,
        objects=objects,
        projects=list(current_projects.values()),
        units=list(current_units.values()),
        listings=list(current_listings.values()),
        observations=current_observations,
        rejections=current_rejections,
        price_history=price_history,
    )

    write_json(OBJECTS, objects)
    write_json(PROJECTS, projects)
    write_json(UNITS, units)
    write_json(LISTINGS, listings)
    write_json(OBSERVATIONS, observations)
    write_json(REJECTIONS, current_rejections)
    write_json(PRICE_HISTORY, price_history)

    current_payload = build_current_payload(
        run_id,
        started_at,
        finished_at,
        source_audit,
        objects,
        projects,
        units,
        listings,
        observations,
    )

    write_json(CURRENT, current_payload)

    write_daily_summary(
        run_id,
        finished_at[:10],
        source_audit,
        objects,
        projects,
        units,
        listings,
    )

    write_debug(
        run_id,
        source_audit,
        objects,
        current_rejections,
    )

    write_json(
        DEBUG / "snapshot_debug.json",
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "snapshot_status": source_audit["overall_status"],
            "records": {
                "collector_rows": len(rows),
                "objects": len(objects),
                "projects": len(projects),
                "units": len(units),
                "listings": len(listings),
                "observations_total": len(observations),
                "rejections": len(current_rejections),
            },
            "source_audit": source_audit,
        },
    )

    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "snapshot_status": source_audit["overall_status"],
                "collector_rows": len(rows),
                "objects": len(objects),
                "projects": len(projects),
                "units": len(units),
                "listings": len(listings),
                "observations": len(observations),
                "rejections": len(current_rejections),
                "mandatory_source_failures": source_audit[
                    "mandatory_broken_or_missing"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
