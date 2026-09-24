import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))

from summary import build_summary  # noqa: E402

FIXED_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)


class TestBuildSummary(unittest.TestCase):
    def test_success_summarizes_counts_and_top_vendors(self):
        status = {
            "last_run": "2026-09-22T11:40:00Z",
            "ok": True,
            "dry_run": True,
            "counts": {"scanned": 12, "new": 12, "filed": 0, "errors": 0},
            "results": [
                {"vendor": "Uber"}, {"vendor": "Uber"}, {"vendor": "Temuemail"},
            ],
        }
        summary = build_summary(status, now=FIXED_NOW)
        self.assertIn("validation mode", summary)
        self.assertIn("12 scanned", summary)
        self.assertIn("Uber", summary)

    def test_live_mode_says_live_not_validation(self):
        status = {
            "last_run": "2026-09-22T11:40:00Z", "ok": True, "dry_run": False,
            "counts": {"scanned": 1, "new": 1, "filed": 1, "errors": 0},
            "results": [{"vendor": "Uber"}],
        }
        summary = build_summary(status, now=FIXED_NOW)
        self.assertIn("live mode", summary)

    def test_failed_run_leads_with_error(self):
        status = {
            "last_run": "2026-09-22T11:40:00Z", "ok": False, "dry_run": True,
            "counts": {}, "results": [], "error": "GmailClient auth failed",
        }
        summary = build_summary(status, now=FIXED_NOW)
        self.assertIn("GmailClient auth failed", summary)

    def test_failed_run_without_error_key_falls_back_to_counts(self):
        # As of 2nd-brain commit 41bf6d1 (2026-09-23), `ok` is derived from
        # counts["errors"] == 0 — a message-level failure produces ok: false
        # with NO "error" key. run.py's own main() falls back to "{errors}
        # of {new} message(s) errored"; build_summary must match it exactly.
        status = {
            "last_run": "2026-09-22T11:40:00Z", "ok": False, "dry_run": True,
            "counts": {"scanned": 14, "new": 14, "filed": 7, "errors": 7},
            "results": [],
        }
        summary = build_summary(status, now=FIXED_NOW)
        self.assertIn("7 of 14 message(s) errored", summary)

    def test_result_without_vendor_key_does_not_raise(self):
        status = {
            "last_run": "2026-09-22T11:40:00Z", "ok": True, "dry_run": True,
            "counts": {"scanned": 1, "new": 1, "filed": 0, "errors": 1},
            "results": [{"msg_id": "abc", "errors": ["ValueError: boom"]}],
        }
        summary = build_summary(status, now=FIXED_NOW)  # must not raise KeyError
        self.assertIn("1 error", summary)

    def test_stale_last_run_gets_warning_prefix(self):
        status = {
            "last_run": "2026-09-20T00:00:00Z",  # >24h before FIXED_NOW
            "ok": True, "dry_run": True,
            "counts": {"scanned": 0, "new": 0, "filed": 0, "errors": 0},
            "results": [],
        }
        summary = build_summary(status, now=FIXED_NOW)
        self.assertIn("stale", summary.lower())

    def test_fresh_last_run_has_no_warning_prefix(self):
        status = {
            "last_run": "2026-09-22T11:40:00Z",  # 20 min before FIXED_NOW
            "ok": True, "dry_run": True,
            "counts": {"scanned": 0, "new": 0, "filed": 0, "errors": 0},
            "results": [],
        }
        summary = build_summary(status, now=FIXED_NOW)
        self.assertNotIn("stale", summary.lower())


if __name__ == "__main__":
    unittest.main()
