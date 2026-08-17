from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CURRENT = DATA / "current.json"
OBJECTS = DATA / "objects.json"
HISTORY = DATA / "history"
PRICE_HISTORY = DATA / "price_history.json"

from radar.collectors.portal_capture import run

DISCOUNT_PATTERNS = (
    "ribassato", "ribasso", "prezzo ribassato", "sconto",
    "offerta", "offerta speciale", "promozione", "promo",
    "occasione", "prezzo speciale", "ultimo prezzo", "ridotto",
)

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
        raw_text = str(r.get("raw_text") or "")
        low = raw_text.lower()
        discount_keywords = [p for p in DISCOUNT_PATTERNS if p in low]
        out.append({
            "object_id": "obj:" + hashlib.sha1(r["listing_id"].encode()).hexdigest(),
            "location": location,
            "project_id": stable_project_id(location, title),
            "unit_id": r.get("unit_id"),
            "listing_id": r["listing_id"],
            "project": None, "unit": None,
            "listing_title": title,
            "price": r.get("price"),
            "area_m2": r.get("area_m2"),
            "eur_m2": round(r["price"] / r["area_m2"]) if r.get("price") and r.get("area_m2") else None,
            "bedrooms": r.get("bedrooms"), "rooms": r.get("rooms"), "floor": r.get("floor"),
            "parking": r.get("parking"), "garage": r.get("garage"), "terrace": r.get("terrace"),
            "pool": r.get("pool"), "sea_distance_m": r.get("sea_distance_m"),
            "delivery": r.get("delivery"), "stage": r.get("stage"),
            "status": "active_snapshot", "source": r.get("source"), "source_url": url,
            "first_seen": run_date, "last_seen": run_date,
            "previous_price": None, "price_delta": None, "price_delta_pct": None,
            "price_change_30d": None, "price_change_30d_pct": None, "price_change_30d_date": None,
            "price_change_180d": None, "price_change_180d_pct": None, "price_change_180d_date": None,
            "discount_signal": bool(discount_keywords), "discount_keywords": discount_keywords,
            "new_to_radar_30d": True,
        })
    dedup = {}
    for obj in out:
        dedup[obj["source_url"]] = obj
    return list(dedup.values())

def parse_date(value):
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return None

def pct_change(current, old):
    if current is None or old in (None, 0):
        return None
    return round((current - old) / old * 100, 2)

def load_price_history():
    payload = load(PRICE_HISTORY, {"schema_version": "1.0", "retention_days": 365, "listings": {}})
    if not isinstance(payload, dict):
        payload = {"schema_version": "1.0", "retention_days": 365, "listings": {}}
    payload.setdefault("listings", {})
    payload.setdefault("retention_days", 365)
    return payload

def strip_today(history, run_date):
    for url in list(history.get("listings", {})):
        history["listings"][url] = [p for p in history["listings"][url] if p.get("date") != run_date]
        if not history["listings"][url]:
            del history["listings"][url]

def nearest_price(points, target_date):
    best = None
    for p in points:
        d = parse_date(p.get("date"))
        price = p.get("price")
        if d and d <= target_date and price is not None and (best is None or d > best[0]):
            best = (d, price)
    return best

def apply_history_deltas(objects, history, now):
    listings = history.get("listings", {})
    target_30 = now.date() - timedelta(days=30)
    target_180 = now.date() - timedelta(days=180)
    for obj in objects:
        points = listings.get(obj.get("source_url"), [])
        if not points:
            continue
        if len(points) >= 1:
            prev = points[-1].get("price")
            if prev is not None and obj.get("price") is not None:
                obj["previous_price"] = prev
                obj["price_delta"] = obj["price"] - prev
                obj["price_delta_pct"] = pct_change(obj["price"], prev)
        p30 = nearest_price(points, target_30)
        if p30 and obj.get("price") is not None:
            obj["price_change_30d"] = obj["price"] - p30[1]
            obj["price_change_30d_pct"] = pct_change(obj["price"], p30[1])
            obj["price_change_30d_date"] = p30[0].isoformat()
        p180 = nearest_price(points, target_180)
        if p180 and obj.get("price") is not None:
            obj["price_change_180d"] = obj["price"] - p180[1]
            obj["price_change_180d_pct"] = pct_change(obj["price"], p180[1])
            obj["price_change_180d_date"] = p180[0].isoformat()

