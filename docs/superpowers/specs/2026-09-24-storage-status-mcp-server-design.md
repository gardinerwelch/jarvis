# Storage Status MCP Server — Design

**Status:** DRAFT — pending fresh-eyes review + Gardiner's approval
**Date:** 2026-09-24
**Repo this builds in:** `file-management` (not `jarvis`). This doc lives in `jarvis` alongside the receipts-status spec because it records the second instance of the JARVIS MCP-server-per-repo pattern; the code lives next to the data it reads.
**Predecessor:** `2026-09-22-receipts-status-mcp-server-design.md` (same pattern, first instance)

## Problem

JARVIS can answer "how are my receipts?" but not "how's my storage?" The answer to that second question is spread across file-management: volume capacity, backup mirrors, migration lanes, running/stalled operations and drive health. Gardiner wants all four areas, spoken as a short headline and shown as a HUD panel with the status of each.

## Why not just read status.json

The receipts-status pattern reads a status file the pipeline already writes. file-management has one too (`status.json`, written by `summarize.py`), but it is currently **12 days stale** (`generated: 2026-09-12`): the job that would refresh it every 5 minutes, `com.gabba.fleet-sweep.plist`, was deliberately left unloaded. `docs/specs/2026-08-12-digitize-station-independence-inventory.md` records why: if the laptop and the Mini both run it, they regenerate `status.json` on independent timers ("churn hazard"), and which machine is the hub is parked until the new Mac Mini arrives.

Running `summarize.py` from the tool is also ruled out: `main()` calls `ops_sweep()` first, which appends to `ops-log.jsonl`, deletes/quarantines heartbeat files and relays files between ops zones. A tool the bridge auto-approves as read-only must not do any of that.

**Decision:** the server builds a **live snapshot in memory** on every call from `summarize.py`'s and `ledger.py`'s read-only functions. It never calls `summarize.main()` or `ops_sweep()`, never writes `status.json`/`STATUS.md`, never writes anything. The hub/sweep question stays parked.

## Architecture

New directory `file-management/mcp-server/`, same layout as `2nd-brain/mcp-server/`:

| File | Responsibility |
|---|---|
| `snapshot.py` | Collects raw data by calling existing read functions (table below). Each collector runs in its own daemon thread with a timeout; a failure or timeout marks only that source unavailable. Returns a plain dict. No MCP import. |
| `summary.py` | Pure: `build_summary(snapshot: dict, now: datetime) -> dict` producing `{headline, generated_at, ok, sections}`. No I/O, no MCP import. Holds the thresholds as module constants. |
| `storage_status_server.py` | Thin MCP wrapper, stdio transport, one tool: `get_storage_status()`, no arguments. Calls `snapshot.collect()` then `summary.build_summary()`. Catches everything; never raises to the client. |
| `requirements.txt` | `mcp>=1.0.0,<2` (unpinned installs pull a breaking 2.x API — same pin as 2nd-brain). |
| `.venv/` | Dedicated virtualenv, gitignored. `~/.claude.json`'s `command` points at its absolute `bin/python3`. |

**Tool name:** `get_storage_status` — must start with a `READ_VERB` in `jarvis/bridge/server.mjs`'s `decideTool()`, or the bridge silently denies it (lesson from receipts-status, 2026-09-24).

**Import path:** file-management's modules are top-level scripts in the repo root (no package). `snapshot.py` does `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` before `import ledger, summarize`. Importing `summarize` has only path-constructing side effects at module level (`NAS_OPS_ZONE`, `LOCAL_OPS_FALLBACK`, `OPS_LOG_PATH` are `Path` objects; `ops_heartbeat._default_nas_zone()` just builds `/Volumes/_Inbox/_fleet-drops/_ops`). The review must confirm the transitive imports (`ops_heartbeat`, `rsync_parse`, `station_scan`, `tree_index` → `hash_index`) have no import-time writes.

### Collectors (snapshot.py)

