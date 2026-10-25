"""Coverage contract tests for mandatory source runs."""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch


sync_api = types.ModuleType("playwright.sync_api")
sync_api.TimeoutError = TimeoutError
sync_api.sync_playwright = lambda: None
playwright = types.ModuleType("playwright")
playwright.sync_api = sync_api
sys.modules.setdefault("playwright", playwright)
sys.modules.setdefault("playwright.sync_api", sync_api)

from radar.snapshot import evaluate_coverage  # noqa: E402


class CoverageTests(unittest.TestCase):
    @patch("radar.snapshot.load_sources")
    def test_cavallino_apartment_run_is_mandatory(self, load_sources):
        load_sources.return_value = {
            "cavallino-treporti": [{
                "name": "idealista_apartments",
                "url": "https://example.test/cavallino",
            }]
        }
        result = evaluate_coverage([])
        self.assertFalse(result["mandatory_complete"])
        self.assertEqual(result["mandatory_results"][0]["status"], "MISSING_RUN")

    @patch("radar.snapshot.load_sources")
    def test_verified_zero_is_present_but_does_not_make_full_inventory(self, load_sources):
        load_sources.return_value = {
            "jesolo": [{
                "name": "immobiliare_new_build",
                "url": "https://example.test/jesolo",
            }]
        }
        result = evaluate_coverage([{
            "location": "jesolo",
            "source": "immobiliare_new_build",
            "status": "VERIFIED_ZERO_RESULTS",
        }])
        self.assertTrue(result["mandatory_complete"])
        self.assertTrue(result["full_inventory_allowed"])


if __name__ == "__main__":
    unittest.main()
