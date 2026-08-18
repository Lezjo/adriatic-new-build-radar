from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

# inventory_audit.py is stored in the repository ROOT.
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DEBUG = DATA / "debug"
CURRENT = DATA / "current.json"
OBJECTS = DATA / "objects.json"
SOURCES = ROOT / "radar" / "sources.json"
OUT = DEBUG / "inventory_audit.json"
MD = DEBUG / "inventory_audit.md"

MANDATORY = {
    "Immobiliare.it": [
        "Jesolo", "Caorle", "Cavallino-Treporti", "San Donà di Piave"
    ],
    "Idealista": [
        "Jesolo", "San Donà di Piave", "Cavallino-Treporti"
    ],
    "Casa.it": [
        "Jesolo", "Cavallino-Treporti", "San Donà di Piave"
    ],
    "JBC": [
        "Jesolo", "Jesolo Paese", "Ca' Gamba", "Eraclea",
        "Ponte di Piave", "Fossalta di Piave", "Noventa di Piave",
        "San Donà di Piave"
    ],
}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def canon(value):
    """Canonical URL used for cross-source deduplication."""
    if not value:
        return ""
    try:
        p = urlparse(str(value))
        q = [
            (k, v)
            for k, v in parse_qsl(p.query)
            if k.lower() not in {
                "utm_source", "utm_medium", "utm_campaign",
                "utm_content", "utm_term"
            }
        ]
        return urlunparse(
            (
                p.scheme.lower(),
                p.netloc.lower(),
                p.path.rstrip("/"),
                "",
                urlencode(q),
                "",
            )
        )
    except Exception:
        return str(value)


def src(name):
    n = str(name or "").lower()
    if "immobiliare" in n:
        return "Immobiliare.it"
    if "idealista" in n:
        return "Idealista"
    if "casa.it" in n or n == "casa":
        return "Casa.it"
    if "jbc" in n:
        return "JBC"
    return n or "UNKNOWN"


