from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CURRENT = DATA / "current.json"
OBJECTS = DATA / "objects.json"
HISTORY = DATA / "history"

# Import the live collectors as a package from the repository root.
from radar.collectors.portal_capture import run


def load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def stable_project_id(location: str, title: str) -> str:
    key = f"{location}|{title.lower().strip()}"
    return "project:" + hashlib.sha1(key.encode()).hexdigest()


def normalize(rows, run_date: str):
    out = []
    for r in rows:
        title = r.get("listing_title") or "Untitled listing"
        location = r.get("location") or "Unknown"
        url = r.get("source_url")
        if not url:
            continue

        out.append({
            "object_id": "obj:" + hashlib.sha1(r["listing_id"].encode()).hexdigest(),
            "location": location,
            "project_id": stable_project_id(location, title),
            "unit_id": None,
            "listing_id": r["listing_id"],
            "project": None,
            "unit": None,
            "listing_title": title,
            "price": r.get("price"),
            "area_m2": r.get("area_m2"),
            "eur_m2": (
                round(r["price"] / r["area_m2"])
                if r.get("price") and r.get("area_m2") else None
            ),
            "bedrooms": None, "rooms": None, "floor": None,
            "parking": None, "garage": None, "terrace": None, "pool": None,
            "sea_distance_m": None, "delivery": None, "stage": None,
            "status": "active_snapshot",
            "source": r.get("source"),
            "source_url": url,
            "first_seen": run_date,
            "last_seen": run_date,
            "previous_price": None,
            "price_delta": None,
            "new_to_radar_30d": True,
        })

    # Same canonical URL = same listing.
    dedup = {}
    for obj in out:
        dedup[obj["source_url"]] = obj
    return list(dedup.values())


def main():
    now = datetime.now(timezone.utc)
    run_date = now.date().isoformat()

    previous_current = load(CURRENT, {})
    previous_objects_payload = load(OBJECTS, {"objects": []})
    previous_objects = previous_objects_payload.get("objects", [])
    prev_by_url = {
        o.get("source_url"): o
        for o in previous_objects
        if o.get("source_url")
    }

    # Run the live capture.
    try:
        rows, coverage = run()
    except Exception as exc:
        # CRITICAL FAIL-SAFE:
        # never replace a good snapshot with an empty/failed one.
        error = {
            "run_date": run_date,
            "status": "FAILED",
            "error": repr(exc),
            "previous_snapshot_preserved": True,
        }
        HISTORY.mkdir(parents=True, exist_ok=True)
        (HISTORY / f"{run_date}-FAILED.json").write_text(
            json.dumps(error, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise

    objects = normalize(rows, run_date)

    # CRITICAL FAIL-SAFE:
    # zero captured records is not a valid market snapshot.
    if not objects:
        diagnostic = {
            "run_date": run_date,
            "status": "FAILED_EMPTY_CAPTURE",
            "captured_rows": 0,
            "coverage": coverage,
            "previous_snapshot_preserved": True,
            "reason": "Live collectors returned zero normalized listings.",
        }
        HISTORY.mkdir(parents=True, exist_ok=True)
        (HISTORY / f"{run_date}-EMPTY.json").write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        raise RuntimeError(
            "Live capture returned 0 normalized listings. "
            "Previous current.json was preserved; no empty snapshot was published."
        )

    # Compare to previous snapshot.
    for obj in objects:
        prev = prev_by_url.get(obj["source_url"])
        if prev:
            obj["first_seen"] = prev.get("first_seen", run_date)
            obj["previous_price"] = prev.get("price")
            if prev.get("price") is not None and obj.get("price") is not None:
                obj["price_delta"] = obj["price"] - prev["price"]

            try:
                first = datetime.fromisoformat(obj["first_seen"]).date()
                obj["new_to_radar_30d"] = (
                    (now.date() - first).days <= 30
                )
            except Exception:
                pass

    DATA.mkdir(exist_ok=True)
    HISTORY.mkdir(exist_ok=True)

    # Preserve the portal inventory baseline from the previous/current source.
    # The crawler's captured listing count is a DIFFERENT metric.
    old_inventory = previous_current.get("inventory_counts", {})
    all_locations = set(old_inventory.keys())
    all_locations.update(o["location"] for o in objects)

    inventory_counts = {}
    for loc in sorted(all_locations):
        old = old_inventory.get(loc, {})
        live_count = sum(1 for o in objects if o["location"] == loc)
        inventory_counts[loc] = {
            "portal_inventory": old.get("portal_inventory"),
            "live_capture": live_count,
            "captured_concrete_rows": live_count,
        }

    # Update only live-capture metadata while preserving the report's static
    # sections and portal inventory baselines.
    current = dict(previous_current)
    current["run_date"] = run_date
    current["last_live_capture"] = now.isoformat()
    current["snapshot_status"] = "SUCCESS"
    current["objects_file"] = "data/objects.json"
    current["capture_coverage"] = coverage
    current["inventory_counts"] = inventory_counts
    current["locations"] = sorted(all_locations)

    # Keep a useful aggregate.
    portal_values = [
        v["portal_inventory"]
        for v in inventory_counts.values()
        if isinstance(v.get("portal_inventory"), (int, float))
    ]
    current["portal_inventory_total"] = sum(portal_values)
    current["live_capture_total"] = len(objects)
    current["captured_concrete_total"] = len(objects)

    obj_payload = {
        "schema_version": "2.2",
        "run_date": run_date,
        "objects": objects,
        "counts": {
            "total_captured_listings": len(objects),
            "by_location": {
                loc: sum(1 for o in objects if o["location"] == loc)
                for loc in sorted({o["location"] for o in objects})
            },
        },
        "coverage": coverage,
    }

    OBJECTS.write_text(
        json.dumps(obj_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    CURRENT.write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Daily history only after successful capture.
    (HISTORY / f"{run_date}.json").write_text(
        json.dumps({
            "run_date": run_date,
            "status": "SUCCESS",
            "capture_coverage": coverage,
            "counts": obj_payload["counts"],
            "portal_inventory_total": current["portal_inventory_total"],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
