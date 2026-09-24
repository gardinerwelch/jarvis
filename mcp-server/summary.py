"""Pure summary-building logic for the receipts-status MCP tool. Kept free
of any MCP SDK import so it's unit-testable as plain Python — see
docs/superpowers/specs/2026-09-22-receipts-status-mcp-server-design.md
in the jarvis repo for the full design."""
from collections import Counter
from datetime import datetime, timedelta, timezone

STALE_AFTER = timedelta(hours=24)


def _parse_last_run(last_run):
    return datetime.strptime(last_run, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def build_summary(status, now=None):
    now = now or datetime.now(timezone.utc)

    if status.get("ok") is False:
        # Matches run.py's own main() fallback exactly (as of commit 41bf6d1,
        # 2026-09-23): a message-level failure carries no "error" key, only
        # counts — so fall back to the same "{errors} of {new} message(s)
        # errored" wording the pipeline's own log line already uses.
        fail_counts = status.get("counts") or {}
        return status.get("error") or "{} of {} message(s) errored".format(
            fail_counts.get("errors", "?"), fail_counts.get("new", "?"))

    counts = status.get("counts") or {}
    results = status.get("results") or []

    vendor_counts = Counter(r["vendor"] for r in results if r.get("vendor"))
    top_vendors = [v for v, _count in vendor_counts.most_common(3)]

    mode = "validation mode" if status.get("dry_run") else "live mode"
    parts = [
        "Still in {}.".format(mode),
        "{} scanned, {} new, {} filed live, {} errors.".format(
            counts.get("scanned", 0), counts.get("new", 0),
            counts.get("filed", 0), counts.get("errors", 0)),
    ]
    if top_vendors:
        parts.append("Top vendors: {}.".format(", ".join(top_vendors)))
    summary = " ".join(parts)

    last_run = status.get("last_run")
    if last_run:
        try:
            age = now - _parse_last_run(last_run)
        except ValueError:
            age = None
        if age is not None and age > STALE_AFTER:
            summary = ("Heads up, this hasn't updated in over a day — might be a "
                        "stale sync rather than a clean pipeline. ") + summary

    return summary