def loc(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def row_url(row):
    return canon(
        row.get("source_url")
        or row.get("url")
        or row.get("listing_url")
    )


def rows():
    """
    Read the current normalized inventory.
    Prefer objects.json; fall back to current.json.
    """
    candidates = [OBJECTS, CURRENT]

    for path in candidates:
        data = load(path, None)
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            for key in ("objects", "listings", "items", "rows", "inventory"):
                value = data.get(key)
                if isinstance(value, list):
                    return value

    return []


def configured():
    data = load(SOURCES, {})
    out = []

    if isinstance(data, dict):
        for location, specs in data.items():
            if not isinstance(specs, list):
                continue

            for spec in specs:
                if not isinstance(spec, dict):
                    continue

                expected = spec.get(
                    "expected_count",
                    spec.get(
                        "live_results",
                        spec.get("expected_results")
                    ),
                )

                out.append(
                    {
                        "location": str(location),
                        "source": src(spec.get("name")),
                        "name": spec.get("name"),
                        "url": spec.get("url"),
                        "expected": expected,
                    }
                )

    return out


def counts(records):
    by_source = Counter()
    by_pair = Counter()
    unique_source = defaultdict(set)
    unique_pair = defaultdict(set)

    for record in records:
        source = src(record.get("source"))
        location = str(record.get("location") or "Unknown")
        url = row_url(record)

        by_source[source] += 1
        by_pair[(location, source)] += 1

        if url:
            unique_source[source].add(url)
            unique_pair[(location, source)].add(url)

    return {
        "by_source": dict(by_source),
        "by_pair": {
            f"{location} | {source}": count
            for (location, source), count in sorted(by_pair.items())
        },
        "unique_urls_by_source": {
            source: len(values)
            for source, values in unique_source.items()
        },
        "unique_urls_by_pair": {
            f"{location} | {source}": len(values)
            for (location, source), values
            in sorted(unique_pair.items())
        },
    }


def overlap(records):
    sources_by_url = defaultdict(set)

    for record in records:
        url = row_url(record)
        if url:
            sources_by_url[url].add(src(record.get("source")))

    duplicated = [
        {
            "url": url,
            "sources": sorted(sources),
        }
        for url, sources in sources_by_url.items()
        if len(sources) > 1
    ]

    return {
        "urls_seen_in_multiple_sources": len(duplicated),
        "examples": duplicated[:200],
    }


def audit_coverage(config, records):
    """
    Compare the latest collector diagnostics with normalized inventory.

    Important:
    a captured count is NOT treated as proof of 100% completeness unless
    an independent live-result denominator exists.
    """
    debug_log = load(DEBUG / "capture_debug.json", [])
    latest = {}

    if isinstance(debug_log, list):
        for item in debug_log:
            key = (
                str(item.get("location")),
                src(item.get("source")),
            )
            latest[key] = item

    unique_by_pair = counts(records)["unique_urls_by_pair"]
    result = []

    for spec in config:
        key = (spec["location"], spec["source"])
        diagnostic = latest.get(key, {})

        captured = int(
            diagnostic.get("records_captured")
            or unique_by_pair.get(
                f"{spec['location']} | {spec['source']}",
                0,
            )
            or 0
        )

        pages = int(diagnostic.get("pages_captured") or 0)
        expected = spec["expected"]
        completeness = None

        if expected is not None:
            try:
                expected_int = int(expected)
                if expected_int > 0:
                    completeness = round(
                        captured / expected_int * 100,
                        1,
                    )
            except (TypeError, ValueError):
                pass

        status = diagnostic.get("last_http_status")
        has_http_error = False

        try:
            has_http_error = status is not None and int(status) >= 400
        except (TypeError, ValueError):
            pass

        if diagnostic.get("error") or has_http_error:
            verdict = "RED — capture error"
        elif captured == 0:
            verdict = "RED — 0 records"
        elif (
            expected is not None
            and completeness is not None
            and captured < int(expected)
        ):
            verdict = "YELLOW — below declared inventory"
        else:
            verdict = (
                "GREEN — captured; live count not independently proven"
            )

        result.append(
            {
                "location": spec["location"],
                "source": spec["source"],
                "configured_url": spec["url"],
                "pages_captured": pages,
                "records_captured": captured,
                "expected_live_results": expected,
                "completeness_pct": completeness,
                "http_status": status,
                "error": diagnostic.get("error"),
                "verdict": verdict,
            }
        )

    # JBC has its own manifests and is audited independently.
    for manifest_path in sorted(
        DEBUG.glob("*__jbc_manifest.json")
    ):
        manifest = load(manifest_path, {})

        if not isinstance(manifest, dict):
            continue

        records_captured = int(
            manifest.get("records_captured") or 0
        )

        result.append(
            {
                "location": manifest.get("location"),
                "source": "JBC",
                "configured_url": (
                    manifest.get("start_urls") or [None]
                )[0],
                "pages_captured": manifest.get("hub_pages_visited"),
                "records_captured": records_captured,
                "expected_live_results": None,
                "completeness_pct": None,
                "http_status": manifest.get("last_http_status"),
                "error": (
                    "; ".join(
                        manifest.get("errors", [])[:5]
                    )
                    or None
                ),
                "verdict": (
                    "RED — 0 JBC records"
                    if not records_captured
                    else
                    "GREEN — JBC discovery produced records; "
                    "completeness requires live-source denominator"
                ),
                "jbc_candidate_detail_urls": manifest.get(
                    "candidate_detail_urls"
                ),
                "jbc_detail_pages_visited": manifest.get(
                    "detail_pages_visited"
                ),
                "jbc_sitemap_urls_found": manifest.get(
                    "sitemap_urls_found"
                ),
                "jbc_unit_records": manifest.get(
                    "unit_records"
                ),
                "jbc_project_records": manifest.get(
                    "project_records"
                ),
            }
        )

    return result


def gaps(config):
    configured_pairs = {
        (item["source"], loc(item["location"]))
        for item in config
    }

    missing = []

    for source, locations in MANDATORY.items():
        for location in locations:
            if (source, loc(location)) not in configured_pairs:
                missing.append(
                    {
                        "source": source,
                        "location": location,
                        "issue": (
                            "Mandatory source/location is not configured "
                            "in radar/sources.json"
                        ),
                    }
                )

    return missing


def report():
    records = rows()
    config = configured()

    return {
        "schema_version": "inventory-audit-1.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "OK" if records else "NO_DATA",
        "current_rows": len(records),
        "unique_source_urls": len(
            {
                row_url(record)
                for record in records
                if row_url(record)
            }
        ),
        "configured_source_locations": len(config),
        "coverage": audit_coverage(config, records),
        "mandatory_configuration_gaps": gaps(config),
        "counts": counts(records),
        "cross_source_overlap": overlap(records),
        "methodology": {
            "important": [
                "Captured count is NOT proof of complete live inventory.",
                "Portal search-result totals must be measured independently from the collector.",
                "Project, Unit and Listing are separate levels.",
                "Same canonical URL across sources is overlap, not a new unique listing.",
                "JBC is audited as a mandatory independent source layer.",
            ],
            "next_upgrade": (
                "Add independent live-result denominator extraction "
                "per portal/location."
            ),
        },
    }


def markdown(report_data):
    lines = [
        "# Adriatic Radar — Inventory Audit",
        "",
        f"Generated: `{report_data['generated_at']}`",
        "",
        f"Current rows: **{report_data['current_rows']}**",
        (
            "Unique source URLs: "
            f"**{report_data['unique_source_urls']}**"
        ),
        "",
        "## Coverage",
        "",
        "| Location | Source | Pages | Captured | Expected live | Completeness | Verdict |",
        "|---|---|---:|---:|---:|---:|---|",
    ]

    for item in report_data["coverage"]:
        expected = (
            "-"
            if item["expected_live_results"] is None
            else str(item["expected_live_results"])
        )
        completeness = (
            "-"
            if item["completeness_pct"] is None
            else f"{item['completeness_pct']}%"
        )

        lines.append(
            f"| {item.get('location', '')} | "
            f"{item.get('source', '')} | "
            f"{item.get('pages_captured', '-')} | "
            f"{item.get('records_captured', 0)} | "
            f"{expected} | "
            f"{completeness} | "
            f"{item.get('verdict', '')} |"
        )

    lines += [
        "",
        "## Mandatory configuration gaps",
        "",
    ]

    missing = report_data["mandatory_configuration_gaps"]

    if not missing:
        lines.append("None detected.")
    else:
        for item in missing:
            lines.append(
                f"- 🔴 **{item['source']} — {item['location']}**: "
                f"{item['issue']}"
            )

    lines += [
        "",
        "## Current inventory by source",
        "",
        "| Source | Captured rows |",
        "|---|---:|",
    ]

    for source, count in sorted(
        report_data["counts"]["by_source"].items()
    ):
        lines.append(f"| {source} | {count} |")

    lines += [
        "",
        "## Cross-source overlap",
        "",
        "URLs present in multiple source layers: "
        f"**{report_data['cross_source_overlap']['urls_seen_in_multiple_sources']}**",
        "",
        "## Important",
        "",
        (
            "This audit deliberately does not call a capture 100% complete "
            "merely because Playwright returned records. An independent "
            "live-result denominator is required for each portal/location."
        ),
        "",
    ]

    return "\n".join(lines)


def main():
    result = report()

    DEBUG.mkdir(parents=True, exist_ok=True)

    OUT.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    MD.write_text(
        markdown(result),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": result["status"],
                "current_rows": result["current_rows"],
                "unique_source_urls": result["unique_source_urls"],
                "configured_source_locations": result[
                    "configured_source_locations"
                ],
                "mandatory_gaps": len(
                    result["mandatory_configuration_gaps"]
                ),
                "overlap_urls": result[
                    "cross_source_overlap"
                ]["urls_seen_in_multiple_sources"],
                "files": [
                    str(OUT),
                    str(MD),
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
