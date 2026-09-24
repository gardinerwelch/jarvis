# Storage Status MCP Server — Design

**Status:** DRAFT r2 — fresh-eyes review (2026-09-24, 19 findings) ruled and folded in; pending Gardiner's approval
**Date:** 2026-09-24
**Repo this builds in:** `file-management` (not `jarvis`). This doc lives in `jarvis` alongside the receipts-status spec because it records the second instance of the JARVIS MCP-server-per-repo pattern; the code lives next to the data it reads.
**Predecessor:** `2026-09-22-receipts-status-mcp-server-design.md` (same pattern, first instance)

## Problem

JARVIS can answer "how are my receipts?" but not "how's my storage?" The answer is spread across file-management: volume capacity, backup mirrors, migration lanes, running/failed operations and drive health. Gardiner wants all four areas: a short spoken headline plus a HUD panel showing the status of each.

## Why not just read status.json

file-management already writes `status.json` (via `summarize.py`), but it is **12 days stale** (`generated: 2026-09-12`). The job that would refresh it every 5 minutes, `com.gabba.fleet-sweep.plist`, was deliberately left unloaded: `docs/specs/2026-08-12-digitize-station-independence-inventory.md` (~line 154) records that laptop + Mini both running it would regenerate `status.json` on independent timers, and which machine is the hub is parked until the new Mac Mini arrives.

Running `summarize.py` from the tool is also ruled out: `main()` calls `ops_sweep()` first, which appends to `ops-log.jsonl`, deletes/quarantines heartbeat files and relays files between ops zones. A tool the bridge auto-approves as read-only must not do any of that.

**Decision:** the server builds a **live snapshot in memory** on every call from `summarize.py`'s and `ledger.py`'s read-only functions, plus `os.statvfs`. It never calls `summarize.main()` or `ops_sweep()`, and writes nothing. The two sweep rules that *resolve* stale alarms (supersession, abandonment) are re-applied in memory at read time — see Ops — so the laptop doesn't announce permanently-unresolvable alarms. The hub/sweep question stays parked.

## Architecture

New directory `file-management/mcp-server/`, same layout as `2nd-brain/mcp-server/`:

| File | Responsibility |
|---|---|
| `snapshot.py` | Collects raw data (table below). Each collector runs in its own daemon thread under one shared deadline; a failure or timeout marks only that source unavailable. Returns a plain dict. No MCP import. |
| `summary.py` | Pure: `build_summary(snapshot: dict, now: datetime) -> dict` → `{ok, headline, generated_at, sections}`. No I/O, no MCP import. Holds thresholds and `EXPECTED_MOUNTS` as module constants. |
| `storage_status_server.py` | Thin FastMCP wrapper, stdio transport, one tool: `async def get_storage_status()`, no arguments. Imports `snapshot`/`summary` **lazily inside the tool** under `try`, runs `collect()` via `await anyio.to_thread.run_sync(...)` (FastMCP runs sync tools on the event loop, which would block pings for the ~5 s worst case), and wraps the collection in `contextlib.redirect_stdout(sys.stderr)` — stdout is the JSON-RPC transport, and several file-management helpers `print()` (don't reassign `sys.stdout` globally; the stdio transport captures it at startup). Never raises to the client. |
| `requirements.txt` | `mcp>=1.0.0,<2` (unpinned installs pull a breaking 2.x API — same pin as 2nd-brain; 1.30.0 on Python 3.14 matches the 2nd-brain venv). |
| `.venv/` | Dedicated virtualenv, gitignored. `~/.claude.json`'s `command` points at its absolute `bin/python3`; `env` sets `PYTHONDONTWRITEBYTECODE=1`. |

**Tool name:** `get_storage_status` — passes `jarvis/bridge/server.mjs`'s `decideTool()` (`READ_VERB` `^get`, no `EFFECTFUL_VERB` hit). A noun-only name is silently denied (receipts-status lesson, 2026-09-24).
**Server key in `~/.claude.json`:** `storage-status`. Must not contain `__` (`mcpServerOf` splits tool names on it, `server.mjs:~365`). The bridge reads `~/.claude.json` once at start, so registration requires a bridge restart.

**Import path and bytecode:** file-management's modules are top-level scripts (no package). `snapshot.py` sets `sys.dont_write_bytecode = True`, then `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`, then `import ledger, summarize`. Import chain verified side-effect-free by review (`summarize` → `ledger`, `ops_heartbeat`, `rsync_parse`, `station_scan`, `tree_index` → `hash_index`: constant/Path construction only; no sqlite open, no signal handlers).

### Collectors (snapshot.py)

| Source key | How | Freshness | Can hang? |
|---|---|---|---|
| `volumes` | `os.statvfs(p)` for each `p` in `Path("/Volumes").iterdir()` (skip non-dirs / non-mount-points via `os.path.ismount`). **Not** `summarize.volume_fullness()`: its `df -H` + `line.split()` parse loses any mount with a space in its name (`/Volumes/Apple Media-Syno` → `mount: "Media-Syno"`), and an abandoned `df` child lingers per call on a hung mount. | live | yes — statvfs on a dropped SMB mount |
| `ledger_rows` | `ledger.read_rows(ledger.LEDGER)` → `summarize.lane_summary()` + per-lane raw status counts | live (local) | no |
| `mirrors` | `ledger.read_rows(ledger.MIRRORS)` | hand-maintained CSV | no |
| `drives` | `ledger.read_rows(ledger.DRIVES)` | updated at check-in | no |
| `active_ops` | `summarize.active_operations([summarize.NAS_OPS_ZONE, summarize.LOCAL_OPS_FALLBACK])` (legacy=True, same call as `fleet_panel.py:~225`) | live | yes — globs `/Volumes/_Inbox/_fleet-drops/…` over SMB |
| `recent_ops` | `summarize.recent_operations(summarize.OPS_LOG_PATH, n=5)` — window of 5 **plus every pinned unresolved failure** (never slice the result) | as of last sweep (ops-log mtime; 2026-09-11 today) | no |

Each collector records `{value, ok, error, as_of}`. `as_of` = call time for live sources; source file mtime for `mirrors`, `drives`, `recent_ops`.

**Timeouts:** start all collector threads (`threading.Thread(daemon=True)`), set `deadline = time.monotonic() + 5`, then `t.join(max(0, deadline - time.monotonic()))` for each — one shared 5 s budget, not 5 s per collector. A thread still alive at the deadline is abandoned (daemon; no locks or shared mutable state in any collector — verified) and its source is `ok: false, error: "timed out"`. No `ThreadPoolExecutor` (its shutdown waits on hung workers). No upstream change to file-management's existing modules.

**CSV failure granularity:** `ledger.read_rows` raises `ValueError` for the whole file on one malformed row (`ledger.py:31-50`). That makes the dependent section/sub-list `unavailable` with the error text — same posture as `fleet_panel.py:213-216`. Note `_legacy_operations` also reads `drives.csv`, so a corrupt `drives.csv` degrades both `drives` and `active_ops`.

## Output shape

```
{
  "ok": bool,                 # false only when every section is unavailable
  "headline": str,            # spoken; ≤ 3 clauses; no paths, IDs or slugs
  "generated_at": iso str,
  "sections": {
    "capacity": { status, as_of, source: "live",   reason?, items: [{mount, name, used_bytes, size_bytes, pct, level}], missing: [name] },
    "backups":  { status, as_of, source: "manual", reason?, items: [{dest, target, method, last_verified, age_days, level}] },
    "lanes":    { status, as_of, source: "live",   reason?, items: [{lane, done, total, bytes_done, bytes_planned, open}] },
    "ops":      { status, as_of, source: "live",   reason?, failed: [...], abandoned: [...], stalled: [...], running: [...], recent: [...], recent_as_of, drive_warnings: [...] }
  }
}
```

`status` per section ∈ `ok | warn | critical | unavailable`. Any section may carry an optional `reason` (plain English); `unavailable` always does.

### Section rules

- **capacity** — every mounted `/Volumes/*`; `pct = round(100 * (1 - f_bavail / f_blocks))` (what Finder/df report as used%). `level`: `critical` ≥ 95, `warn` ≥ 85, else `ok`. `EXPECTED_MOUNTS = ["Projects-Syno", "_Inbox"]` (the NAS share and the fleet drop-zone share): any not mounted → listed in `missing`, section at least `warn`. Section status = worst of items and missing.
- **backups** — from `mirrors.csv` (`dest, mirror_target, method, last_verified_sync, status`). `age_days` = today − `last_verified_sync`. `level`: `warn` when `age_days` > 30, `status` ≠ `active`, or the date is unparseable. `source: "manual"` — a hand-recorded verification, not live Hyper Backup/Snapshot Replication telemetry; spoken as "last checked", never "last synced". Empty CSV → `ok`, `reason: "no mirrors configured"`.
- **lanes** — from `lane_summary()` plus raw status counts. `done` = `verified + cleared` (same definition as `STATUS.md`); `bytes_done` = `lane_summary`'s `bytes_verified`. **`open` = the lane still has any `planned` row.** Lanes whose work finished in states that never reach `verified` (`executed`, `reviewed-no-action` — e.g. `small-folders-triage`, `kids-corner-cull`) are not open. Section `warn` if any lane is open, else `ok`; never `critical` (open lanes are expected work).
- **ops** — from `active_ops` (heartbeats) + `recent_ops` (log), with the sweep's resolution rules applied **in memory only**:
  Candidate pool for both rules = `active_ops` entries + `recent_ops` entries (the in-memory analogue of the sweep's `merged_log` + valid heartbeats). Mirror `ops_sweep` exactly (`summarize.py:~841-900`), without any of its writes:
  - `failed` (sweep step 5): only `state == "failed"` entries are reported (the sweep also resolves `paused`, but paused is a first-class terminal state, not a failure, per `recent_operations`' docstring). A failed entry is **superseded** — hence dropped — when any *other* pool entry satisfies `summarize._same_run_family(entry, other)` and has a later `finished_at or logged_at` than the entry's (any terminal state, as the sweep does; a newer failure supersedes an older one). Entries already `summarize.is_resolved()` are dropped. What remains → `critical`.
  - `abandoned` (sweep step 6): `state == "running"` entries (excluding the synthetic unreadable-heartbeat entry, which the sweep treats in step 7, not 6) where `last_beat` is unparseable/missing, **or** older than `summarize.ABANDON_S` (24 h), **or** `summarize._find_newer_match(entry, pool, entry["op_id"])` returns a newer same-family run. → `warn`. Spoken "an old operation never finished cleanly", never "stalled".
  - `stalled`: `state == "running"`, `stale: True`, not abandoned — including the synthetic unreadable-heartbeat entry (`running`, `stale: True`, label "unreadable heartbeat — inspect"), the repo's designed visible alarm. → `warn`.
  - `running`: `state == "running"`, not stale. Informational.
  - Terminal `complete` / `paused` heartbeats awaiting a sweep are ignored (terminal set = `ops_heartbeat._TERMINAL_STATES`).
  - `recent`: `recent_ops` as returned (5 + pins). `recent_as_of` = ops-log mtime; the panel always shows it dimmed (it's weeks old until a sweep runs — intended, so it's never mistaken for live).
  - `drive_warnings`: `drives.csv` rows with non-empty `health` other than `ok`: `{label, health, note}` where `note` = `notes` up to the first `;`, ` -- ` or `.`.
  - Section status = worst of the above.

### Headline

Up to three clauses, worst-first, with **the last slot reserved** for a missing-mount / unavailable-section clause whenever one exists (so an unreachable NAS is always spoken). Priority and wording:

1. Critical capacity — "Projects-Syno is at 96 percent."
2. Missing expected mount — "Projects-Syno isn't mounted." (reserved slot)
3. Failed operation — by `op_type` + count + relative day, never `subject_label` (new-style labels are slugged paths): "Two presort runs failed, most recently on the 20th."
4. Abandoned operation — "An old presort run never finished cleanly." (count if > 1)
5. Stalled operation — "A running operation has stopped reporting."
6. Drive health warning — "The LaCie has a health warning." (count if > 1)
7. Overdue backup check — uses the **max** `age_days`: "Backup checks are 50 days old." Non-`active` or unparseable mirror → "A backup mirror needs checking."
8. Warn-level capacity — "Projects-Syno is at 90 percent."
9. Open lanes — "Two migration lanes are still open."
10. Unavailable section (reserved slot) — "Couldn't read live operations." / "Couldn't read disk capacity."

All `ok` → "Storage is healthy." All sections unavailable → `ok: false`, "I couldn't read any storage status right now."

**Expected output against today's live data** (per review of the live ops zone and `ledger.csv`, 2026-09-24 — the build's first task re-runs `collect()` read-only and records the actual result here before tests are written): ops `critical` if any of the 3 `failed` presort heartbeats (09-17 ×2, 09-20) is not superseded by a completed retry — the review found two retried minutes later, so expect one remaining failure; 5 `running` `presort_plan` heartbeats 6–12 days old → abandoned; LaCie drive warning; backups 50 days; Projects-Syno 90%; two open lanes (letter-normalize, PRESORT_Projects). Likely headline: "A presort run failed on the 20th. Five old presort runs never finished cleanly. The LaCie has a health warning."

### Panel

No new UI code. JARVIS's in-process `display` tool (`jarvis/bridge/panels.mjs`) lets the model author a panel from a fixed class set, and its own rule caps a panel at roughly 6 rows / 40 words (`panels.mjs:~93`). So the tool description specifies a **compact panel**: one `.hud-row` per section (capacity, backups, lanes, ops) with the section name as `.hud-label`, the level as `.hud-tag` (`.hud-hot` when warn/critical), and one `.hud-sub` line with the single worst detail; a `.hud-dim` "as of" note on any section older than a day. The full breakdown (a `.hud-bar` per volume and per lane, a row per mirror/op) goes in a blade (`kind: markup`, `panels.mjs:~239`) **only when Gardiner asks for detail**. The spoken reply is the `headline`.

## Error handling

- Collector failure or timeout → that source unavailable; only its section/sub-list degrades. `ops` is `unavailable` only if `active_ops`, `recent_ops` and `drives` all fail.
- Malformed individual values (bad date, missing key in a heartbeat) → item `level: warn` or skipped, never raise. Malformed CSV → section unavailable with the `ValueError` text (see Collectors).
- `snapshot`/`summary` import failure (repo moved, upstream syntax error) → caught by the lazy import inside the tool → `{ok: false, headline: "The storage status tool couldn't load file-management's code.", sections: {}}`. The server process itself still starts and registers the tool.
- Any other exception → same `ok: false` shape, generic headline; exception text to stderr only.

## Testing

In `file-management/tests/`, `unittest`, 2-space indent, matching the existing suite. Test files do `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))` to import `summary`/`snapshot` (same approach as 2nd-brain's `tests/test_receipts_status_summary.py:~8`).

1. **`test_storage_status_summary.py`** — pure `build_summary` from fixture snapshots: threshold edges (84/85/94/95 %, 30/31 days), missing expected mount, lane `open` rule (planned vs executed/reviewed-no-action), supersession (failed + later completed same family → not failed), abandonment (running > 24 h → abandoned; synthetic unreadable → stalled), pinned failure beyond the window still critical, headline order + three-clause cap + reserved last slot, max-age backup phrasing, no slug/path in any headline, all-healthy, all-unavailable, partially-unavailable.
2. **`test_storage_status_snapshot.py`** — each collector patched to raise → only its source degrades; two collectors patched to `time.sleep(10)` → both marked timed out and `collect()` returns in < 7 s (proves the shared deadline); malformed CSV → section unavailable.
3. **No-write tripwire** — patch to raise: `summarize.ops_sweep`, `summarize.main`, `summarize.append_ops_log`, `summarize._safe_delete`, `summarize._quarantine_rename_file`, `summarize._atomic_write_relay`, `summarize._acquire_ops_lock`, `ledger.write_rows`, `ops_heartbeat.pick_zone`, `ops_heartbeat.write_heartbeat`, `os.replace`, `Path.unlink`, `Path.mkdir`, `Path.write_text`; run the real `collect()` + `build_summary()`; assert success. Also snapshot path + mtime + size of every file under the repo (excluding only `.git/`), plus `/Volumes/_Inbox/_fleet-drops/_ops` and `…/_heartbeats` when mounted, before and after; assert identical.
4. **Full existing suite** stays green (`python3 -m unittest discover tests`).
5. **Live end-to-end through JARVIS** (required before "done"): register `storage-status` in `~/.claude.json` (stop-and-confirm with Gardiner first — full read-modify-write of Claude Code's global state file), restart the bridge, ask "how's my storage?", confirm the spoken headline and the compact four-row panel, then ask for detail and confirm the blade.

## Out of scope (log as deferred items in file-management's ledger)

- Loading `com.gabba.fleet-sweep` / choosing the hub — Mac Mini session.
- Any write or action tool (purge, rescan, check-in, resolving ops).
- Live mirror telemetry from Synology Hyper Backup / Snapshot Replication instead of hand-kept `mirrors.csv`.
- Per-drive SMART reads at call time.
- Fixing `summarize.volume_fullness()`'s space-in-mount-name parse bug for `STATUS.md`/`fleet_panel` (found by this review; this server sidesteps it via `statvfs`).

## Review log

- **2026-09-24 r1 → r2, fresh-eyes review (Fable subagent, spec + repos only), 19 findings, all accepted.** Blocker: expected output was derived from stale `status.json`, not live collectors. Majors: permanent unresolvable ops alarms (added in-memory supersession/abandonment), `recent[:5]` dropped pinned failures, `df` parse lost space-named mounts (→ `statvfs`), unmounted NAS read as healthy (→ `EXPECTED_MOUNTS` + reserved headline slot), per-collector joins were additive (→ shared deadline), panel exceeded the display tool's row cap (→ compact panel + detail blade), lane `open` definition mismatched real data (ruled: open = has `planned` rows), no-write test too weak (→ tripwire). Minors: stdout guard, bytecode writes, lazy import, test import path, headline phrasings, CSV failure granularity, `reason` contradiction, note splitting, `bytes_done` definition, async tool, server-key `__` constraint.
