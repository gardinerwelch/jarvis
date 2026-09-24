# Receipts Status MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give JARVIS a read-only MCP tool that answers "what's the status of my receipts pipeline?" by reading the status.json the receipts-ingest daemon already writes.

**Architecture:** A pure summary-building module (`summary.py`, no MCP dependency, fully unit-testable) is wrapped by a thin FastMCP server (`receipts_status_server.py`) that resolves the daemon's `status_dir` and exposes one tool, `receipts_status`. Registered in `~/.claude.json` so JARVIS's bridge picks it up automatically.

**Tech Stack:** Python 3, `mcp` SDK (`FastMCP`), `unittest` (stdlib) — all inside a dedicated venv at `mcp-server/.venv` in the 2nd-brain repo.

**Spec:** `docs/superpowers/specs/2026-09-22-receipts-status-mcp-server-design.md` (this repo) — read it alongside this plan; the plan assumes its findings (cross-host settings drift, missing venv, etc.) without re-deriving them.

## Global Constraints

- **Strictly read-only.** No writes, no shell-out, no ledger DB access, no Gmail call — nothing in this build may mutate anything (spec: Safety).
- **Dedicated venv required.** `mcp-server/.venv`, with `mcp` installed into it. `~/.claude.json` must point at that venv's absolute `python3`, never a bare `python3` (spec: Architecture correction).
- **Import convention.** `receipts_ingest` has no `__init__.py`-based package install; make it importable via `sys.path.insert(0, str(<repo_root>/"scripts"))`, exactly as `scripts/receipts_ingest/run.py:20` already does (spec: Components).
- **Test convention.** All new tests are `unittest.TestCase` subclasses with a manual `sys.path.insert` at the top of the file, matching every existing file in `2nd-brain/tests/` (e.g. `test_receipts_ledger.py`). No `pytest` fixtures, no `conftest.py` (spec: Testing correction).
- **Vendor tally must use `.get("vendor")`, never `result["vendor"]`** — per-message-exception result dicts from `run_once()` (run.py) have no `vendor` key (spec: Components).
- **Staleness threshold: 24 hours.** If `last_run` is older than that, the summary must lead with a plain staleness warning rather than reporting the counts as current (spec: Error handling).
- **Return shape:** `{"summary": str, "ok": bool | None, "last_run": str | None, "dry_run": bool | None, "counts": dict}`. `ok`/`last_run`/`dry_run` are `None` and `counts` is `{}` on the missing-file and malformed-JSON paths (spec: Components).
- **Ok:false fallback matches `run.py`'s own logging exactly.** `"{errors} of {new} message(s) errored"` when there's no `error` key — added 2026-09-24 after discovering commit `41bf6d1` changed `ok` from hardcoded `true` to derived, post-dating the original spec lock (spec: Error handling correction; ledger: Ruling 1).

---

### Task 1: `build_summary()` — pure summary logic

**Files:**
- Create: `/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/summary.py`
- Test: `/Users/gardinerwelch/Documents/_Projects/2nd-brain/tests/test_receipts_status_summary.py`

