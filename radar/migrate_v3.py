"""One-time, loss-aware migration of the current compatibility inventory."""
from __future__ import annotations

from datetime import datetime, timezone

from radar.snapshot import OBJECTS, REGISTRY, load, publish


def main():
    legacy = load(OBJECTS, {}).get("objects", [])
    registry = load(REGISTRY, {}).get("sources", [])
    # The old JBC collector emitted `jbc_direct`; map that durable observation
    # to the new single global JBC inventory layer without changing its URL.
    source_id = "jbc_official_inventory"
    now = datetime.now(timezone.utc)
    run_id = "migration-" + now.strftime("%Y%m%dT%H%M%SZ")
    rows = []
    for index, row in enumerate(legacy):
        migrated = dict(row)
        migrated["source_id"] = source_id if row.get("source") == "jbc_direct" else row.get("source") or "legacy_unknown"
        migrated["source_name"] = "JBC Immobiliare" if migrated["source_id"] == source_id else row.get("source")
        migrated["raw_reference"] = f"legacy:data/objects.json#{index}"
        rows.append(migrated)
    manifest = {
        "schema_version": "1.0", "run_id": run_id, "started_at": now.isoformat(), "finished_at": now.isoformat(),
        "migration": True,
        "captures": [{
            "source_id": source_id, "source_name": "JBC Immobiliare", "location": "Adriatic coverage",
            "requested_url": None, "started_at": now.isoformat(), "finished_at": now.isoformat(),
            "pages_visited": None, "rows_discovered": len(rows), "rows_accepted": None, "rows_rejected": None,
            "rejection_reason_counts": {}, "errors": [], "status": "MIGRATED_LEGACY",
            "mandatory": True, "raw_references": [row["raw_reference"] for row in rows],
        }],
    }
    result = publish(rows, manifest, now=now)
    print({"run_id": run_id, "listings": len(result["listings"]), "projects": len(result["projects"]), "units": len(result["units"]), "rejected": len(result["rejected"])})


if __name__ == "__main__":
    main()
