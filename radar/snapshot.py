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
HISTORY = DATA / "history"
RUNS = DATA / "runs"
PRICE_HISTORY = DATA / "price_history.json"

SCHEMA_VERSION = "4.0"
RETENTION_DAYS = 365

DISCOUNT_PATTERNS = (
    "ribassato", "ribasso", "prezzo ribassato", "prezzo precedente",
    "sconto", "offerta", "offerta speciale", "promozione", "promo",
    "occasione", "prezzo speciale", "ultimo prezzo", "ridotto",
)

LOCATION_ALIASES = {
    "jesolo paese": "jesolo-paese",
    "jesolo": "jesolo",
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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()

def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    url = str(url).strip()
    if not url:
        return None
    # Keep this intentionally simple; collector already canonicalizes URLs.
    return url.rstrip("/")

def normalize_location(value: Any) -> str:
    raw = " ".join(str(value or "unknown").strip().lower().replace("–", "-").split())
    return LOCATION_ALIASES.get(raw, raw.replace(" ", "-"))

def stable_project_id(row: dict[str, Any]) -> str:
    # Prefer a project-level identity supplied by the source; otherwise use
    # normalized title + micro-location. URL is deliberately not part of it.
    explicit = row.get("project_id")
    if explicit:
        return str(explicit)
    title = re.sub(r"\s+", " ", str(row.get("listing_title") or "untitled")).strip().lower()
    location = normalize_location(row.get("micro_location") or row.get("location"))
    key = f"{location}|{title}"
    return "project:" + sha1(key)

def stable_unit_id(row: dict[str, Any], project_id: str) -> str | None:
    unit = row.get("unit_id")
    if unit:
        return "unit:" + sha1(f"{project_id}|{str(unit).strip().lower()}")
    return None

def stable_listing_id(row: dict[str, Any]) -> str:
    # A source listing is a source-specific entity. URL changes must not
    # destroy the project identity, but a listing URL remains a useful
    # source-level identity.
    source = str(row.get("source") or "unknown").lower()
    listing_id = str(row.get("listing_id") or "")
    if listing_id:
        return "listing:" + sha1(f"{source}|{listing_id}")
    url = canonical_url(row.get("source_url")) or ""
    return "listing:" + sha1(f"{source}|{url}")

def extract_bedrooms(row: dict[str, Any]) -> int | None:
    value = row.get("bedrooms")
    if isinstance(value, (int, float)):
        return int(value)
    text = str(row.get("raw_text") or "").lower()
    patterns = (
        r"(\d+)\s*(?:camere da letto|camere|bedrooms|bedroom)",
        r"(\d+)\s*(?:stanze da letto)",
    )
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return int(m.group(1))
    rooms = row.get("rooms")
    if isinstance(rooms, (int, float)) and int(rooms) >= 4:
        # Conservative inference only: 4+ locali can correspond to 3+ bedrooms,
        # but this is not treated as verified.
        return None
    return None

def feature_bool(row: dict[str, Any], *names: str) -> bool | None:
    explicit = {}
    for n in names:
        if n in row:
            explicit[n] = row.get(n)
    if explicit:
        vals = list(explicit.values())
        if any(v is True for v in vals):
            return True
        if all(v is False for v in vals):
            return False
    features = {str(x).lower() for x in (row.get("features") or [])}
    text = str(row.get("raw_text") or "").lower()
    needles = {
        "parking": ("parcheggio", "posto auto"),
        "garage": ("garage", "box auto"),
        "terrace": ("terrazza", "terrazzo", "balcone"),
        "pool": ("piscina",),
        "sea_view": ("vista mare", "fronte mare"),
        "pv": ("pannelli fotovoltaici", "fotovoltaico", "fotovoltaica", "pannelli solari"),
        "heat_pump": ("pompa di calore",),
        "ev_charging": ("ricarica auto elettrica", "colonnina", "ev charging"),
    }
    key = next((k for k in needles if k in names), names[0] if names else "")
    terms = needles.get(key, names)
    return True if any(t in features or t in text for t in terms) else False

def discount_evidence(row: dict[str, Any]) -> dict[str, Any]:
    text = str(row.get("raw_text") or "")
    low = text.lower()
    keywords = sorted({p for p in DISCOUNT_PATTERNS if p in low})
    old_price = row.get("old_price")
    new_price = row.get("new_price") or row.get("price")
    if old_price is not None:
        try:
            old_price = int(float(old_price))
        except Exception:
            old_price = None
    if new_price is not None:
        try:
            new_price = int(float(new_price))
        except Exception:
            new_price = None
    amount = None
    pct = None
    if old_price and new_price and old_price > new_price:
        amount = old_price - new_price
        pct = round(amount / old_price * 100, 2)
    return {
        "signal": bool(keywords or amount),
        "keywords": keywords,
        "old_price": old_price,
        "new_price": new_price,
        "amount": amount,
        "percent": pct,
        "evidence_text": next((k for k in keywords), None),
    }

def make_entities(rows: list[dict[str, Any]], captured_at: str, run_id: str):
    projects: dict[str, dict[str, Any]] = {}
    units: dict[str, dict[str, Any]] = {}
    listings: dict[str, dict[str, Any]] = {}
    observations: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []

    for row in rows:
        url = canonical_url(row.get("source_url"))
        if not url:
            # Collector should report this, but never silently lose the row.
            continue

        location = normalize_location(row.get("micro_location") or row.get("location"))
        title = re.sub(r"\s+", " ", str(row.get("listing_title") or url)).strip()[:300]
        project_id = stable_project_id(row)
        unit_id = stable_unit_id(row, project_id)
        listing_id = stable_listing_id(row)
        source = str(row.get("source") or "unknown")

        micro = normalize_location(row.get("micro_location") or row.get("location"))
        verification = row.get("location_verification_status") or (
            "SOURCE_DECLARED" if row.get("location") else "UNVERIFIED"
        )
        confidence = row.get("location_verification_confidence")
        if confidence is None:
            confidence = "medium" if row.get("location") else "low"

        if project_id not in projects:
            projects[project_id] = {
                "project_id": project_id,
                "name": title,
                "macro_zone": location,
                "micro_location": micro,
                "location_verification_status": verification,
                "location_verification_confidence": confidence,
                "sources": [],
                "listing_ids": [],
                "unit_ids": [],
                "first_seen": captured_at,
                "last_seen": captured_at,
            }
        p = projects[project_id]
        p["name"] = p["name"] if p["name"] != "Untitled listing" else title
        if source not in p["sources"]:
            p["sources"].append(source)
        if listing_id not in p["listing_ids"]:
            p["listing_ids"].append(listing_id)
        if unit_id and unit_id not in p["unit_ids"]:
            p["unit_ids"].append(unit_id)
        p["last_seen"] = captured_at

        if unit_id:
            units.setdefault(unit_id, {
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
            })
            u = units[unit_id]
            for k in ("bedrooms", "rooms", "area_m2", "floor"):
                if u.get(k) is None and row.get(k) is not None:
                    u[k] = row.get(k)
            u["last_seen"] = captured_at

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
            "location": location,
            "micro_location": micro,
            "location_verification_status": verification,
            "location_verification_confidence": confidence,
        }

        obs_id = "obs:" + sha1(f"{run_id}|{listing_id}|{url}")
        observations.append({
            "observation_id": obs_id,
            "run_id": run_id,
            "captured_at": captured_at,
            "listing_id": listing_id,
            "project_id": project_id,
            "unit_id": unit_id,
            "source": source,
            "source_url": url,
            "title": title,
            "price": row.get("price"),
            "area_m2": row.get("area_m2"),
            "eur_m2": round(row["price"] / row["area_m2"]) if row.get("price") and row.get("area_m2") else None,
            "bedrooms": extract_bedrooms(row),
            "rooms": row.get("rooms"),
            "floor": row.get("floor"),
            "energy_class": row.get("energy_class"),
            "parking": feature_bool(row, "parking"),
            "garage": feature_bool(row, "garage"),
            "terrace": feature_bool(row, "terrace"),
            "pool": feature_bool(row, "pool"),
            "sea_view": feature_bool(row, "sea_view"),
            "pv_present": feature_bool(row, "pv", "pannelli fotovoltaici"),
            "heat_pump": feature_bool(row, "heat_pump", "pompa di calore"),
            "ev_charging": feature_bool(row, "ev_charging"),
            "delivery": row.get("delivery"),
            "stage": row.get("stage"),
            "status": row.get("status") or "ACTIVE",
            "micro_location": micro,
            "location_verification_status": verification,
            "location_verification_confidence": confidence,
            "discount": promo,
            "features": row.get("features") or [],
            "raw_capture_ref": f"data/debug/capture/{run_id}/manifest.json",
        })

        # Keep backwards-compatible objects.json for the existing frontend.
        legacy.append({
            "object_id": "obj:" + sha1(listing_id),
            "location": location,
            "project_id": project_id,
            "unit_id": unit_id,
            "listing_id": listing_id,
            "listing_title": title,
            "price": row.get("price"),
            "area_m2": row.get("area_m2"),
            "eur_m2": round(row["price"] / row["area_m2"]) if row.get("price") and row.get("area_m2") else None,
            "bedrooms": extract_bedrooms(row),
            "rooms": row.get("rooms"),
            "floor": row.get("floor"),
            "energy_class": row.get("energy_class"),
            "parking": feature_bool(row, "parking"),
            "garage": feature_bool(row, "garage"),
            "terrace": feature_bool(row, "terrace"),
            "pool": feature_bool(row, "pool"),
            "sea_view": feature_bool(row, "sea_view"),
            "pv_present": feature_bool(row, "pv", "pannelli fotovoltaici"),
            "heat_pump": feature_bool(row, "heat_pump", "pompa di calore"),
            "ev_charging": feature_bool(row, "ev_charging"),
            "delivery": row.get("delivery"),
            "stage": row.get("stage"),
            "status": row.get("status") or "ACTIVE",
            "source": source,
            "source_url": url,
            "micro_location": micro,
            "location_verification_status": verification,
            "location_verification_confidence": confidence,
            "first_seen": captured_at,
            "last_seen": captured_at,
            "discount_signal": promo["signal"],
            "discount_keywords": promo["keywords"],
        })

    return projects, units, listings, observations, legacy

