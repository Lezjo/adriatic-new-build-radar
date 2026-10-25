"""Schema-tolerant audit of the published radar inventory and source coverage."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEBUG = DATA / "debug"
SUCCESS = {"PASS", "SUCCESS", "SUCCESS_WITH_RESULTS", "VERIFIED_ZERO_RESULTS", "ZERO_VERIFIED"}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def rows(payload, key: str):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    value = payload.get(key, [])
    return list(value.values()) if isinstance(value, dict) else value if isinstance(value, list) else []


def source_runs():
    out = []
    for location, specs in load(ROOT / "radar" / "sources.json", {}).items():
        if isinstance(specs, list):
            out.extend(
                {"location": location, "source": spec.get("name", "unknown")}
                for spec in specs
                if isinstance(spec, dict) and spec.get("url") and spec.get("mandatory", True)
            )
    return out


def coverage_for(current: dict):
    direct = current.get("coverage") or current.get("capture_coverage")
    if isinstance(direct, list):
        return direct, "current_manifest"
    run_id = current.get("run_id")
    if isinstance(run_id, str):
        immutable = load(DATA / "runs" / run_id / "coverage.json", None)
        if isinstance(immutable, list):
            return immutable, "immutable_run"
    evaluated = current.get("coverage_evaluation", {}).get("mandatory_results", [])
    return evaluated if isinstance(evaluated, list) else [], "coverage_evaluation"


def main():
    current = load(DATA / "current.json", {})
    objects = rows(load(DATA / "objects.json", {}), "objects")
    configured = source_runs()
    coverage, coverage_source = coverage_for(current)
    want = {(x["location"].lower(), x["source"].lower()) for x in configured}
    have = {(str(x.get("location", "")).lower(), str(x.get("source", "")).lower()) for x in coverage}
    global_jbc = any(
        str(item.get("source", "")).lower() == "jbc_direct"
        and str(item.get("location", "")).upper() == "GLOBAL"
        and str(item.get("status", "")).upper() in SUCCESS
        for item in coverage
    )
    if global_jbc:
        have.update(pair for pair in want if pair[1] == "jbc_direct")
    missing = sorted(f"{location} | {source}" for location, source in want - have)
    published = Counter(
        (str(item.get("location", "")).lower(), str(item.get("source", item.get("source_id", "")).lower()))
        for item in objects
    )
    outcomes = Counter(str(item.get("status") or "NOT_REPORTED").upper() for item in coverage)
    incomplete = sum(count for status, count in outcomes.items() if status not in SUCCESS)
    report = {
        "schema_version": "3.2",
        "run_id": current.get("run_id"),
        "inventory_mode": current.get("inventory_mode", current.get("snapshot_status")),
        "coverage_source": coverage_source,
        "objects": len(objects),
        "configured_mandatory_runs": len(want),
        "reported_mandatory_runs": len(want & have),
        "missing_run_reports": missing,
        "source_outcomes": dict(sorted(outcomes.items())),
        "incomplete_source_runs": incomplete,
        "published_by_source_location": {f"{location} | {source}": count for (location, source), count in sorted(published.items())},
        "provenance_missing": sum(1 for item in objects if not (item.get("raw_reference") or item.get("raw_path") or item.get("source_url"))),
        "status": "PASS" if not missing and not incomplete else "DEGRADED",
    }
    DEBUG.mkdir(parents=True, exist_ok=True)
    (DEBUG / "inventory_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (DEBUG / "inventory_audit.md").write_text(
        "# Inventory audit\n\n" + "\n".join(
            f"- {key}: {value}" for key, value in report.items()
            if key not in {"missing_run_reports", "published_by_source_location"}
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
