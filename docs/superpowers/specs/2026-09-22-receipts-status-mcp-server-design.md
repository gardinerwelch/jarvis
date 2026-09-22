# Receipts Status MCP Server — Design

**Status:** LOCKED (fresh-eyes review found 2 design-changing issues 2026-09-22, fixed below; user approved building on laptop now, migrating to the incoming Mac Mini in ~2-3 weeks per the migration note added under Registration)
**Date:** 2026-09-22
**Repo this builds in:** `2nd-brain` (not `jarvis`) — this doc lives in `jarvis` because it records a JARVIS-wide integration pattern, but the code it describes lives alongside the pipeline it reads.

## Problem

JARVIS (the voice assistant) has no way to answer questions about your own tools and pipelines — e.g. "did today's receipts file cleanly?" This is the first of a planned series of narrow, read-only MCP servers wiring JARVIS into existing repos (2nd-brain, file-management, gabba-command-center), per the 2026-09-22 scoping conversation. Decision made that session: **MCP-server-per-tool**, not `JARVIS_WORKSPACE` mode — narrow, specific, read-only-first tools, no voice-triggered writes. This spec covers the first instance: 2nd-brain's receipts pipeline.

## Why this is the right first target

The receipts ingest daemon (`2nd-brain/scripts/receipts_ingest/run.py`) already writes a `status.json` after every scheduled run, to a Drive-synced `status_dir` (see `receipts-ingest-settings.json`). Confirmed live on disk:

```json
{
  "last_run": "2026-09-22T10:35:06Z",
  "ok": true,
  "counts": {"scanned": 12, "new": 12, "filed": 0, "errors": 0, ...},
  "results": [...],
  "dry_run": true,
  "candidates": [],
  "candidates_pass_error": null,
  "duration_s": 6.63,
  "disk_free_gb": 15.71
}
```

This means the MCP tool never has to execute the pipeline, touch its SQLite ledger, or call Gmail — it just reads a file the daemon already produces on its own schedule. Zero new risk surface.

**Where the daemon actually runs (correction from initial draft):** the production daemon does not run on this laptop. `launchd/com.gabba.receipts-ingest.gabba-bu-server.plist` runs it on Gabba-BU-Server via `automation-host`'s `run-lane.sh` wrapper, with `RECEIPTS_SETTINGS` pointing at an untracked, host-specific settings file on that machine — never `run.py` directly, never this repo's tracked `config/receipts-ingest-settings.json`. The MCP server (below) runs on the laptop and resolves the laptop's own tracked settings file. These are two different settings files on two different hosts. They agree on `status_dir` today only because that value is the deliberate Drive-synced hand-off point between the two — not because of any shared code path. The MCP server's freshness is therefore only as good as Drive sync; see Error handling below for the staleness guard this requires.

## Architecture

A new MCP server, `receipts-status`, living inside the `2nd-brain` repo at `mcp-server/receipts_status_server.py` — Python, stdio transport, using the `mcp` SDK. Python matches 2nd-brain's existing scripts (all Python), so it shares that repo's lifecycle rather than being bundled into `jarvis`. Registered as one new entry in `~/.claude.json`; JARVIS's bridge picks it up automatically, the same way it already reaches `sequential-thinking`, `apify`, `higgsfield`, `open-design`.

**Correction from initial draft — no venv exists yet.** 2nd-brain has no `venv/`, `requirements.txt`, or `pyproject.toml` today; its scripts run against system Python with whatever's already installed globally. `mcp` (the Python SDK) is not installed anywhere on this machine. This build creates a dedicated virtualenv at `mcp-server/.venv`, installs `mcp` into it, and `~/.claude.json`'s `command` points at that venv's absolute interpreter path (`mcp-server/.venv/bin/python3`), not a bare `python3` — a bare `python3` resolves against the MCP client's own `PATH`, which has no guarantee of matching whatever interpreter `pip install mcp` targeted.

