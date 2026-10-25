"""Regression tests for V2/V3 price-history compatibility."""
from __future__ import annotations

import sys
import types
import unittest


# snapshot imports the collector, which imports Playwright. These pure ledger
# tests do not run a browser, so provide the tiny import-time interface needed
# by the collector in environments without Playwright installed.
sync_api = types.ModuleType("playwright.sync_api")
sync_api.TimeoutError = TimeoutError
sync_api.sync_playwright = lambda: None
playwright = types.ModuleType("playwright")
playwright.sync_api = sync_api
sys.modules.setdefault("playwright", playwright)
sys.modules.setdefault("playwright.sync_api", sync_api)

from radar.snapshot import (  # noqa: E402
    normalize_price_history,
    price_metrics,
    update_price_history,
)


CAPTURED_AT = "2026-09-19T07:30:54Z"
LEGACY_URL = "https://example.test/listing/one"


class PriceHistoryCompatibilityTests(unittest.TestCase):
    def test_legacy_list_format_is_losslessly_migrated_and_rekeyed(self):
        legacy = {
            "schema_version": "2.0",
            "retention_days": 365,
            "listings": {
                LEGACY_URL: [
                    {"date": "2026-08-17", "price": 100000},
                    {"date": "2026-08-18", "price": 110000},
                ]
            },
        }

        normalized = normalize_price_history(legacy)
        migrated = normalized["listings"][LEGACY_URL]
        self.assertIsInstance(migrated, dict)
        self.assertEqual(migrated["legacy_history_key"], LEGACY_URL)
        self.assertEqual(
            [item["captured_at"] for item in migrated["observations"]],
            ["2026-08-17T00:00:00Z", "2026-08-18T00:00:00Z"],
        )

        updated = update_price_history(
            normalized,
            [{
                "listing_id": "listing:canonical",
                "source_url": LEGACY_URL,
                "source": "jbc_direct",
                "run_id": "run-3",
                "price": 120000,
                "eur_m2": 2000,
            }],
            CAPTURED_AT,
        )
        self.assertNotIn(LEGACY_URL, updated["listings"])
        canonical = updated["listings"]["listing:canonical"]
        self.assertEqual(len(canonical["observations"]), 3)
        self.assertEqual(canonical["legacy_history_keys"], [LEGACY_URL])

        metrics = price_metrics(canonical, CAPTURED_AT)
        self.assertEqual(metrics["first_recorded"], 100000)
        self.assertEqual(metrics["current"], 120000)
        self.assertEqual(metrics["ledger_change"], 20000)
        self.assertEqual(metrics["observations_count"], 3)

    def test_empty_canonical_history_has_explicit_missing_metrics(self):
        metrics = price_metrics({"observations": []}, CAPTURED_AT)
        self.assertIsNone(metrics["current"])
        self.assertIsNone(metrics["first_recorded"])
        self.assertEqual(metrics["observations_count"], 0)
        self.assertEqual(metrics["30d"]["observations"], 0)

    def test_malformed_history_is_preserved_but_cannot_break_metrics(self):
        normalized = normalize_price_history({
            "listings": {
                "bad-scalar": "not an observation ledger",
                "bad-observations": {"observations": "not a list"},
                "mixed": [
                    {"date": "not-a-date", "price": 100000},
                    "unstructured legacy value",
                ],
            }
        })
        self.assertEqual(
            normalized["listings"]["bad-scalar"]["malformed_observations"],
            ["not an observation ledger"],
        )
        self.assertEqual(
            normalized["listings"]["bad-observations"]["malformed_observations"],
            ["not a list"],
        )
        mixed = normalized["listings"]["mixed"]
        self.assertEqual(mixed["malformed_observations"], ["unstructured legacy value"])
        self.assertEqual(mixed["observations"][0]["invalid_captured_at"], "not-a-date")

        metrics = price_metrics(mixed, CAPTURED_AT)
        self.assertEqual(metrics["current"], 100000)
        self.assertEqual(metrics["30d"]["observations"], 0)


if __name__ == "__main__":
    unittest.main()