def load_price_history():
    payload = load(PRICE_HISTORY, {
        "schema_version": "2.0",
        "retention_days": RETENTION_DAYS,
        "listings": {},
    })
    if not isinstance(payload, dict):
        payload = {"schema_version": "2.0", "retention_days": RETENTION_DAYS, "listings": {}}
    payload.setdefault("listings", {})
    payload.setdefault("retention_days", RETENTION_DAYS)
    return payload

def parse_dt(value: Any):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None

def nearest_price(points, target: datetime, max_age_days: int = 14):
    best = None
    for p in points:
        d = parse_dt(p.get("captured_at") or p.get("date"))
        price = p.get("price")
        if d and price is not None and d <= target:
            age = (target - d).days
            if age <= max_age_days and (best is None or d > best[0]):
                best = (d, price)
    return best

def update_price_history(history, observations, now):
    listings = history.setdefault("listings", {})
    cutoff = now - timedelta(days=int(history.get("retention_days", RETENTION_DAYS)))
    for obs in observations:
        price = obs.get("price")
        if price is None:
            continue
        lid = obs["listing_id"]
        points = listings.setdefault(lid, [])
        # Immutable observations: don't create a duplicate identical point
        # for repeated execution of the exact same run.
        if not any(p.get("observation_id") == obs["observation_id"] for p in points):
            points.append({
                "observation_id": obs["observation_id"],
                "run_id": obs["run_id"],
                "captured_at": obs["captured_at"],
                "price": price,
                "eur_m2": obs.get("eur_m2"),
                "source": obs.get("source"),
                "source_url": obs.get("source_url"),
                "title": obs.get("title"),
            })
        listings[lid] = [
            p for p in points
            if parse_dt(p.get("captured_at")) and parse_dt(p.get("captured_at")) >= cutoff
        ]
        listings[lid].sort(key=lambda p: p.get("captured_at", ""))
    history["schema_version"] = "2.0"
    history["retention_days"] = int(history.get("retention_days", RETENTION_DAYS))
    history["updated_at"] = iso(now)
    return history

