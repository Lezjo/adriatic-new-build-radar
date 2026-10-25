"""Outcome classification regression tests for portal source reports."""
from __future__ import annotations

import sys
import types
import unittest


sync_api = types.ModuleType("playwright.sync_api")
sync_api.TimeoutError = TimeoutError
sync_api.sync_playwright = lambda: None
playwright = types.ModuleType("playwright")
playwright.sync_api = sync_api
sys.modules.setdefault("playwright", playwright)
sys.modules.setdefault("playwright.sync_api", sync_api)

from radar.collectors.portal_capture import classify_capture_status  # noqa: E402


class CaptureStatusTests(unittest.TestCase):
    def classify(self, **overrides):
        values = {
            "records_published": 0,
            "candidates_seen": 0,
            "blocked_reason": None,
            "http_statuses": [200],
            "errors": [],
            "rejected": [],
            "fallback_records": 0,
        }
        values.update(overrides)
        return classify_capture_status(**values)

    def test_403_is_blocked_not_zero_or_broken(self):
        self.assertEqual(self.classify(http_statuses=[403], blocked_reason="http_403"), "BLOCKED")

    def test_timeout_is_distinct(self):
        self.assertEqual(self.classify(http_statuses=[], errors=["timeout:https://portal.test"]), "TIMEOUT")

    def test_candidates_without_parsed_rows_are_parser_error(self):
        self.assertEqual(self.classify(candidates_seen=3), "PARSER_ERROR")

    def test_valid_empty_page_is_verified_zero(self):
        self.assertEqual(self.classify(), "VERIFIED_ZERO_RESULTS")

    def test_transport_error_is_not_zero(self):
        self.assertEqual(self.classify(http_statuses=[], errors=["page:Error: offline"]), "SOURCE_UNAVAILABLE")

    def test_no_response_is_not_verified_zero(self):
        self.assertEqual(self.classify(http_statuses=[]), "SOURCE_UNAVAILABLE")

    def test_rows_recovered_after_block_are_partial(self):
        self.assertEqual(self.classify(records_published=1, blocked_reason="http_403", fallback_records=1), "PARTIAL")

    def test_clean_rows_are_success_with_results(self):
        self.assertEqual(self.classify(records_published=1), "SUCCESS_WITH_RESULTS")


if __name__ == "__main__":
    unittest.main()
