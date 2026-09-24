# Storage Status MCP Server — Design

**Status:** DRAFT r4 — three fresh-eyes reviews (2026-09-24: 19 + 13 + 12 findings) plus a targeted re-read (2 minors, fixed) ruled and folded in; reviewer verdict: ready to lock. Pending Gardiner's approval.
**Date:** 2026-09-24
**Repo this builds in:** `file-management` (not `jarvis`). This doc lives in `jarvis` alongside the receipts-status spec because it records the second instance of the JARVIS MCP-server-per-repo pattern; the code lives next to the data it reads.
**Predecessor:** `2026-09-22-receipts-status-mcp-server-design.md` (same pattern, first instance)

## Problem

JARVIS can answer "how are my receipts?" but not "how's my storage?" The answer is spread across file-management: volume capacity, backup mirrors, migration lanes, running/failed operations and drive health. Gardiner wants all four areas: a short spoken headline plus a HUD panel showing the status of each.

## Why not just read status.json

file-management already writes `status.json` (via `summarize.py`), but it is **12 days stale** (`generated: 2026-09-12T15:58:10`). The job that would refresh it every 5 minutes, `com.gabba.fleet-sweep.plist`, was deliberately left unloaded: `docs/specs/2026-08-12-digitize-station-independence-inventory.md` (~line 154) records that laptop + Mini both running it would regenerate `status.json` on independent timers, and which machine is the hub is parked until the new Mac Mini arrives.

Running `summarize.py` from the tool is also ruled out: `main()` calls `ops_sweep()` first, which appends to `ops-log.jsonl`, deletes/quarantines heartbeat files and relays files between ops zones. A tool the bridge auto-approves as read-only must not do any of that.

**Decision:** the server builds a **live snapshot in memory** on every call from `summarize.py`'s and `ledger.py`'s read-only functions, plus `os.statvfs`. It never calls `summarize.main()` or `ops_sweep()`, and writes nothing. The sweep rules that *resolve* stale alarms (step 3 dead-weight, step 5 supersession, step 6 abandonment) are re-applied in memory at read time — see Ops — so the laptop doesn't announce permanently-unresolvable alarms. The hub/sweep question stays parked.

## Architecture

New directory `file-management/mcp-server/`, same layout as `2nd-brain/mcp-server/`:

| File | Responsibility |
|---|---|
| `snapshot.py` | Collects raw data (table below). Each collector runs in its own daemon thread under one shared deadline; a failure or timeout marks only that source unavailable. Returns a plain dict. No MCP import. |
| `summary.py` | Pure: `build_summary(snapshot: dict, now: datetime) -> dict` → `{ok, headline, generated_at, sections}`. No I/O, no MCP import. Holds thresholds, `EXPECTED_MOUNTS`, and the phrasing maps as module constants. |
| `storage_status_server.py` | Thin FastMCP wrapper, stdio transport, one tool: `async def get_storage_status()`, no arguments. Imports `snapshot`/`summary` **lazily inside the tool** under `try`, runs `collect()` via `await anyio.to_thread.run_sync(...)` (FastMCP 1.30 runs sync tools inline on the event loop, `func_metadata.py:112-115`, which would block pings for the ~5 s worst case), and wraps collection in `contextlib.redirect_stdout(sys.stderr)` as belt-and-braces: nothing on the read path prints to stdout today (`read_ops_log` already uses `file=sys.stderr`), but stdout is the JSON-RPC channel. This is safe because the mcp stdio transport wraps `sys.stdout.buffer` once at `stdio_server()` entry (`server/stdio.py:~49`), so swapping `sys.stdout` later can't touch the transport's writer. Never raises to the client. |
| `requirements.txt` | `mcp>=1.0.0,<2` (unpinned installs pull a breaking 2.x API — same pin as 2nd-brain; 1.30.0 on Python 3.14 matches the 2nd-brain venv). |
| `.venv/` | Dedicated virtualenv, gitignored. `~/.claude.json`'s `command` points at its absolute `bin/python3`; `env` sets `PYTHONDONTWRITEBYTECODE=1`. |