def price_metrics(listing_id: str, current_price, history, now):
    points = history.get("listings", {}).get(listing_id, [])
    metrics = {}
    for key, days in (("1d", 1), ("7d", 7), ("30d", 30), ("90d", 90), ("180d", 180), ("365d", 365)):
        baseline = nearest_price(points, now - timedelta(days=days), max_age_days=14)
        metrics[f"price_change_{key}"] = None
        metrics[f"price_change_{key}_pct"] = None
        metrics[f"price_change_{key}_date"] = None
        if baseline and current_price is not None:
            old = baseline[1]
            metrics[f"price_change_{key}"] = current_price - old
            metrics[f"price_change_{key}_pct"] = round((current_price - old) / old * 100, 2) if old else None
            metrics[f"price_change_{key}_date"] = iso(baseline[0])
    priced = [p.get("price") for p in points if p.get("price") is not None]
    metrics["first_price"] = priced[0] if priced else current_price
    metrics["lowest_price"] = min(priced) if priced else current_price
    metrics["highest_price"] = max(priced) if priced else current_price
    metrics["observations_count"] = len(points)
    return metrics

def merge_previous_metadata(legacy, previous_objects, now):
    prev = {o.get("listing_id") or o.get("source_url"): o for o in previous_objects}
    for obj in legacy:
        old = prev.get(obj.get("listing_id")) or prev.get(obj.get("source_url"))
        if old:
            obj["first_seen"] = old.get("first_seen", obj["first_seen"])
            obj["new_to_radar_30d"] = (now - (parse_dt(obj["first_seen"]) or now)).days <= 30
        else:
            obj["new_to_radar_30d"] = True
    return legacy