def update_price_history(history, objects, run_date):
    listings = history.setdefault("listings", {})
    cutoff = datetime.fromisoformat(run_date).date() - timedelta(days=int(history.get("retention_days", 365)))
    for obj in objects:
        price, url = obj.get("price"), obj.get("source_url")
        if not url or price is None:
            continue
        points = [p for p in listings.setdefault(url, []) if p.get("date") != run_date]
        points.append({
            "date": run_date, "price": price, "eur_m2": obj.get("eur_m2"),
            "source": obj.get("source"), "listing_title": obj.get("listing_title"),
        })
        points = [p for p in points if parse_date(p.get("date")) and parse_date(p.get("date")) >= cutoff]
        points.sort(key=lambda x: x.get("date", ""))
        listings[url] = points
    for url in list(listings):
        if not listings[url]:
            del listings[url]
    history["schema_version"] = "1.0"
    history["updated_at"] = datetime.now(timezone.utc).isoformat()
    return history

def main():
    now = datetime.now(timezone.utc)
    run_date = now.date().isoformat()
    previous_current = load(CURRENT, {})
    previous_objects = load(OBJECTS, {"objects": []}).get("objects", [])
    prev_by_url = {o.get("source_url"): o for o in previous_objects if o.get("source_url")}

    try:
        rows, coverage = run()
    except Exception as exc:
        HISTORY.mkdir(parents=True, exist_ok=True)
        (HISTORY / f"{run_date}-FAILED.json").write_text(json.dumps({
            "run_date": run_date, "status": "FAILED", "error": repr(exc),
            "previous_snapshot_preserved": True,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        raise

    objects = normalize(rows, run_date)
    if not objects:
        HISTORY.mkdir(parents=True, exist_ok=True)
        (HISTORY / f"{run_date}-EMPTY.json").write_text(json.dumps({
            "run_date": run_date, "status": "FAILED_EMPTY_CAPTURE", "captured_rows": 0,
            "coverage": coverage, "previous_snapshot_preserved": True,
            "reason": "Live collectors returned zero normalized listings.",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        raise RuntimeError("Live capture returned 0 normalized listings. Previous current.json was preserved; no empty snapshot was published.")

    for obj in objects:
        prev = prev_by_url.get(obj["source_url"])
        if prev:
            obj["first_seen"] = prev.get("first_seen", run_date)
        try:
            first = datetime.fromisoformat(obj["first_seen"]).date()
            obj["new_to_radar_30d"] = (now.date() - first).days <= 30
        except Exception:
            pass

    DATA.mkdir(exist_ok=True)
    HISTORY.mkdir(exist_ok=True)
    history = load_price_history()

    # Important: remove today's previous run before calculating "previous run",
    # so repeated manual runs on the same day compare to the last actual run.
    strip_today(history, run_date)
    apply_history_deltas(objects, history, now)
    history = update_price_history(history, objects, run_date)

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

    current = dict(previous_current)
    current.update({
        "run_date": run_date,
        "last_live_capture": now.isoformat(),
        "snapshot_status": "SUCCESS",
        "objects_file": "data/objects.json",
        "price_history_file": "data/price_history.json",
        "price_history_retention_days": int(history.get("retention_days", 365)),
        "capture_coverage": coverage,
        "inventory_counts": inventory_counts,
        "locations": sorted(all_locations),
        "portal_inventory_total": sum(v["portal_inventory"] for v in inventory_counts.values() if isinstance(v.get("portal_inventory"), (int, float))),
        "live_capture_total": len(objects),
        "captured_concrete_total": len(objects),
        "price_history_stats": {
            "priced_objects": sum(1 for o in objects if o.get("price") is not None),
            "previous_run_changes": sum(1 for o in objects if o.get("price_delta") is not None),
            "decreases_vs_previous": sum(1 for o in objects if (o.get("price_delta") or 0) < 0),
            "increases_vs_previous": sum(1 for o in objects if (o.get("price_delta") or 0) > 0),
            "with_30d": sum(1 for o in objects if o.get("price_change_30d") is not None),
            "with_180d": sum(1 for o in objects if o.get("price_change_180d") is not None),
            "discount_signals": sum(1 for o in objects if o.get("discount_signal")),
        },
    })

    obj_payload = {
        "schema_version": "2.3", "run_date": run_date, "objects": objects,
        "counts": {
            "total_captured_listings": len(objects),
            "by_location": {loc: sum(1 for o in objects if o["location"] == loc) for loc in sorted({o["location"] for o in objects})},
        },
        "coverage": coverage,
    }

    OBJECTS.write_text(json.dumps(obj_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    CURRENT.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    PRICE_HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    (HISTORY / f"{run_date}.json").write_text(json.dumps({
        "run_date": run_date, "status": "SUCCESS", "capture_coverage": coverage,
        "counts": obj_payload["counts"], "portal_inventory_total": current["portal_inventory_total"],
        "price_objects": [{
            "source_url": o["source_url"], "listing_id": o["listing_id"],
            "listing_title": o["listing_title"], "location": o["location"],
            "source": o["source"], "price": o["price"], "eur_m2": o["eur_m2"],
            "discount_signal": o["discount_signal"], "discount_keywords": o["discount_keywords"],
        } for o in objects if o.get("price") is not None],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