**Interfaces:**
- Produces: `build_summary(status: dict, now: datetime | None = None) -> str` — pure function, no file I/O, no MCP dependency. `now` defaults to `datetime.now(timezone.utc)`; tests always pass a fixed `now` explicitly.
- Produces: `STALE_AFTER = timedelta(hours=24)` (module-level constant in `summary.py`, used by both `build_summary` and Task 2's `read_status_result`).

- [ ] **Step 1: Write the failing tests**

Create `/Users/gardinerwelch/Documents/_Projects/2nd-brain/tests/test_receipts_status_summary.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/gardinerwelch/Documents/_Projects/2nd-brain && python3 -m unittest tests.test_receipts_status_summary -v`
Expected: FAIL (or ERROR) — `summary.py` doesn't exist yet, `ModuleNotFoundError: No module named 'summary'`.

- [ ] **Step 3: Write minimal implementation**

Create `/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/summary.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/gardinerwelch/Documents/_Projects/2nd-brain && python3 -m unittest tests.test_receipts_status_summary -v`
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/gardinerwelch/Documents/_Projects/2nd-brain
git add mcp-server/summary.py tests/test_receipts_status_summary.py
git commit -m "Add pure summary-building logic for receipts-status MCP tool"
```

---

### Task 2: `read_status_result()` — file I/O + error handling

**Files:**
- Modify: `/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/summary.py`
- Modify: `/Users/gardinerwelch/Documents/_Projects/2nd-brain/tests/test_receipts_status_summary.py`

**Interfaces:**
- Consumes: `build_summary(status, now)` from Task 1 (same file, no import needed — staleness is handled inside `build_summary` itself, `read_status_result` doesn't touch `STALE_AFTER` directly).
- Produces: `read_status_result(status_path: Path, now: datetime | None = None) -> dict`, returning the full tool return shape defined in Global Constraints. Task 3's MCP server calls this directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_receipts_status_summary.py` (add these imports at the top alongside the existing ones, and this new class at the bottom, before the `if __name__ == "__main__":` line):

```python
import json
import tempfile
```

```python
class TestReadStatusResult(unittest.TestCase):
    def test_missing_file_returns_placeholder_shape(self):
        with tempfile.TemporaryDirectory() as d:
            result = read_status_result(Path(d) / "status.json", now=FIXED_NOW)
        self.assertIsNone(result["ok"])
        self.assertIsNone(result["last_run"])
        self.assertIsNone(result["dry_run"])
        self.assertEqual(result["counts"], {})
        self.assertIn("no receipts status recorded", result["summary"])

    def test_malformed_json_returns_placeholder_shape(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "status.json"
            path.write_text("{not valid json")
            result = read_status_result(path, now=FIXED_NOW)
        self.assertIsNone(result["ok"])
        self.assertIn("no receipts status recorded", result["summary"])

    def test_valid_file_returns_full_shape(self):
        fixture = {
            "last_run": "2026-09-22T11:40:00Z", "ok": True, "dry_run": True,
            "counts": {"scanned": 3, "new": 3, "filed": 0, "errors": 0},
            "results": [{"vendor": "Uber"}],
            "candidates": [], "candidates_pass_error": None,
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "status.json"
            path.write_text(json.dumps(fixture))
            result = read_status_result(path, now=FIXED_NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["last_run"], "2026-09-22T11:40:00Z")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["counts"]["scanned"], 3)
        self.assertIn("Uber", result["summary"])
```

Update the existing import line to also pull in `read_status_result`:

```python
from summary import build_summary, read_status_result  # noqa: E402
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/gardinerwelch/Documents/_Projects/2nd-brain && python3 -m unittest tests.test_receipts_status_summary -v`
Expected: the 3 new tests FAIL/ERROR — `ImportError: cannot import name 'read_status_result'`.

- [ ] **Step 3: Write minimal implementation**

Append to `mcp-server/summary.py` (add `import json` and `from pathlib import Path` to the existing imports at the top):

```python
import json
from pathlib import Path
```

```python
def read_status_result(status_path, now=None):
    now = now or datetime.now(timezone.utc)
    status_path = Path(status_path)

    if not status_path.exists():
        return {"summary": "no receipts status recorded yet", "ok": None,
                "last_run": None, "dry_run": None, "counts": {}}

    try:
        status = json.loads(status_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"summary": "no receipts status recorded yet", "ok": None,
                "last_run": None, "dry_run": None, "counts": {}}

    return {
        "summary": build_summary(status, now=now),
        "ok": status.get("ok"),
        "last_run": status.get("last_run"),
        "dry_run": status.get("dry_run"),
        "counts": status.get("counts") or {},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/gardinerwelch/Documents/_Projects/2nd-brain && python3 -m unittest tests.test_receipts_status_summary -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/gardinerwelch/Documents/_Projects/2nd-brain
git add mcp-server/summary.py tests/test_receipts_status_summary.py
git commit -m "Add read_status_result: file I/O + error handling for receipts status"
```

---

### Task 3: MCP server wrapper + venv

**Files:**
- Create: `/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/receipts_status_server.py`
- Create: `/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/requirements.txt`
- Modify: `/Users/gardinerwelch/Documents/_Projects/2nd-brain/.gitignore`

**Interfaces:**
- Consumes: `read_status_result(status_path, now=None)` from Task 2 (same directory, plain import).
- Consumes: `receipts_ingest.config.resolve_settings_path(repo_root)` and `config.load_settings(path)` (existing 2nd-brain code, unchanged).
- Produces: a running MCP server, `receipts_status_server.py`, exposing tool `receipts_status`. Task 4 registers this in `~/.claude.json`.

No new automated test here — spec explicitly says no integration test is needed for this thin wrapper (the only logic is in `summary.py`, already covered). This task ends with a manual smoke test instead.

- [ ] **Step 1: Create the venv and install the `mcp` SDK**

```bash
cd /Users/gardinerwelch/Documents/_Projects/2nd-brain
python3 -m venv mcp-server/.venv
mcp-server/.venv/bin/pip install --upgrade pip
```

- [ ] **Step 2: Write `requirements.txt` and install it**

Create `/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/requirements.txt`:

```
mcp>=1.0.0,<2
```

(Corrected during implementation, 2026-09-24: an unpinned `mcp>=1.0.0` installs 2.2.0, which renamed `FastMCP` to `MCPServer` and removed `mcp.server.fastmcp` entirely — a hard import failure, not the milder decorator-callability question this plan flagged as the real unknown. Pinning `<2` installs 1.30.0, which matches this task's server code exactly with zero changes needed.)

```bash
cd /Users/gardinerwelch/Documents/_Projects/2nd-brain
mcp-server/.venv/bin/pip install -r mcp-server/requirements.txt
```

- [ ] **Step 3: Add the venv to `.gitignore`**

Append this line to `/Users/gardinerwelch/Documents/_Projects/2nd-brain/.gitignore`:

```
mcp-server/.venv/
```

- [ ] **Step 4: Write the server**

Create `/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/receipts_status_server.py`:

```python
"""MCP server exposing a single read-only tool, receipts_status. Wraps
2nd-brain's receipts-ingest daemon status.json — see
docs/superpowers/specs/2026-09-22-receipts-status-mcp-server-design.md
in the jarvis repo for the full design and its known cross-host caveat."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))  # makes `receipts_ingest` importable, same convention run.py itself uses

from receipts_ingest import config  # noqa: E402
from summary import read_status_result  # noqa: E402

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("receipts-status")


# CORRECTION, 2026-09-24: this plan originally specified `receipts_status()`
# (no verb prefix) as the tool name. Live testing against the real JARVIS
# bridge showed that's wrong — bridge/server.mjs's decideTool() auto-approves
# read-only tools by matching a recognized verb prefix (get/list/read/search/
# etc.) in the tool name, and a noun-only name silently falls through to
# requiring ALLOW_WRITES, denying an actually-read-only tool. The tool was
# renamed to `get_receipts_status()` post-merge (2nd-brain commit 4321ebd).
# Any future MCP tool built for JARVIS needs a READ_VERB-matching name from
# the start — see the spec's Components section for the exact regex.
@mcp.tool()
def get_receipts_status() -> dict:
    """Report the status of the last receipts-ingest pipeline run: whether
    it succeeded, counts (scanned/new/filed/errors), and a spoken-friendly
    summary. Read-only — never triggers a pipeline run."""
    settings_path = config.resolve_settings_path(REPO_ROOT)
    settings = config.load_settings(settings_path)
    status_dir = Path(settings["status_dir"])
    return read_status_result(status_dir / "status.json")


if __name__ == "__main__":
    mcp.run()
```

- [ ] **Step 5: Manual smoke test — call the tool function directly**

```bash
cd /Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server
.venv/bin/python3 -c "
import receipts_status_server as s
import json
print(json.dumps(s.receipts_status(), indent=2))
"
```

(`mcp-server` has a hyphen so it can't be imported as a package by that name — running from inside the directory with a plain module import is the correct approach, not a fallback.)

Expected: prints real JSON with a non-empty `summary` string reflecting the actual current `status.json` on disk (e.g. mentioning "validation mode" and today's counts) — not an exception, not a placeholder message (unless `status.json` genuinely doesn't exist on this machine, in which case "no receipts status recorded yet" is correct).

Note: this assumes FastMCP's `@mcp.tool()` decorator returns the original function unchanged (a common pattern, but not verified against the installed `mcp` version here). If calling `s.receipts_status()` directly raises a `TypeError` about a `Tool` object not being callable, inspect `dir(s.mcp)` for how to reach the registered function directly (e.g. via its tool manager) and adjust this one-liner accordingly — the underlying `read_status_result` call itself (already covered by Task 2's tests) isn't in question, only how to invoke the decorated wrapper manually.

- [ ] **Step 6: Commit**

```bash
cd /Users/gardinerwelch/Documents/_Projects/2nd-brain
git add mcp-server/receipts_status_server.py mcp-server/requirements.txt .gitignore
git commit -m "Add receipts-status MCP server wrapper"
```

---

### Task 4: Register with JARVIS + end-to-end smoke test

**Files:**
- Modify: `/Users/gardinerwelch/.claude.json` (outside any git repo — this is a global Claude Code config file, not part of `2nd-brain` or `jarvis`)

**Interfaces:**
- Consumes: the absolute paths from Task 3 (`mcp-server/.venv/bin/python3`, `mcp-server/receipts_status_server.py`).

This file also holds credentials/tokens for your other MCP servers — do not print its full contents to the terminal at any point in this task. The script below reads and rewrites it in-process without ever echoing its contents; it only prints the list of server *names* afterward for confirmation, never values.

- [ ] **Step 1: Register the server without exposing existing secrets**

```bash
python3 - <<'PYEOF'
import json

path = "/Users/gardinerwelch/.claude.json"
with open(path) as f:
    data = json.load(f)

assert "mcpServers" in data, (
    "expected a top-level 'mcpServers' key — if this assertion fails, "
    "stop here and inspect the file's structure without printing its "
    "contents (e.g. `python3 -c \"import json;print(list(json.load(open(path)).keys()))\"`), "
    "then adjust this script to match the real key name before re-running."
)

data["mcpServers"]["receipts-status"] = {
    "command": "/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/.venv/bin/python3",
    "args": ["/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/receipts_status_server.py"],
}

with open(path, "w") as f:
    json.dump(data, f, indent=2)

print("Registered. mcpServers now:", sorted(data["mcpServers"].keys()))
PYEOF
```

Expected output: a list of server names that includes `receipts-status` alongside the existing ones (`sequential-thinking`, `apify`, `higgsfield`, `open-design`, `palmier-pro`, etc.) — no secret values printed.

- [ ] **Step 2: Restart JARVIS's bridge if it's currently running**

If the bridge is running (`npm start` / `npm run bridge:writes` from the `jarvis` repo), stop it and start it again so it re-reads `~/.claude.json` and picks up the new server. If it isn't running, no action needed — it'll pick it up on next start.

- [ ] **Step 3: End-to-end smoke test through JARVIS itself**

With the bridge running and the JARVIS frontend open (`http://localhost:5173`), ask it out loud or via the type-to-JARVIS text bar: *"What's the status of my receipts pipeline?"* Confirm the response reflects real data (matches what Task 3's manual smoke test printed) — not a generic "I don't have a tool for that" response, and not an error.

This step is manual (it needs a live voice/text interaction with the running app) — there's no automated test for it in this plan. If it doesn't work, check the bridge's own startup log for an MCP connection error on `receipts-status` before assuming the server code is wrong.

- [ ] **Step 4: Commit** (only if Step 1's script or any other file changed inside a git repo — `~/.claude.json` itself is not tracked in either repo, so there is likely nothing to commit here; skip if `git status` in both `2nd-brain` and `jarvis` shows nothing new)

```bash
cd /Users/gardinerwelch/Documents/_Projects/2nd-brain && git status --short
cd /Users/gardinerwelch/Documents/_Claude/Tools/jarvis && git status --short
```

If either shows unexpected changes, investigate before committing — this task shouldn't have touched either repo's tracked files.