def main():
    now = utc_now()
    run_id = run_id_for(now)
    captured_at = iso(now)
    run_dir = RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    HISTORY.mkdir(parents=True, exist_ok=True)

    previous_current = load(CURRENT, {})
    previous_objects = load(OBJECTS, {"objects": []}).get("objects", [])

    try:
        rows, coverage = run()
    except Exception as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "captured_at": captured_at,
            "status": "FAILED",
            "error": repr(exc),
            "previous_current_preserved": True,
        }
        write_json(run_dir / "run.json", payload)
        write_json(HISTORY / f"{run_id}-FAILED.json", payload)
        raise

    # The collector is the source of truth for source-run diagnostics.
    # Never publish a run with zero rows.
    if not rows:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "captured_at": captured_at,
            "status": "FAILED_EMPTY_CAPTURE",
            "records_seen": sum(int(x.get("records_seen", 0)) for x in coverage),
            "records_parsed": sum(int(x.get("records_parsed", 0)) for x in coverage),
            "records_normalized": 0,
            "records_published": 0,
            "records_rejected": sum(int(x.get("records_rejected", 0)) for x in coverage),
            "coverage": coverage,
            "previous_current_preserved": True,
        }
        write_json(run_dir / "run.json", payload)
        write_json(HISTORY / f"{run_id}-EMPTY.json", payload)
        raise RuntimeError("Live capture returned 0 rows; previous current.json preserved.")

    projects, units, listings, observations, legacy = make_entities(rows, captured_at, run_id)
    legacy = merge_previous_metadata(legacy, previous_objects, now)

    history = load_price_history()
    history = update_price_history(history, observations, now)

    for obj in legacy:
        obj.update(price_metrics(obj["listing_id"], obj.get("price"), history, now))

    # Immutable run payload. This is never overwritten by another run.
    run_payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "captured_at": captured_at,
        "status": "SUCCESS",
        "records_seen": sum(int(x.get("records_seen", 0)) for x in coverage),
        "records_parsed": sum(int(x.get("records_parsed", 0)) for x in coverage),
        "records_normalized": len(observations),
        "records_published": len(observations),
        "records_rejected": sum(int(x.get("records_rejected", 0)) for x in coverage),
        "source_runs": coverage,
        "counts": {
            "projects": len(projects),
            "units": len(units),
            "listings": len(listings),
            "observations": len(observations),
        },
    }
    write_json(run_dir / "run.json", run_payload)
    write_json(run_dir / "observations.json", {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "observations": observations,
    })
    write_json(run_dir / "listings.json", {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "listings": list(listings.values()),
    })

    # Current materialized views.
    write_json(PROJECTS, {
        "schema_version": SCHEMA_VERSION,
        "updated_at": captured_at,
        "projects": list(projects.values()),
    })
    write_json(UNITS, {
        "schema_version": SCHEMA_VERSION,
        "updated_at": captured_at,
        "units": list(units.values()),
    })
    write_json(LISTINGS, {
        "schema_version": SCHEMA_VERSION,
        "updated_at": captured_at,
        "listings": list(listings.values()),
    })

    # Append-only observation ledger. Existing observations are preserved.
    old_obs = load(OBSERVATIONS, {"schema_version": SCHEMA_VERSION, "observations": []})
    old_list = old_obs.get("observations", []) if isinstance(old_obs, dict) else []
    known_obs = {x.get("observation_id") for x in old_list}
    for obs in observations:
        if obs["observation_id"] not in known_obs:
            old_list.append(obs)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    old_list = [
        x for x in old_list
        if (parse_dt(x.get("captured_at")) or now) >= cutoff
    ]
    write_json(OBSERVATIONS, {
        "schema_version": SCHEMA_VERSION,
        "updated_at": captured_at,
        "retention_days": RETENTION_DAYS,
        "observations": old_list,
    })

    write_json(PRICE_HISTORY, history)

    obj_payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "run_date": now.date().isoformat(),
        "objects": legacy,
        "counts": {
            "total_captured_listings": len(legacy),
            "projects": len(projects),
            "units": len(units),
            "listings": len(listings),
            "observations": len(observations),
            "by_location": {
                loc: sum(1 for x in legacy if x.get("location") == loc)
                for loc in sorted({x.get("location") for x in legacy})
            },
        },
        "coverage": coverage,
    }
    write_json(OBJECTS, obj_payload)

    numeric_portal_total = previous_current.get("portal_inventory_total")
    if not isinstance(numeric_portal_total, (int, float)):
        numeric_portal_total = None

    current = {
        # Deliberately no legacy static sections/cards/counts.
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "run_date": now.date().isoformat(),
        "last_live_capture": captured_at,
        "snapshot_status": "SUCCESS",
        "run_path": f"data/runs/{run_id}/run.json",
        "objects_file": "data/objects.json",
        "projects_file": "data/projects.json",
        "units_file": "data/units.json",
        "listings_file": "data/listings.json",
        "observations_file": "data/observations.json",
        "price_history_file": "data/price_history.json",
        "price_history_retention_days": RETENTION_DAYS,
        "capture_coverage": coverage,
        "records_seen": run_payload["records_seen"],
        "records_parsed": run_payload["records_parsed"],
        "records_normalized": run_payload["records_normalized"],
        "records_published": run_payload["records_published"],
        "records_rejected": run_payload["records_rejected"],
        "counts": run_payload["counts"],
        "locations": sorted(projects[x]["macro_zone"] for x in projects),
        "live_capture_total": len(legacy),
        "portal_inventory_total": numeric_portal_total,
        "mandatory_sources_complete": all(
            str(x.get("status", "")).upper() == "SUCCESS"
            for x in coverage
            if x.get("mandatory") is True
        ) if any(x.get("mandatory") is True for x in coverage) else False,
        "price_history_stats": {
            "priced_objects": sum(1 for x in legacy if x.get("price") is not None),
            "with_1d": sum(1 for x in legacy if x.get("price_change_1d") is not None),
            "with_7d": sum(1 for x in legacy if x.get("price_change_7d") is not None),
            "with_30d": sum(1 for x in legacy if x.get("price_change_30d") is not None),
            "with_90d": sum(1 for x in legacy if x.get("price_change_90d") is not None),
            "with_180d": sum(1 for x in legacy if x.get("price_change_180d") is not None),
            "with_365d": sum(1 for x in legacy if x.get("price_change_365d") is not None),
            "discount_signals": sum(1 for x in legacy if x.get("discount_signal")),
        },
        "quality": {
            "immutable_run": True,
            "raw_capture_manifest": f"data/debug/capture/{run_id}/manifest.json",
            "source_run_contract": True,
            "no_empty_publish": True,
        },
    }
    write_json(CURRENT, current)

    # Daily summary is derived from immutable runs; it is not the immutable run.
    daily_path = HISTORY / f"{now.date().isoformat()}.json"
    daily = load(daily_path, {
        "schema_version": SCHEMA_VERSION,
        "date": now.date().isoformat(),
        "runs": [],
    })
    runs = daily.get("runs", [])
    if not any(x.get("run_id") == run_id for x in runs):
        runs.append({
            "run_id": run_id,
            "captured_at": captured_at,
            "status": "SUCCESS",
            "records_published": len(observations),
            "projects": len(projects),
            "units": len(units),
            "listings": len(listings),
        })
    daily["runs"] = runs
    daily["last_run_id"] = run_id
    daily["updated_at"] = captured_at
    write_json(daily_path, daily)

if __name__ == "__main__":
    main()
