"""
Adriatic Radar snapshot runner.

STAGE 4A:
- Provides the GitHub Actions -> data/history -> commit/push infrastructure.
- It does NOT yet claim to perform the full multi-source web capture.
- The next stage will replace/extend `collect_live_inventory()` with the real
  source collectors and Project -> Unit -> Listing normalization.

This conservative behavior is intentional: a scheduled job must never create
fake "fresh" market numbers when no live source capture has been performed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CURRENT = DATA / "current.json"
HISTORY = DATA / "history"

ITALY_OFFSET_NOTE = "Scheduled GitHub Actions run; live source capture module pending Stage 4B."


def load_current() -> dict:
    if not CURRENT.exists():
        raise FileNotFoundError("data/current.json not found")
    return json.loads(CURRENT.read_text(encoding="utf-8"))


def collect_live_inventory() -> dict:
    """
    Placeholder for Stage 4B.

    We deliberately fail instead of copying old values and pretending they are
    current. This protects the radar from generating false snapshots.
    """
    raise RuntimeError(
        "Live inventory collectors are not installed yet. "
        "Stage 4A only installs the automatic GitHub snapshot infrastructure. "
        "Implement Stage 4B collectors before enabling the daily schedule."
    )


def main() -> None:
    # Keep this guard explicit so a scheduled workflow cannot silently publish
    # stale data as a new market snapshot.
    _ = load_current()
    _ = collect_live_inventory()


if __name__ == "__main__":
    main()
