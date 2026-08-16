# Adriatic New Build Radar — V2 data architecture

## Structure

- `index.html` — stable front-end. Do not rewrite it on every radar run.
- `data/current.json` — current radar snapshot. Replace this file on every run.
- `data/history/YYYY-MM-DD.json` — immutable historical snapshot.
- `data/schema.json` — data model / future object fields.

## Update workflow

1. Radar run collects live inventory and project/unit/listing records.
2. Create a new `data/history/YYYY-MM-DD.json`.
3. Update `data/current.json`.
4. Commit/push the `data/` changes to GitHub.
5. GitHub Pages keeps the same public URL and renders the new numbers.

The front-end fetches `data/current.json` at runtime with cache-busting, so inventory cards and deltas can change without replacing `index.html`.

## Important

This first V2 snapshot is a migration of the existing Master HTML report. It does not claim that every portal listing has already been captured as an individual row. Portal inventory counts and captured concrete rows are intentionally separate.

Future radar runs should populate the Project → Unit → Listing fields in `data/current.json`.