**Tool name:** `get_storage_status` — passes `jarvis/bridge/server.mjs`'s `decideTool()` (`READ_VERB` `^get`, no `EFFECTFUL_VERB` hit). A noun-only name is silently denied (receipts-status lesson, 2026-09-24).
**Server key in `~/.claude.json`:** `storage-status`, top-level `mcpServers` (the bridge also merges `projects[homedir()].mcpServers`, `server.mjs:~355`; either works). Must not contain `__` (`mcpServerOf` splits tool names on it). The bridge reads the config once at start, so registration requires a bridge restart. There is no bridge-side per-tool timeout; the Agent SDK default applies, and ~5 s is well inside it.

**Import path and bytecode:** file-management's modules are top-level scripts (no package). `snapshot.py` sets `sys.dont_write_bytecode = True`, then `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`, then `import ledger, summarize`. Import chain verified side-effect-free by both reviews (constant/Path construction only; no sqlite open, no signal handlers).

### Collectors (snapshot.py)

| Source key | How | Freshness | Can hang? |
|---|---|---|---|
| `volumes` | See Capacity collector below. **Not** `summarize.volume_fullness()`: its `df -H` + `line.split()` parse loses any mount with a space in its name (`/Volumes/Apple Media-Syno` → `mount: "Media-Syno"`), and an abandoned `df` child lingers per call on a hung mount. | live | yes |
| `lanes` | `summarize.lane_summary(ledger.read_rows(ledger.LEDGER))` — already returns `by_status` per lane, no second pass | live (local) | no |
| `mirrors` | `ledger.read_rows(ledger.MIRRORS)` | hand-maintained CSV | no |
| `drives` | `ledger.read_rows(ledger.DRIVES)` | updated at check-in | no |
| `active_ops` | `summarize.active_operations([summarize.NAS_OPS_ZONE, summarize.LOCAL_OPS_FALLBACK])` (legacy=True, same call as `fleet_panel.py:~225`). On an **unmounted** NAS this silently returns `[]` (glob `OSError` → `continue`); `EXPECTED_MOUNTS` is what catches that case. On a **hung** mount it times out. | live | yes — SMB glob |
| `ops_log` | `summarize.read_ops_log(summarize.OPS_LOG_PATH)` — the full merged log dict (local, ~100 lines / 53 KB today). This is the log-side **pool** for the resolution rules, matching the sweep, which uses all of `merged_log` (`summarize.py:~841, ~870`). | as of last sweep | no |
| `recent_ops` | `summarize.recent_operations(summarize.OPS_LOG_PATH, n=5)` — window of 5 **plus every pinned unresolved failure** (never slice). **Display only**, never used as the resolution pool. | as of last sweep (ops-log mtime; 2026-09-11 today) | no |

Each collector records `{value, ok, error, as_of}`. `as_of` = call time for live sources; source file mtime for `mirrors`, `drives`, `ops_log`, `recent_ops`.

