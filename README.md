# Adriatic New Build Radar

Radar collects evidence-backed new-build listings for the Adriatic and the
mandatory Bologna–Modena markets. Its canonical product contract is
[`Першоджерело.md`](Першоджерело.md). That document is the single Source of
Truth for Phase 1; this README describes the implementation, not a competing
requirements document.

## Architecture

```text
radar/sources.json
  -> radar/collectors/portal_capture.py
  -> data/raw/<run-id>/                 (raw capture evidence)
  -> radar/snapshot.py                  (normalisation and deduplication)
  -> Project -> Unit -> Listing -> Observation
  -> data/runs/<run-id>/                (immutable run artifacts)
  -> data/price_history.json            (append-only observation ledger)
  -> data/current.json                  (public manifest / last-known-good inventory)
  -> index.html -> GitHub Pages
```

The canonical working files are `Першоджерело.md`, this README,
`data/schema.json`, `data/locations.json`, `radar/sources.json`,
`radar/snapshot.py`, the collector modules, `data/current.json`, and
`index.html`. Generated run evidence is intentionally not a second working
configuration: it is immutable provenance.

## Data and publication rules

- `radar/sources.json` is the executable source registry. A location is only
  collectable when it has source specifications there; `data/locations.json`
  remains the geographic registry, including configured-but-unverified
  markets.
- Each collector attempt records coverage and errors. A blocked or failed
  source is reported as such; it is not converted into zero inventory.
- Every execution creates `data/runs/<run-id>/` before collection. The run
  keeps its entities, observations, rejections, coverage, and run manifest.
- A partial mandatory-source run is retained as immutable evidence but cannot
  replace a previously complete public inventory. `data/current.json` reports
  whether it displays the current run or the last known good full inventory.
- `data/history/YYYY-MM-DD.json` is a compact daily summary. Historical runs,
  timestamps, run IDs, raw references, and source provenance must not be
  overwritten merely to simplify file naming.
- `data/price_history.json` is an evidence ledger. Price deltas are calculated
  from observed listing prices; a portal promotion remains a separate signal.

## Schema compatibility

`data/schema.json` is the V3.2 logical contract from `Першоджерело.md`. It
defines the aggregate Source → SourceRun → RawCapture → Observation → Listing
→ Unit → Project contract. Pipeline artifacts have independently versioned
transport formats: current snapshots use the version emitted by
`radar/snapshot.py`, while existing V2/V3/V4 run files remain immutable and
readable. Do not relabel or rewrite historical files as part of a schema
upgrade. A future breaking change must add a tested reader/migration before
changing producers or consumers.

## Running locally

Install the collector dependencies and browser once, then run the pipeline:

```bash
python3 -m pip install -r radar/requirements.txt
python3 -m playwright install chromium
python3 -m radar.snapshot
python3 inventory_audit.py
```

The GitHub Actions workflow runs the same snapshot daily at 06:00 UTC and on
manual dispatch. On success it commits generated data; that push triggers the
Pages deployment workflow. Source diagnostics are uploaded even if a run has
errors.

## Source discovery documentation

`Першоджерело.md` contains the active source-discovery and registry
requirements. No separate `SOURCE_DISCOVERY_REQUIREMENTS_UA.md` exists in this
checkout or its available Git history. Do not create a replacement document
without a recoverable source; incorporate it into `Першоджерело.md` only when
the original requirements are available for comparison.