| Source key | Call | Live? | Can hang? |
|---|---|---|---|
| `volumes` | `summarize.volume_fullness()` (`df -H`) | live | yes — `df` blocks on a dropped SMB mount |
| `ledger_rows` | `ledger.read_rows(ledger.LEDGER)` → `summarize.lane_summary()` | live (local file) | no |
| `mirrors` | `ledger.read_rows(ledger.MIRRORS)` | hand-maintained CSV | no |
| `drives` | `ledger.read_rows(ledger.DRIVES)` | updated at check-in | no |
| `active_ops` | `summarize.active_operations([summarize.NAS_OPS_ZONE, summarize.LOCAL_OPS_FALLBACK])` (legacy=True default, same call `fleet_panel.py` makes) | live | yes — globs `/Volumes/_Inbox/…` over SMB |
| `recent_ops` | `summarize.recent_operations(summarize.OPS_LOG_PATH)` | only as fresh as the last sweep | no (local file) |

Each collector records `{value, ok, error, as_of}`. `as_of` is the call time for live sources and the source file's mtime for `mirrors`, `drives` and `recent_ops` (`ops-log.jsonl`).

**Timeouts:** each collector runs in a `threading.Thread(daemon=True)`; the collector waits `join(timeout=5)`. A thread still alive after that is abandoned (daemon, so it can't keep the process alive) and the source is marked `ok: false, error: "timed out"`. Collectors run concurrently so the worst case for the whole call is ~5 s, not 5 s × 6. No `ThreadPoolExecutor` (its shutdown waits on hung workers). `summarize.volume_fullness()` itself is **not** modified — no upstream change.

## Output shape

```
{
  "ok": bool,                 # false only when every section is unavailable
  "headline": str,            # spoken; ≤ 3 clauses; no paths, IDs or raw numbers beyond percentages
  "generated_at": iso str,
  "sections": {
    "capacity": { status, as_of, source: "live",   items: [{mount, used, capacity, pct, level}] },
    "backups":  { status, as_of, source: "manual", items: [{dest, target, method, last_verified, age_days, level}] },
    "lanes":    { status, as_of, source: "live",   items: [{lane, done, total, bytes_done, bytes_planned, complete}] },
    "ops":      { status, as_of, source: "live",   active: [...], recent: [...], recent_as_of, drive_warnings: [...] }
  }
}
```

`status` per section ∈ `ok | warn | critical | unavailable`. `unavailable` sections carry `reason` (plain English) and no items.

### Section rules

- **capacity** — keep only mounts starting with `/Volumes/` (summarize's own filter is a substring match, so it also admits `/System/Volumes/*`, which must be dropped). `pct` parsed from `use_pct` (`"90%"` → 90). `level`: `critical` ≥ 95, `warn` ≥ 85, else `ok`. Section status = worst item level.
- **backups** — from `mirrors.csv` (`dest, mirror_target, method, last_verified_sync, status`). `age_days` = today − `last_verified_sync`. `level`: `warn` when `age_days` > 30, or when `status` ≠ `active`, or the date is unparseable. `source: "manual"`: this date is a hand-recorded verification, not live Hyper Backup/Snapshot Replication telemetry; JARVIS must phrase it as "last checked", never "last synced". Empty CSV → section status `ok` with a `reason: "no mirrors configured"`.
- **lanes** — from `lane_summary()`. `done` = `by_status.verified + by_status.cleared` (same definition as `STATUS.md`). `complete` = `done == total`. Section status `warn` if any lane is incomplete, else `ok`. (Open lanes are expected work, not a fault, so never `critical`.)
- **ops** —
  - `active`: entries from `active_operations()` with `state == "running"` (the terminal set is `complete | failed | paused`, `ops_heartbeat._TERMINAL_STATES`; `summarize._op_rank` uses the same `!= "running"` test). An entry with `stale: true` is **stalled** → `warn`. This includes `summarize._synthetic_unparseable_entry` (state `running`, `stale: True`, label "unreadable heartbeat — inspect") — intended: an unreadable heartbeat is meant to be a visible alarm.
  - `failed_unswept`: entries from `active_operations()` with `state == "failed"`. Because the sweep isn't running on this laptop (see "Why not just read status.json"), a failed op's terminal heartbeat can sit in the ops zone without ever reaching `ops-log.jsonl`, so `recent_operations()` alone would miss it. These count as unresolved failures → `critical`. Terminal `complete`/`paused` heartbeats awaiting a sweep are ignored.
  - `recent`: first 5 from `recent_operations()`; any `is_unresolved_failure(e)` → `critical`. `recent_as_of` = ops-log mtime, so the panel can show that recent ops are only as fresh as the last sweep.
  - `drive_warnings`: rows in `drives.csv` whose `health` is non-empty and not `ok` (today: the LaCie `warn`), with `label` and the first sentence of `notes`.
  - Section status = worst of the above.

### Headline

Built worst-first from at most three items, in this priority:

1. Critical capacity ("Projects-Syno is at 96 percent.")
2. Unresolved failed operation
3. Stalled active operation
4. Drive health warning ("The LaCie has a health warning.")
5. Overdue backup check ("Backup checks are 50 days old.")
6. Warn-level capacity
7. Open lanes ("Three migration lanes are still open.")

Unavailable sections add one clause at the end only if there's room ("Couldn't reach the NAS for live operations."). All `ok` → "Storage is healthy." All unavailable → `ok: false`, headline "I couldn't read any storage status right now."

**Expected output against today's real data:** capacity warn (Projects-Syno 90%); backups warn (all three mirrors last verified 2026-08-05, 50 days); lanes warn (letter-normalize, PRESORT_Projects, small-folders-triage open); drive warning (LaCie). Headline: "The LaCie has a health warning. Backup checks are 50 days old. Projects-Syno is at 90 percent."

### Panel

No new UI code. JARVIS's in-process `display` tool (`jarvis/bridge/panels.mjs`) already lets the model author a panel from a fixed class set. The tool description tells the model the result is built to be displayed: one block per section, `.hud-bar` (`--v`) per volume and per lane, tagged `.hud-row`s for backups and ops, `.hud-tag` carrying the level, `.hud-dim` "as of" line on any section whose `as_of` is over a day old. The spoken reply is the `headline`; the panel carries the detail.

## Error handling

- Every collector failure or timeout → that section `unavailable` with a plain reason; other sections unaffected. A section depending on two sources (ops: `active_ops` + `recent_ops` + `drives`) degrades per sub-list, and is `unavailable` only if all three fail.
- Malformed individual rows (bad date, non-numeric pct, missing key) are skipped or given `level: warn` — never raise.
- `import ledger, summarize` failing (repo moved, syntax error upstream) is caught in `storage_status_server.py` and returned as `{ok: false, headline: "The storage status tool couldn't load file-management's code.", sections: {}}`.
- The MCP tool never raises; any unexpected exception becomes the same `ok: false` shape with a generic headline, and the exception text goes to stderr only (never spoken).

## Testing

In `file-management/tests/`, `unittest`, 2-space indent, matching the existing 29-file suite.

1. **`test_storage_status_summary.py`** — pure `build_summary` from fixture snapshots: every threshold edge (84/85/94/95%, 30/31 days), `/System/Volumes` filtering, lane completion, headline ordering and three-item cap, all-healthy, all-unavailable, partially-unavailable, malformed rows.
2. **`test_storage_status_snapshot.py`** — each collector patched to raise → only its source degrades; a collector patched to `time.sleep(10)` → marked timed out and `collect()` returns in < 7 s.
3. **No-write guarantee** — run the real `collect()` + `build_summary()` against the real repo; record every file's path + mtime + size under the repo (excluding `__pycache__/` and `.git/`) before and after; assert identical. Also patch `summarize.ops_sweep` and `summarize.main` to raise if called.
4. **Full existing suite** stays green (`python3 -m unittest discover tests`).
5. **Live end-to-end through JARVIS** (required before "done", per the receipts-status lesson): register in `~/.claude.json` (stop-and-confirm with Gardiner — full read-modify-write of Claude Code's global state file), restart the bridge, ask "how's my storage?", confirm a spoken headline and a rendered four-section panel.

## Out of scope (log as deferred items in file-management's ledger)

- Loading `com.gabba.fleet-sweep` / choosing the hub — belongs to the Mac Mini session.
- Any write or action tool (purge, rescan, check-in).
- Live mirror telemetry from Synology Hyper Backup / Snapshot Replication instead of the hand-kept `mirrors.csv`.
- Per-drive SMART reads at call time.