This establishes the template for the next two servers (file-management, gabba-command-center): each source repo owns its own tiny `mcp-server/`, reading whatever status artifact (or read-only query, for gabba-command-center's Notion backend) it already has.

## Components

- **`mcp-server/receipts_status_server.py`** — the server. One tool: `receipts_status()`, no arguments for v1.
- **Import path** — `scripts/` has no `__init__.py` and isn't installed as a package; `run.py` and every test file make `receipts_ingest` importable by doing `sys.path.insert(0, str(<repo_root>/"scripts"))` before importing it. `receipts_status_server.py` must do the same (`sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))`) before `from receipts_ingest import config`.
- **Path resolution** — calls `receipts_ingest.config.resolve_settings_path()` and `load_settings()` to find `status_dir`. Confirmed side-effect-free (read-only) by inspection. As corrected above, this resolves *this machine's* tracked settings file, not the production daemon's — it's used here simply because it's the existing, correct way to find `status_dir` on the laptop, not because it tracks the daemon's own resolution.
- **Summary builder** — a small pure function, `build_summary(status: dict) -> str`, kept separate from the MCP tool wrapper so it's unit-testable without any MCP scaffolding. Produces a short spoken-friendly sentence, e.g.:
  > "Last run 20 minutes ago, still in validation mode. 12 scanned, 12 new, 0 filed live, 0 errors. Top vendors: Uber, Temuemail."
  Vendor tally reads `result.get("vendor")` and skips entries without one — `run_once()` (run.py) appends `{"msg_id", "errors"}` with no `vendor` key for per-message exceptions, so a bare `result["vendor"]` would `KeyError` on any run that had an error.
- **Staleness guard** — because `status_dir` freshness depends on Drive sync completing on a different machine (see above), `build_summary()` compares `last_run` to the current time and prefixes the summary with a plain warning (e.g. "heads up, this hasn't updated in over a day, might be a stale sync, not a clean pipeline") when it's older than 24 hours, rather than reporting stale data as if it were current.
- **Tool return shape** — `{"summary": str, "ok": bool | None, "last_run": str | None, "dry_run": bool | None, "counts": dict}`. JARVIS speaks `summary`; the structured fields are there if a follow-up question needs specifics. `ok`/`last_run`/`dry_run` are `None` and `counts` is `{}` on the missing-file and malformed-JSON branches (see Error handling).

## Data flow

JARVIS → MCP tool call (`receipts_status`) → read `status_dir/status.json` from disk → `build_summary()` → return. No subprocess, no DB connection, no network call. The launchd daemon that writes `status.json` is completely decoupled from this — it keeps running on its own schedule regardless of whether JARVIS ever calls the tool.

## Error handling

- `status.json` missing (fresh install, daemon never run) → return `{"summary": "no receipts status recorded yet", "ok": None, "last_run": None, "dry_run": None, "counts": {}}`, not an exception.
- `status.json` unreadable / malformed JSON → same fallback shape, never a stack trace surfaced as spoken output.
- `status["ok"] is False` → `build_summary()` leads with the daemon's own `error` field instead of the counts.
- `last_run` older than 24 hours → staleness warning prefix (see Components), even when `ok` is `True`.

## Testing

**Correction from initial draft — repo convention is `unittest.TestCase`, not bare pytest.** Every existing test file (e.g. `tests/test_receipts_ledger.py`) is a `unittest.TestCase` subclass with a manual `sys.path.insert(0, str(<repo_root>/"scripts"))` at the top of the file, and there's no `conftest.py` doing that for them. `tests/test_receipts_status_server.py` follows the same shape, adding a second `sys.path.insert` for `mcp-server/` so it can import `build_summary`. Covers `build_summary()` against fixture `status.json` dicts:
1. Normal success (the shape above, including the extra `candidates`/`candidates_pass_error` keys — must be ignored gracefully, not required)
2. `ok: false` with an `error` field
3. A result entry with no `vendor` key (the per-message-exception shape from `run.py`) — must not raise
4. `last_run` more than 24 hours old — staleness prefix present
5. Missing file
6. Malformed JSON

No integration test needed — there's no live call to mock; the only I/O is a local file read, exercised via the fixtures.

## Safety

Strictly read-only: no writes, no shell-out, no ledger DB access, no Gmail call. Matches the 2026-09-22 decision ("MCP tools for all three, read-only-friendly") with zero exposure — there is no code path in this server that can mutate anything.

## Registration

New entry added to `~/.claude.json`'s MCP server list (alongside `sequential-thinking`, `apify`, `higgsfield`, `open-design`, `palmier-pro`):

```json
"receipts-status": {
  "command": "/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/.venv/bin/python3",
  "args": ["/Users/gardinerwelch/Documents/_Projects/2nd-brain/mcp-server/receipts_status_server.py"]
}
```

## Future migration to the new Mac Mini (~2-3 weeks out)

Built on the laptop now; expected to move once the new Mini arrives. Confirmed low-effort — no code in this spec changes:

1. `git pull` on the Mini's existing `2nd-brain` checkout (`/Users/mediaserver/automation/repos/2nd-brain` per the launchd plist) picks up `mcp-server/` unchanged.
2. Recreate the venv there (`python3 -m venv mcp-server/.venv && mcp-server/.venv/bin/pip install mcp`) — venvs aren't portable across machines.
3. Register the server in whichever host's `~/.claude.json` is running JARVIS's bridge at that point, pointing at the Mini's absolute venv interpreter path.
4. Optional tightening (not required for correctness — the staleness guard already covers the gap): add a `RECEIPTS_SETTINGS` env var to the Mini-side registration, matching whatever host-specific settings path `automation-host`'s `run-lane.sh` uses there, so the MCP server reads the exact same settings file as the daemon instead of this repo's tracked default.

Whether JARVIS's bridge itself moves to the Mini (vs. staying on the laptop and pointing `VITE_BRIDGE_URL` at a Mini-hosted bridge) is a separate, larger decision, out of scope here — this server's registration works identically either way, it's just a config edit on whichever host ends up running the bridge.

## Out of scope for this spec

- Deferred-items ledger, inbox-ocr, drive-filing status (other 2nd-brain pipelines) — same pattern, separate future tool, not bundled in here.
- Any write capability (re-running the daemon, forcing a filing pass) — explicitly excluded per the read-only-first decision.
- file-management and gabba-command-center servers — separate specs, once this one is built and proven.