**Capacity collector:** list `Path("/Volumes").iterdir()` (local, can't hang), skip dot-entries, then stat each entry in **its own daemon thread** under the same shared deadline — `p.is_dir()` and `os.path.ismount()` both block on a hung SMB mount, so one hung share must not lose every volume after it. Keep entries where `os.path.ismount(p)` is true (verified: true on the smbfs mounts; false on `/Volumes/Macintosh HD`, a symlink to `/`, which is thereby deliberately excluded). Per volume, from `os.statvfs(p)`, always using **`f_frsize`** (on smbfs `f_bsize` is 2 MiB vs `f_frsize` 1 KiB — using `f_bsize` over-reports 2048×):
- `used_bytes = (f_blocks − f_bfree) * f_frsize`
- `avail_bytes = f_bavail * f_frsize`
- `size_bytes = f_blocks * f_frsize`
- `total = used + avail`; `pct = 0 if total == 0 else -(-100 * used // total)` — df's own `ceil(used / (used + avail))`, in integer math so an exact 85 can't float to 86 (live check: Projects-Syno → 90, matching `df`; the naive `1 − bavail/blocks` gives 89). `total == 0` (e.g. a zero-block map mount) → `pct` 0.
Per-volume work is wrapped in `try/except Exception`: a fast failure (e.g. `OSError` EIO from a stale SMB handle) is recorded `{name, error: str}`; only a thread that misses its deadline is recorded `{name, timed_out: true}`.

**Timeouts:** start all collector threads (`threading.Thread(daemon=True)`), set `deadline = time.monotonic() + 5`, then `t.join(max(0, deadline - time.monotonic()))` for each — one shared 5 s budget, not 5 s per thread. The capacity collector's **per-volume** threads join against `inner_deadline = deadline − 0.5`, leaving the capacity collector half a second to assemble and store its partial result before the outer join expires; without the margin, one hung volume makes the outer join expire at the same instant and the whole capacity section is lost. Each thread writes only into its **own private holder** object; after the joins, `collect()` copies each holder's value once and never hands a holder out, so a late-finishing abandoned thread can't mutate the returned snapshot mid-serialisation. A thread still alive at the deadline is abandoned (daemon; no locks or shared mutable state — verified) and its source is `ok: false, error: "timed out"`. No `ThreadPoolExecutor` (its shutdown waits on hung workers). A client cancel lets the ≤5 s worker finish (`to_thread.run_sync` default `cancellable=False`) — acceptable. No upstream change to file-management's existing modules.

**CSV failure granularity:** `ledger.read_rows` raises `ValueError` for the whole file on one malformed row (`ledger.py:31-50`). The dependent section/sub-source is then unavailable with the error text — same posture as `fleet_panel.py:213-216`. `_legacy_operations` also reads `drives.csv`, so a corrupt `drives.csv` degrades both `drives` and `active_ops`.

## Output shape

```
{
  "ok": bool,                 # false only when every section is unavailable
  "headline": str,            # spoken; ≤ 3 clauses; no paths, IDs or slugs
  "generated_at": iso str,
  "sections": {
    "capacity": { status, as_of, source: "live",   reason?, items: [{name, used_bytes, size_bytes, pct, level} | {name, timed_out: true} | {name, error: str}], missing: [name] },
    "backups":  { status, as_of, source: "manual", reason?, items: [{dest, target, method, last_verified, age_days, level}] },
    "lanes":    { status, as_of, source: "live",   reason?, items: [{lane, done, total, bytes_done, bytes_planned, open}] },
    "ops":      { status, as_of, source: "live",   reason?, failed: [...], abandoned: [...], stalled: [...], running: [...], recent: [...], recent_as_of, drive_warnings: [...], unavailable_sources: [...] }
  }
}
```

`status` per section ∈ `ok | warn | critical | unavailable`. Any section may carry an optional `reason` (plain English); `unavailable` always does.

### Section rules

- **capacity** — `level`: `critical` ≥ 95, `warn` ≥ 85, else `ok`. A timed-out or errored volume → `warn`. `EXPECTED_MOUNTS = {"Projects-Syno": "Projects-Syno", "_Inbox": "the Inbox share"}` (mount name → spoken name; names verified against `/Volumes`): an expected name absent from `iterdir()`, or whose `ismount` completed `False`, → listed in `missing`, section at least `warn`. A timed-out expected mount is **never** also `missing` (it's reported once, as "didn't respond"). Section status = worst of items and missing.
- **backups** — from `mirrors.csv` (`dest, mirror_target, method, last_verified_sync, status`). `age_days` = today − `last_verified_sync`. `level`: `warn` when `age_days` > 30, `status` ≠ `active`, or the date is unparseable. `source: "manual"` — a hand-recorded verification, not live Hyper Backup/Snapshot Replication telemetry; spoken as "checked", never "synced". Empty CSV → `ok`, `reason: "no mirrors configured"`.
- **lanes** — from `lane_summary()`. `done` = `by_status.verified + by_status.cleared` (same definition as `STATUS.md`); `bytes_done` = `bytes_verified`. **`open` = `by_status.planned > 0`.** Lanes whose work finished in states that never reach `verified` (`executed`, `reviewed-no-action` — e.g. `small-folders-triage`, `kids-corner-cull`) are not open. Section `warn` if any lane is open, else `ok`; never `critical`.
- **ops** — built from `active_ops` (heartbeats), `ops_log` (full log, the pool) and `drives`. Mirrors `ops_sweep` (`summarize.py:~786-900`) exactly, without any of its writes:
  1. **Dedupe by `op_id` (sweep step 3, `summarize.py:~786-813`):** if an `op_id` exists in both heartbeats and the log: (a) log entry has `terminal_source == "finish"` → drop the heartbeat copy (dead weight); (b) log entry is not a finish but the heartbeat has `terminal_source == "finish"` → keep the log copy but **overlay `state` and `terminal_source` from the heartbeat** (the sweep's correction: "finish always outranks close"; `resolved_by`/`resolution_note`/`logged_at` stay from the log); (c) otherwise keep the log copy as-is. `read_ops_log` already folds `resolution`/`correction` records into their `op` entry, so they never appear as separate pool members. Result: one entry per `op_id` = the **pool**.
  2. **Field notes:** heartbeat entries (from `_finalize_new_style_entry`) carry `started_at`/`finished_at`/`op_type`/`subject`/`target` and `resolved_by: None` but never `logged_at`; log entries carry `logged_at` and may lack `resolved_by`/`resolution_note` keys — always `.get()`. The "time" of an entry is `finished_at or logged_at or ""`, except a **terminal heartbeat not in the log** with no `finished_at` uses `started_at` (the sweep stamps `logged_at = now` before step 5, so nothing older could supersede it; `""` would let anything do so — no live effect today, every heartbeat has `finished_at`). Running and legacy entries have time `""` and can never supersede anything (matches the sweep).
  3. **`failed` (sweep step 5):** pool entries with `state == "failed"`, **excluding** log entries with `terminal_source == "auto-close"` and `error == "superseded — no clean finish"` — those are the sweep's own step-6 closures of abandoned runs and go to rule 4 (Gardiner ruling 2026-09-24: an abandoned run is `warn` both before and after a sweep, so JARVIS doesn't flip to "five runs failed" the moment the hub sweeps; `fleet_panel` will still show them red — accepted difference). The remaining failed entries are dropped if `summarize.is_resolved(e)` (honours `resolved_by` **and** `resolution_note` — deliberately broader than step 5's `resolved_by`-only check, per the 2026-08-09 ruling in `is_resolved`'s docstring) or if any *other* pool entry satisfies `summarize._same_run_family(e, other)` and has a strictly later time (any terminal state supersedes, as the sweep does; a newer failure supersedes an older one). The sweep also resolves `paused`, but paused is a first-class terminal state, not a failure, so it is never reported. What remains → `critical`.
  4. **`abandoned` (sweep step 6):** (i) pool entries with `state == "running"`, excluding the synthetic unreadable-heartbeat entry (step 7's territory) and legacy `checkin_scan` entries (`op_id` starting `legacy_` — the sweep never closes legacy heartbeats), where `last_beat` is missing/unparseable, **or** older than `summarize.ABANDON_S` (24 h), **or** `summarize._find_newer_match(e, pool, e["op_id"])` returns a newer same-family run; plus (ii) the sweep auto-closures excluded from rule 3, dropped if `is_resolved` or superseded by the same test as rule 3 (another pool entry, `_same_run_family`, strictly later time). → `warn`.
  5. **`stalled`:** `state == "running"`, `stale: True`, not abandoned — including legacy entries that would otherwise be abandoned, and the synthetic unreadable-heartbeat entry, identified by `subject_label == "unreadable heartbeat — inspect" and station is None and started_at is None` (every field but op_id/subject_label/state/progress/bytes is `None` in `_synthetic_unparseable_entry`; `op_type is None and last_beat is None` alone also matches a valid-but-sparse real heartbeat). The repo's designed visible alarm. → `warn`.
  6. **`running`:** `state == "running"`, not stale, not abandoned. Informational.
  7. Terminal `complete` / `paused` pool entries are **not reported, but remain pool members** (they're what supersedes failures).
  8. `recent`: `recent_ops` as returned (5 + pins), display only. `recent_as_of` = ops-log mtime; always shown dimmed (weeks old until a sweep runs — intended).
  9. `drive_warnings`: `drives.csv` rows with non-empty `health` other than `ok` → `{label, health, note}`, level `warn`. `note` = `notes` cut at the first `; `, ` -- `, or `. ` (sentence end — not a bare `.`, which would truncate "v1.2").
  10. **Sub-source failure:** if any of `active_ops`, `ops_log`, `drives` fails or times out, record it in `unavailable_sources`, set `reason`, and raise the section to at least `warn`. The section is `unavailable` only if all three fail. Each failed sub-source feeds the reserved headline clause (below).
  11. Section status = worst of the above.

### Headline

**Algorithm:**
1. Build two lists. **Problem clauses**, in priority order: critical capacity → failed operation → abandoned operation → stalled operation → drive health warning → overdue/abnormal backup → warn-level capacity → open lanes. **Reach clauses**: missing expected mounts, timed-out or errored volumes (both spoken in the "didn't respond" group), and failed sub-sources/sections.
2. If there are any reach clauses, merge them all into **one** sentence, always placed **last**, and fill at most two slots from problem clauses in priority order. Otherwise fill up to three slots from problem clauses.
3. No problems and no reach clauses → "Storage is healthy." Every section unavailable → `ok: false`, "I couldn't read any storage status right now."

**Wording:**
- Numbers: words for counts one through ten ("Two", "Five"), digits above ten; percentages always digits ("90 percent").
- `op_type` → spoken noun: `presort_plan`/`presort_execute` → "presort run", `index_build` → "index build", `checkin_scan` → "drive scan", `hash_compare` → "hash compare", anything else → "operation"; a clause covering mixed types says "operations". Never `subject_label` (new-style labels are slugged paths).
- Relative day (from the entry's time, else `started_at`): "today", "yesterday", "on the 20th" (same month), "on September 20th" (otherwise).
- Critical / warn capacity: "Projects-Syno is at 96 percent." Two or more in the same clause → "Projects-Syno and LaCie are over 95 percent." (critical clause) / "…are over 85 percent." (warn clause); the two are separate priorities, never mixed.
- Failed: "A presort run failed on the 20th." / "Two presort runs failed, most recently on the 20th."
- Abandoned: "An old presort run never finished cleanly." / "Five old presort runs never finished cleanly."
- Stalled: "A running operation has stopped reporting." (count if > 1)
- Drive warning: "Drive LaCie has a health warning." (labels like `4TB_Scratch` don't read as proper nouns after "The"; count if > 1: "Two drives have health warnings.")
- Backups, overdue: "The oldest backup check is 50 days old." Non-`active` or unparseable mirror: "A backup mirror needs checking."
- Open lanes: "Two migration lanes are still open."
- Reach (merged into one sentence): three groups, each rendered once, joined with ", and ":
  - mount group: "Projects-Syno isn't mounted" / "Projects-Syno and the Inbox share aren't mounted"
  - respond group: "Projects-Syno didn't respond" / "Projects-Syno and LaCie didn't respond"
  - read group: "I couldn't read <A>" / "I couldn't read <A>, <B> and <C>", with nouns "live operations" (`active_ops`), "the operations log" (`ops_log`), "drive health" (`drives`), "disk capacity", "the backup list", "the migration ledger".
  Example: "Projects-Syno isn't mounted, and I couldn't read live operations and the operations log." When there are **no** problem clauses but reach is non-empty, prefix "Everything I could read looks fine, but " (no case change — every group starts with a name or "I").

**Expected output against today's live data** (second review, applying these rules by hand to `/Volumes/_Inbox/_fleet-drops/_ops` — 162 heartbeats: 154 complete, 3 failed, 5 running — plus `indexes/ops`, the log, and the CSVs): all three failed `presort_execute` heartbeats are superseded by completed retries (`jw37`/`ceqo` by `ti0w` on 09-17, `l3pn` by `lmdp` on 09-20) → `failed = []`; the five running `presort_plan` heartbeats (09-12 to 09-17) are over 24 h old and have a newer same-family match → abandoned; ops `warn`. LaCie health `warn`; oldest backup check 50 days; Projects-Syno 90 % (warn); two open lanes (letter-normalize, PRESORT_Projects). Headline (confirmed by hand in review 3): **"Five old presort runs never finished cleanly. Drive LaCie has a health warning. The oldest backup check is 50 days old."** (Warn capacity and open lanes fall below the three-clause cap and appear only on the panel.) After the hub's first sweep these five become auto-closed log entries; per the 2026-09-24 ruling (ops rule 3/4) they are still classed abandoned, so the headline does not change. The build's first task re-runs `collect()` read-only and records the actual result here before tests are written.

### Panel

No new UI code. JARVIS's in-process `display` tool (`jarvis/bridge/panels.mjs`) lets the model author a panel from a fixed class set, and its own rule caps a panel at roughly 6 rows / 40 words (`panels.mjs:~93`). The row cap is enforced only by the model reading instructions, so the tool's **description** carries them. Draft text:

> Read-only storage health for file-management: disk capacity, backup checks, migration lanes, and operations/drive health. Speak `headline` as your reply. Then show a compact panel: one `.hud-row` per section in this order — Capacity, Backups, Lanes, Ops — with the section name as `.hud-label`, its `status` as a `.hud-tag` (add `.hud-hot` when warn or critical), and one `.hud-sub` line naming the single worst item. Add a `.hud-dim` "as of" line to any section whose `as_of` is over a day old. Do not list every volume, lane, mirror or operation in the panel. Only when asked for detail, open a `markup` blade with a `.hud-bar` (`--v`) per volume and per lane and a row per mirror and operation.

## Error handling

- Collector failure or timeout → that source unavailable; only its section/sub-source degrades (see ops rule 10).
- Malformed individual values (bad date, missing key in a heartbeat) → item `level: warn` or skipped, never raise. Malformed CSV → section/sub-source unavailable with the `ValueError` text.
- `snapshot`/`summary` import failure (repo moved, upstream syntax error) → caught by the lazy import inside the tool → `{ok: false, headline: "The storage status tool couldn't load file-management's code.", sections: {}}`. The server process still starts and registers the tool.
- Any other exception → same `ok: false` shape, generic headline; exception text to stderr only.

## Testing

In `file-management/tests/`, `unittest`, 2-space indent, matching the existing suite. Test files do `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))` to import `summary`/`snapshot` (same approach as 2nd-brain's `tests/test_receipts_status_summary.py:~8`).

1. **`test_storage_status_summary.py`** — pure `build_summary` from fixture snapshots: capacity threshold edges (84/85/94/95 %); backup edges (30/31 days, non-active, unparseable); missing expected mount + spoken name; timed-out volume; lane `open` rule (planned vs executed/reviewed-no-action); op_id dedupe (finish-in-log drops heartbeat; late finish heartbeat corrects a closed log entry's state; otherwise log copy wins); supersession (failed + later terminal same family → dropped; newer failure supersedes older; running/legacy can't supersede; `resolution_note` resolves; unlogged terminal heartbeat without `finished_at` falls back to `started_at`); abandonment (no last_beat / > 24 h / newer match; sweep auto-closed "superseded — no clean finish" log entry → abandoned wording, not failed, and dropped once a later same-family terminal run exists); legacy `checkin_scan` running > 24 h → stalled, not abandoned; synthetic unreadable → stalled, never abandoned; sparse real heartbeat (`op_type`/`last_beat` None) not mistaken for synthetic; capacity `total == 0` → pct 0; exact-85 integer ceil; timed-out expected mount not also `missing`; reach groups joined correctly and the "Everything I could read looks fine, but" prefix; complete/paused never reported but still supersede; pinned failure beyond the window reported; sub-source failure → warn + reach clause; headline algorithm (priority order, three-clause cap, merged reach clause always last with ≤ 2 problem clauses, multiple reach parts merged into one sentence); every wording rule above; no slug/path in any headline; all-healthy; all-unavailable. Plus one fixture reproducing today's expected output above.
2. **`test_storage_status_snapshot.py`** — each collector patched to raise → only its source degrades; two collectors patched to `time.sleep(10)` → both marked timed out and `collect()` returns in < 7 s (shared deadline); one volume's stat patched to hang → that volume `timed_out`, other volumes still reported and the capacity section itself not timed out (proves the inner-deadline margin); a volume's statvfs patched to raise `OSError` → `{name, error}`, not `timed_out`; an abandoned thread finishing after `collect()` returns does not change the returned dict; malformed CSV → unavailable; statvfs math: fixture `statvfs_result` values → expected bytes/pct, and one live mount asserted against `df -k`'s used% (skipped if no mounts).
3. **No-write tripwire** — take the "before" inventory **after** imports. Patch to raise: `summarize.ops_sweep`, `summarize.main`, `summarize.append_ops_log`, `summarize._safe_delete`, `summarize._quarantine_rename_file`, `summarize._atomic_write_relay`, `summarize._acquire_ops_lock`, `ledger.write_rows`, `ops_heartbeat.pick_zone`, `ops_heartbeat.write_heartbeat`, `os.replace`, `os.rename`, `os.remove`, `os.unlink`, `os.makedirs`, `Path.unlink`, `Path.mkdir`, `Path.write_text`, `Path.rename`, `Path.replace`, `Path.touch`, `shutil.move`, `shutil.rmtree`, `sqlite3.connect`, `subprocess.run` (proves `df` is sidestepped), and `builtins.open`/`Path.open` when the mode contains `w`, `a`, `x` or `+`. Run the real `collect()` + `build_summary()`; assert success. Inventory path + mtime + size of every file under the repo, excluding `.git/`, `__pycache__/` and `.venv/`; after the run assert no file was removed and no pre-existing file's mtime/size changed (new files from another machine's live heartbeats don't fail the test). Include `/Volumes/_Inbox/_fleet-drops/_ops` and `…/_heartbeats` in the inventory only when mounted, under the same "nothing removed or modified" assertion. (The test imports only `snapshot`/`summary` — no MCP SDK or anyio runs — so these patches can't break the transport.)
4. **Full existing suite** stays green (`python3 -m unittest discover tests`).
5. **Live end-to-end through JARVIS** (required before "done"): register `storage-status` in `~/.claude.json` (stop-and-confirm with Gardiner first — full read-modify-write of Claude Code's global state file), restart the bridge, ask "how's my storage?", confirm the spoken headline and the compact four-row panel, then ask for detail and confirm the blade.

## Out of scope (log as deferred items in file-management's ledger)

- Loading `com.gabba.fleet-sweep` / choosing the hub — Mac Mini session.
- Any write or action tool (purge, rescan, check-in, resolving ops).
- Live mirror telemetry from Synology Hyper Backup / Snapshot Replication instead of hand-kept `mirrors.csv`.
- Per-drive SMART reads at call time.
- Fixing `summarize.volume_fullness()`'s space-in-mount-name parse bug for `STATUS.md`/`fleet_panel` (found by review 1; this server sidesteps it via `statvfs`).
- `recent_operations()` ordering by `logged_at` degrades after a bulk sweep (every swept entry shares one `logged_at`) — affects `fleet_panel` too (found by review 2).

## Review log

- **r1 → r2, fresh-eyes review 1 (Fable subagent, spec + repos only), 19 findings, all accepted.** Blocker: expected output derived from stale `status.json`. Majors: permanent unresolvable ops alarms (added in-memory supersession/abandonment), `recent[:5]` dropped pinned failures, `df` parse lost space-named mounts (→ `statvfs`), unmounted NAS read as healthy (→ `EXPECTED_MOUNTS` + reserved slot), additive joins (→ shared deadline), panel exceeded the display tool's row cap (→ compact panel + detail blade), lane `open` mismatch (ruled: open = has `planned` rows), weak no-write test (→ tripwire). Plus 10 minors.
- **r2 → r3, fresh-eyes review 2 (new Fable subagent), 13 findings, all accepted.** Blocker: expected output still wrong — all three failures are superseded by retries; hand-applied rules give the headline now recorded above. Majors: resolution pool must be the full log (`read_ops_log`), not the 5-entry display window; op_id dedupe (sweep step 3) was missing → double counting; statvfs formula didn't match df (89 vs 90) and risked `f_bsize` 2048× error; reserved-slot rule contradicted the priority list (→ explicit algorithm, merged reach clause, spoken-name map); ops sub-source failure had no status and never reached the headline. Minors: tripwire false-fail on bytecode/live heartbeats + missing patches, abandoned threads mutating the snapshot (→ private holders), one hung mount blocking all volumes (→ thread per volume), redirect_stdout rationale, field-shape notes, phrasing maps and tool-description text, bridge config/timeout facts.
- **r3 → r4, fresh-eyes review 3 (new Fable subagent, scoped to Ops/Headline/Capacity/Timeouts/Tests), 12 findings, all accepted; expected headline independently re-derived and confirmed.** Majors: dedupe missed sweep step 3's finish-corrects-close branch; per-volume threads shared the outer deadline instant, so one hung mount lost all capacity (→ 0.5 s inner margin); abandoned runs would flip warn → critical after the first sweep (**Gardiner ruling: warn both ways** — sweep auto-closures classed abandoned). Minors: legacy scans excluded from abandonment, stricter synthetic-entry discriminator, per-volume error path + integer ceil + zero-total guard, timed-out expected mount not double-reported, reach-clause grouping + no-problem prefix, critical/warn plural wording, `started_at` time fallback, "Drive <label>" wording, test additions.
- **r4 targeted re-read (reviewer 3):** all 12 fixes confirmed; expected headline re-confirmed post-sweep. Two new minors fixed: reach-prefix lower-casing removed; rule 4(ii) auto-closures now dropped by the same supersession test as rule 3. Verdict: ready to lock.
