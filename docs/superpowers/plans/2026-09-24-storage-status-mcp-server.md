# Storage Status MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give JARVIS a read-only MCP tool, `get_storage_status`, that answers "how's my storage?" with a spoken worst-first headline and a four-section structured result (capacity, backups, lanes, ops) built from a live, in-memory snapshot of file-management's data.

**Architecture:** `snapshot.py` collects raw data in daemon threads under one shared 5 s deadline by calling file-management's existing read-only functions (never `summarize.main()` / `ops_sweep()`), plus `os.statvfs` per volume. Pure modules turn that snapshot into the result: `ops_rules.py` (sweep steps 3/5/6 applied in memory), `sections.py` (per-section status), `headline.py` (spoken sentence), `summary.py` (assembly). A thin async FastMCP wrapper, `storage_status_server.py`, exposes the one tool.

**Tech Stack:** Python 3.14 (system `python3` for tests), `mcp>=1.0.0,<2` (`FastMCP`) + `anyio` in a dedicated venv at `mcp-server/.venv`, `unittest` (stdlib), 2-space indentation.

**Spec:** `docs/superpowers/specs/2026-09-24-storage-status-mcp-server-design.md` (this `jarvis` repo, LOCKED r4). Read it alongside this plan; this plan does not re-argue its rulings.

## Global Constraints

- **Strictly read-only.** Never call `summarize.main()` or `summarize.ops_sweep()`. Never write, rename, delete, lock, or `mkdir` anything, anywhere (spec: Decision).
- **Tool name `get_storage_status`**, server key `storage-status` (no `__`) — a non-`READ_VERB` name is silently denied by JARVIS's bridge (spec: Architecture).
- **Code lives in `file-management/mcp-server/`**, developed in a file-management worktree on branch `feat/storage-status-mcp`. Existing file-management modules are **not modified**.
- **Style:** 2-space indentation, `unittest.TestCase`, test files do `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))`. No pytest, no conftest (matches the existing `tests/` suite).
- **Shared deadline:** 5.0 s for all collectors; per-volume threads use `deadline − 0.5` (spec: Timeouts).
- **Capacity math:** `f_frsize` only; `pct = 0 if total == 0 else -(-100 * used // total)`, `used = (f_blocks − f_bfree) * f_frsize`, `total = used + f_bavail * f_frsize` (spec: Capacity collector).
- **Thresholds:** capacity warn ≥ 85, critical ≥ 95; backup check overdue when `age_days > 30`; `ABANDON_S` = `summarize.ABANDON_S` (24 h).
- **`EXPECTED_MOUNTS = {"Projects-Syno": "Projects-Syno", "_Inbox": "the Inbox share"}`.**
- **Reuse, don't re-derive:** `summarize._same_run_family`, `summarize._find_newer_match`, `summarize.is_resolved`, `summarize.lane_summary`, `summarize.active_operations`, `summarize.read_ops_log`, `summarize.recent_operations`, `ledger.read_rows`.
- **Headline:** ≤ 3 clauses; if any reach clause exists, one merged reach sentence goes last and ≤ 2 problem clauses precede it; never a path, op_id, or `subject_label` (spec: Headline).
- **Stop-and-confirm with Gardiner** before: `pip install` into the venv (Task 6), editing `~/.claude.json` (Task 7), and merging to file-management `main` (Task 7).

## Review Focus

1. **NAS mounted but hung (SMB stalls, not unmounted)** — expect a ~5 s answer that still reports every other section, with "didn't respond" / "I couldn't read live operations" spoken; pinned by Task 1's hang tests and Task 5's reach-clause tests.
2. **Another machine writing heartbeats while the tool runs** — expect no crash, no false failure; pinned by Task 2's tripwire tolerating new/changed NAS files only when fresh.
3. **A hub sweep runs later (Mac Mini)** — the five abandoned runs become auto-closed log entries; expect the same headline; pinned by Task 3's auto-close test and Task 5's today-fixture.
4. **Worktree vs. main checkout data** — `ops-log.jsonl` is untracked, so it exists only in the main checkout; expect the tool to degrade to an empty log in the worktree, never crash; covered by `read_ops_log` returning `{}` for a missing file, and the real headline recorded only after merge (Task 7).
5. **Import failure when the repo moves** — expect a spoken "couldn't load file-management's code" rather than a dead server; pinned by Task 6's smoke step with a broken path.

## File map

| File | Responsibility |
|---|---|
| `mcp-server/_paths.py` | Puts the repo root on `sys.path`, disables bytecode writes. Imported first by every module that needs `ledger`/`summarize`. |
| `mcp-server/snapshot.py` | Collectors, per-volume probes, shared-deadline threading. Only module doing I/O. |
| `mcp-server/ops_rules.py` | Pure: sweep steps 3/5/6 in memory → `{failed, abandoned, stalled, running}`. |
| `mcp-server/sections.py` | Pure: the four section dicts from snapshot sources. |
| `mcp-server/headline.py` | Pure: spoken headline from the section dicts. |
| `mcp-server/summary.py` | Pure: `build_summary(snapshot, now)` assembly. |
| `mcp-server/storage_status_server.py` | FastMCP wrapper, one async tool. |
| `mcp-server/requirements.txt` | `mcp>=1.0.0,<2` |
| `tests/test_storage_status_snapshot.py` | Task 1 + Task 2 tests. |
| `tests/test_storage_status_ops_rules.py` | Task 3 tests. |
| `tests/test_storage_status_sections.py` | Task 4 tests. |
| `tests/test_storage_status_summary.py` | Task 5 tests. |
| `.gitignore` | Add `mcp-server/.venv/`. |

All paths below are relative to the file-management worktree root, `WT=/Users/gardinerwelch/Documents/_Projects/file-management/.claude/worktrees/storage-status-mcp`.

---

### Task 0: Worktree setup

- [ ] **Step 1: Create the worktree and verify location** (first command of every subagent task too: `cd $WT && pwd` — expect exactly the `WT` path above; stop if different. Lesson from receipts-status: a subagent ignored a prose "work in this directory" instruction.)

```bash
cd /Users/gardinerwelch/Documents/_Projects/file-management && git status --short && git worktree add .claude/worktrees/storage-status-mcp -b feat/storage-status-mcp main && cd .claude/worktrees/storage-status-mcp && pwd && git branch --show-current
```
Expected: `pwd` prints the `WT` path, branch `feat/storage-status-mcp`. (Untracked `ops-log.jsonl` / `manifests/LaCie_verify-ok-to-delete_progress.jsonl` in the main checkout belong to other sessions — do not touch them.)

- [ ] **Step 2: Baseline the existing suite**

Run: `cd $WT && python3 -m unittest discover tests 2>&1 | tail -3`
Expected: `OK` (record the test count; Task 7 re-checks it).

---

### Task 1: `snapshot.py` — collectors, volume probes, shared deadline

**Files:**
- Create: `mcp-server/_paths.py`, `mcp-server/snapshot.py`
- Test: `tests/test_storage_status_snapshot.py`

**Interfaces:**
- Produces: `snapshot.collect(deadline_s: float = 5.0, sources: dict | None = None) -> dict` returning `{"collected_at": iso str, "sources": {key: {"value", "ok": bool, "error": str | None, "as_of": iso str | None}}}` with keys `volumes, lanes, mirrors, drives, active_ops, ops_log, recent_ops`. `sources` maps key → `callable(inner_deadline: float)`; tests pass their own.
- Produces: `snapshot.volume_stats(st) -> {"used_bytes": int, "size_bytes": int, "pct": int}` (pure, takes a `statvfs`-like object).
- Produces: `snapshot.collect_volumes(inner_deadline: float) -> list[dict]`; each item is `{"name", "mounted": True, "used_bytes", "size_bytes", "pct"}`, `{"name", "mounted": False}`, `{"name", "timed_out": True}`, or `{"name", "error": str}`.
- Produces: `snapshot.VOLUMES_DIR`, `snapshot.DEADLINE_S = 5.0`, `snapshot.VOLUME_MARGIN_S = 0.5` (module constants tests patch).

- [ ] **Step 1: Write the failing tests** — create `tests/test_storage_status_snapshot.py`:

```python
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))

import snapshot  # noqa: E402


def fake_statvfs(blocks, bfree, bavail, frsize=1024, bsize=2 * 1024 * 1024):
  return SimpleNamespace(f_blocks=blocks, f_bfree=bfree, f_bavail=bavail,
                         f_frsize=frsize, f_bsize=bsize)


class VolumeStats(unittest.TestCase):
  def test_uses_frsize_not_bsize(self):
    s = snapshot.volume_stats(fake_statvfs(1000, 400, 400))
    self.assertEqual(s["size_bytes"], 1000 * 1024)
    self.assertEqual(s["used_bytes"], 600 * 1024)

  def test_pct_is_df_ceil_of_used_over_used_plus_avail(self):
    # used 600, avail 300 -> 66.67 -> 67 (naive 1 - bavail/blocks = 70)
    self.assertEqual(snapshot.volume_stats(fake_statvfs(1000, 400, 300))["pct"], 67)

  def test_exact_85_stays_85(self):
    self.assertEqual(snapshot.volume_stats(fake_statvfs(100, 15, 15))["pct"], 85)

  def test_zero_total_is_zero_pct(self):
    self.assertEqual(snapshot.volume_stats(fake_statvfs(0, 0, 0))["pct"], 0)

  def test_matches_df_on_a_live_mount(self):
    mounts = [p for p in Path("/Volumes").iterdir()
              if not p.name.startswith(".") and os.path.ismount(p)]
    if not mounts:
      self.skipTest("no mounted volumes")
    path = mounts[0]
    out = subprocess.run(["df", "-kP", str(path)], capture_output=True,
                         text=True, timeout=5).stdout.splitlines()[1]
    df_pct = int(out.split()[4].rstrip("%"))
    self.assertEqual(snapshot.volume_stats(os.statvfs(path))["pct"], df_pct)


class CollectVolumes(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    root = Path(self.tmp.name)
    for name in ("Alpha", "Beta", ".hidden"):
      (root / name).mkdir()
    self.patch_dir = mock.patch.object(snapshot, "VOLUMES_DIR", root)
    self.patch_dir.start()

  def tearDown(self):
    self.patch_dir.stop()
    self.tmp.cleanup()

  def test_hung_volume_times_out_others_still_reported(self):
    def probe(path):
      if path.name == "Alpha":
        time.sleep(10)
      return {"name": path.name, "mounted": True, "used_bytes": 1,
              "size_bytes": 2, "pct": 50}
    with mock.patch.object(snapshot, "_probe_volume", probe):
      result = snapshot.collect(deadline_s=1.0, sources={
        "volumes": snapshot.collect_volumes})
    vol = result["sources"]["volumes"]
    self.assertTrue(vol["ok"], "capacity section itself must not time out")
    by_name = {v["name"]: v for v in vol["value"]}
    self.assertEqual(by_name["Alpha"], {"name": "Alpha", "timed_out": True})
    self.assertEqual(by_name["Beta"]["pct"], 50)
    self.assertNotIn(".hidden", by_name)

  def test_statvfs_oserror_is_error_not_timeout(self):
    with mock.patch.object(snapshot.os.path, "ismount", return_value=True), \
         mock.patch.object(snapshot.os, "statvfs", side_effect=OSError(5, "EIO")):
      items = snapshot.collect_volumes(time.monotonic() + 1)
    self.assertTrue(all("error" in v and "EIO" in v["error"] for v in items))

  def test_not_a_mount_is_reported_unmounted(self):
    items = snapshot.collect_volumes(time.monotonic() + 1)
    self.assertIn({"name": "Alpha", "mounted": False}, items)


class Collect(unittest.TestCase):
  def test_failing_source_only_degrades_itself(self):
    def boom(inner):
      raise ValueError("ledger.csv: row 3 has more fields than the header")
    result = snapshot.collect(sources={"a": boom, "b": lambda inner: [1]})
    a, b = result["sources"]["a"], result["sources"]["b"]
    self.assertFalse(a["ok"])
    self.assertIn("ValueError", a["error"])
    self.assertIn("more fields than the header", a["error"])
    self.assertEqual(b, {"value": [1], "ok": True, "error": None,
                         "as_of": b["as_of"]})

  def test_shared_deadline_not_additive(self):
    slow = lambda inner: time.sleep(10)
    start = time.monotonic()
    result = snapshot.collect(deadline_s=2.0, sources={"a": slow, "b": slow})
    self.assertLess(time.monotonic() - start, 3.0)
    for key in ("a", "b"):
      self.assertEqual(result["sources"][key]["error"], "timed out")

  def test_late_thread_cannot_mutate_returned_snapshot(self):
    def late(inner):
      time.sleep(0.6)
      return "late value"
    result = snapshot.collect(deadline_s=0.2, sources={"a": late})
    before = dict(result["sources"]["a"])
    time.sleep(0.8)
    self.assertEqual(result["sources"]["a"], before)
    self.assertEqual(before["error"], "timed out")

  def test_file_sources_use_file_mtime_as_of(self):
    result = snapshot.collect(sources={"mirrors": lambda inner: []})
    expected = snapshot._mtime_iso(snapshot.ledger.MIRRORS)
    self.assertEqual(result["sources"]["mirrors"]["as_of"], expected)

  def test_real_read_of_malformed_csv_degrades(self):
    with tempfile.TemporaryDirectory() as d:
      bad = Path(d) / "drives.csv"
      bad.write_text("drive_id,label\nGW-D01,LaCie,extra\n")
      result = snapshot.collect(sources={
        "drives": lambda inner: snapshot.ledger.read_rows(bad)})
    self.assertFalse(result["sources"]["drives"]["ok"])
    self.assertIn("more fields than the header",
                  result["sources"]["drives"]["error"])


if __name__ == "__main__":
  unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd $WT && python3 -m unittest tests.test_storage_status_snapshot -v 2>&1 | tail -5`
Expected: `ModuleNotFoundError: No module named 'snapshot'`.

- [ ] **Step 3: Implement** — create `mcp-server/_paths.py`:

```python
"""Make file-management's top-level modules (ledger, summarize, ...)
importable from mcp-server/, and keep this read-only server from writing
bytecode into the repo. Import this before `ledger`/`summarize`."""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(REPO_ROOT))
```

Create `mcp-server/snapshot.py`:

```python
"""Live, read-only snapshot of file-management's storage state.

Calls only summarize.py / ledger.py read functions plus os.statvfs —
never summarize.main() or ops_sweep(), which write (see the jarvis spec
docs/superpowers/specs/2026-09-24-storage-status-mcp-server-design.md).
Every source runs in its own daemon thread under ONE shared deadline, so a
hung SMB mount costs at most DEADLINE_S for the whole call and degrades
only its own source."""
import datetime
import os
import threading
import time
from pathlib import Path

import _paths  # noqa: F401
import ledger  # noqa: E402
import summarize  # noqa: E402

DEADLINE_S = 5.0
# Per-volume probes stop this much earlier than the outer deadline so the
# volumes collector can still store its partial result before the outer
# join gives up on it — without the margin one hung mount loses them all.
VOLUME_MARGIN_S = 0.5
VOLUMES_DIR = Path("/Volumes")

FILE_SOURCES = {
  "mirrors": ledger.MIRRORS,
  "drives": ledger.DRIVES,
  "ops_log": summarize.OPS_LOG_PATH,
  "recent_ops": summarize.OPS_LOG_PATH,
}


class _Holder:
  """Private per-thread result slot. collect() reads it once after the
  join; a thread that finishes later writes only here, never into the
  snapshot already handed back."""
  __slots__ = ("result",)

  def __init__(self):
    self.result = None


def _run(fn, arg, holder):
  try:
    holder.result = {"value": fn(arg), "ok": True, "error": None}
  except Exception as exc:  # noqa: BLE001 — any failure degrades one source
    holder.result = {"value": None, "ok": False,
                     "error": "{}: {}".format(type(exc).__name__, exc)}


def _run_all(jobs, deadline):
  """jobs: list of (key, fn, arg). Returns {key: result-or-None}."""
  started = []
  for key, fn, arg in jobs:
    holder = _Holder()
    thread = threading.Thread(target=_run, args=(fn, arg, holder), daemon=True)
    thread.start()
    started.append((key, holder, thread))
  for _, _, thread in started:
    thread.join(max(0.0, deadline - time.monotonic()))
  return {key: holder.result for key, holder, _ in started}


def _mtime_iso(path):
  try:
    ts = Path(path).stat().st_mtime
  except OSError:
    return None
  return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def volume_stats(st):
  # f_frsize, never f_bsize: on smbfs f_bsize is 2 MiB vs f_frsize 1 KiB.
  used = (st.f_blocks - st.f_bfree) * st.f_frsize
  avail = st.f_bavail * st.f_frsize
  total = used + avail
  # df's own ceil(used / (used + avail)), in integer math.
  pct = 0 if total == 0 else -(-100 * used // total)
  return {"used_bytes": used, "size_bytes": st.f_blocks * st.f_frsize,
          "pct": pct}


def _probe_volume(path):
  if not os.path.ismount(path):
    return {"name": path.name, "mounted": False}
  return {"name": path.name, "mounted": True, **volume_stats(os.statvfs(path))}


def collect_volumes(inner_deadline):
  entries = sorted(p for p in VOLUMES_DIR.iterdir() if not p.name.startswith("."))
  results = _run_all([(p.name, _probe_volume, p) for p in entries],
                     inner_deadline)
  items = []
  for path in entries:
    result = results[path.name]
    if result is None:
      items.append({"name": path.name, "timed_out": True})
    elif not result["ok"]:
      items.append({"name": path.name, "error": result["error"]})
    else:
      items.append(result["value"])
  return items


def default_sources():
  return {
    "volumes": collect_volumes,
    "lanes": lambda inner: summarize.lane_summary(ledger.read_rows(ledger.LEDGER)),
    "mirrors": lambda inner: ledger.read_rows(ledger.MIRRORS),
    "drives": lambda inner: ledger.read_rows(ledger.DRIVES),
    "active_ops": lambda inner: summarize.active_operations(
      [summarize.NAS_OPS_ZONE, summarize.LOCAL_OPS_FALLBACK]),
    "ops_log": lambda inner: summarize.read_ops_log(summarize.OPS_LOG_PATH),
    "recent_ops": lambda inner: summarize.recent_operations(
      summarize.OPS_LOG_PATH, n=5),
  }


def collect(deadline_s=DEADLINE_S, sources=None):
  sources = sources if sources is not None else default_sources()
  deadline = time.monotonic() + deadline_s
  inner_deadline = deadline - VOLUME_MARGIN_S
  results = _run_all([(key, fn, inner_deadline) for key, fn in sources.items()],
                     deadline)
  now_iso = datetime.datetime.now().isoformat(timespec="seconds")
  out = {}
  for key, result in results.items():
    entry = dict(result) if result is not None else {
      "value": None, "ok": False, "error": "timed out"}
    entry["as_of"] = _mtime_iso(FILE_SOURCES[key]) if key in FILE_SOURCES else now_iso
    out[key] = entry
  return {"collected_at": now_iso, "sources": out}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd $WT && python3 -m unittest tests.test_storage_status_snapshot -v 2>&1 | tail -5`
Expected: all pass (`test_matches_df_on_a_live_mount` may be skipped only if nothing is mounted).

- [ ] **Step 5: Commit**

```bash
cd $WT && git add mcp-server/_paths.py mcp-server/snapshot.py tests/test_storage_status_snapshot.py && git commit -m "feat(storage-status): live read-only snapshot collectors with shared deadline"
```

---

### Task 2: No-write tripwire

**Files:**
- Modify: `tests/test_storage_status_snapshot.py` (append a class)

**Interfaces:**
- Consumes: `snapshot.collect()`, `snapshot.REPO_ROOT` via `_paths.REPO_ROOT`.

- [ ] **Step 1: Write the tripwire test** — append before the `if __name__` block:

```python
import builtins  # noqa: E402
import shutil  # noqa: E402
import sqlite3  # noqa: E402

import _paths  # noqa: E402
import ops_heartbeat  # noqa: E402

WRITE_MODES = set("wax+")
SKIP_DIRS = {".git", "__pycache__", ".venv", ".claude"}


def _inventory(root, skip=SKIP_DIRS):
  inv = {}
  for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in skip]
    for name in filenames:
      p = os.path.join(dirpath, name)
      try:
        st = os.stat(p)
      except OSError:
        continue
      inv[p] = (st.st_mtime, st.st_size)
  return inv


def _quiet_nas_dirs():
  """NAS ops dirs are inventoried only when no heartbeat is fresh — a
  live op on another machine legitimately rewrites its own heartbeat."""
  dirs = []
  for d in (snapshot.summarize.NAS_OPS_ZONE,
            snapshot.ledger.FLEET_DROP_ZONE / "_heartbeats"):
    try:
      mtimes = [p.stat().st_mtime for p in d.iterdir()]
    except OSError:
      continue
    if all(time.time() - m > snapshot.summarize.HEARTBEAT_STALE_S for m in mtimes):
      dirs.append(d)
  return dirs


class NoWriteTripwire(unittest.TestCase):
  def test_collect_and_summarize_never_write(self):
    def refuse(*a, **k):
      raise AssertionError("write path called: {!r}".format(a[:1]))

    real_open = builtins.open
    real_path_open = Path.open

    def guarded_open(file, mode="r", *a, **k):
      if WRITE_MODES & set(mode):
        raise AssertionError("open for write: {} {}".format(file, mode))
      return real_open(file, mode, *a, **k)

    def guarded_path_open(self, mode="r", *a, **k):
      if WRITE_MODES & set(mode):
        raise AssertionError("Path.open for write: {} {}".format(self, mode))
      return real_path_open(self, mode, *a, **k)

    targets = [
      (snapshot.summarize, "ops_sweep"), (snapshot.summarize, "main"),
      (snapshot.summarize, "append_ops_log"), (snapshot.summarize, "_safe_delete"),
      (snapshot.summarize, "_quarantine_rename_file"),
      (snapshot.summarize, "_atomic_write_relay"),
      (snapshot.summarize, "_acquire_ops_lock"),
      (snapshot.ledger, "write_rows"),
      (ops_heartbeat, "pick_zone"), (ops_heartbeat, "write_heartbeat"),
      (os, "replace"), (os, "rename"), (os, "remove"), (os, "unlink"),
      (os, "makedirs"), (Path, "unlink"), (Path, "mkdir"),
      (Path, "write_text"), (Path, "rename"), (Path, "replace"),
      (Path, "touch"), (shutil, "move"), (shutil, "rmtree"),
      (sqlite3, "connect"), (subprocess, "run"),
    ]
    roots = [_paths.REPO_ROOT] + _quiet_nas_dirs()
    before = {}
    for r in roots:
      before.update(_inventory(r))

    patches = [mock.patch.object(obj, name, refuse) for obj, name in targets]
    patches += [mock.patch.object(builtins, "open", guarded_open),
                mock.patch.object(Path, "open", guarded_path_open)]
    for p in patches:
      p.start()
    try:
      import summary
      result = snapshot.collect()
      summary.build_summary(result, snapshot.datetime.datetime.now())
    finally:
      for p in reversed(patches):
        p.stop()

    for key, src in result["sources"].items():
      self.assertNotIn("AssertionError", src["error"] or "",
                       "{} hit a write path: {}".format(key, src["error"]))
    after = {}
    for r in roots:
      after.update(_inventory(r))
    removed = sorted(set(before) - set(after))
    changed = sorted(p for p in before if p in after and before[p] != after[p])
    self.assertEqual(removed, [], "files removed")
    self.assertEqual(changed, [], "files modified")
```

Note: this test imports `summary`, which is created in Task 5. Until then, run it with the two `summary` lines commented out is **not** allowed — instead, mark it expected-to-error in Step 2 and re-run it in Task 5 Step 4.

- [ ] **Step 2: Run it**

Run: `cd $WT && python3 -m unittest tests.test_storage_status_snapshot.NoWriteTripwire -v 2>&1 | tail -5`
Expected now: ERROR `ModuleNotFoundError: No module named 'summary'` (it passes in Task 5). Temporarily verify the collect half by running:
`cd $WT && python3 -c "import sys; sys.path.insert(0,'mcp-server'); import snapshot, json; r=snapshot.collect(); print({k:(v['ok'],v['error']) for k,v in r['sources'].items()})"`
Expected: every source `(True, None)` except possibly `active_ops`/`volumes` degraded if the NAS is off; no traceback.

- [ ] **Step 3: Commit**

```bash
cd $WT && git add tests/test_storage_status_snapshot.py && git commit -m "test(storage-status): no-write tripwire over collect + summary"
```

---

### Task 3: `ops_rules.py` — sweep steps 3/5/6 in memory

**Files:**
- Create: `mcp-server/ops_rules.py`
- Test: `tests/test_storage_status_ops_rules.py`

**Interfaces:**
- Consumes: `summarize._same_run_family(a, b)`, `summarize._find_newer_match(entry, candidates, self_op_id)`, `summarize.is_resolved(entry)`, `summarize.ABANDON_S`.
- Produces: `ops_rules.classify_ops(active_ops: list[dict], ops_log: dict[str, dict], now: datetime) -> {"failed": [...], "abandoned": [...], "stalled": [...], "running": [...]}` — lists of pool entry dicts (each carries a `_from` key, `"heartbeat"` or `"log"`).
- Produces: `ops_rules.entry_time(entry) -> str` (ISO string or `""`).

- [ ] **Step 1: Write the failing tests** — create `tests/test_storage_status_ops_rules.py`:

```python
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))

import ops_rules  # noqa: E402

NOW = datetime(2026, 9, 24, 18, 0, 0)
FIELDS = ["op_id", "op_type", "subject_kind", "subject", "target", "subject_label",
          "batch", "station", "started_at", "last_beat", "phase", "progress",
          "bytes", "state", "terminal_source", "error", "finished_at"]


def hb(op_id, state="running", started_at="2026-09-24T17:00:00",
       last_beat="2026-09-24T17:59:00", finished_at=None, stale=False,
       terminal_source=None, op_type="presort_plan",
       subject="/Volumes/Projects-Syno", station="mini", **kw):
  e = {f: None for f in FIELDS}
  e.update(op_id=op_id, op_type=op_type, subject=subject, station=station,
           started_at=started_at, last_beat=last_beat, state=state,
           finished_at=finished_at, terminal_source=terminal_source,
           subject_label="-volumes-projects-syno")
  e.update(stale=stale, heartbeat_age_s=None, safe_to_unmount=False,
           resolved_by=None, resolution_note=None)
  e.update(kw)
  return e


def log(op_id, state="complete", finished_at="2026-09-24T12:00:00",
        logged_at="2026-09-24T12:05:00", terminal_source="finish", **kw):
  e = hb(op_id, state=state, finished_at=finished_at,
         terminal_source=terminal_source, **kw)
  for k in ("stale", "heartbeat_age_s", "safe_to_unmount"):
    e.pop(k)
  e.update(record="op", logged_at=logged_at)
  return e


def ids(entries):
  return sorted(e["op_id"] for e in entries)


class Dedupe(unittest.TestCase):
  def test_finish_in_log_drops_heartbeat(self):
    out = ops_rules.classify_ops([hb("a", state="running")],
                                 {"a": log("a", state="complete")}, NOW)
    self.assertEqual(sum(len(v) for v in out.values()), 0)

  def test_late_finish_heartbeat_corrects_closed_log_entry(self):
    closed = log("a", state="failed", terminal_source="auto-close",
                 error="superseded — no clean finish")
    late = hb("a", state="complete", terminal_source="finish",
              finished_at="2026-09-24T12:30:00")
    out = ops_rules.classify_ops([late], {"a": closed}, NOW)
    self.assertEqual(out["failed"] + out["abandoned"], [])

  def test_otherwise_log_copy_wins(self):
    logged = log("a", state="failed", terminal_source="close",
                 resolved_by="b")
    out = ops_rules.classify_ops([hb("a", state="failed")], {"a": logged}, NOW)
    self.assertEqual(out["failed"], [])


class Failed(unittest.TestCase):
  def test_superseded_by_later_complete_same_family(self):
    active = [hb("f", state="failed", finished_at="2026-09-17T16:52:40",
                 op_type="presort_execute"),
              hb("ok", state="complete", finished_at="2026-09-17T16:53:22",
                 op_type="presort_execute")]
    out = ops_rules.classify_ops(active, {}, NOW)
    self.assertEqual(out["failed"], [])

  def test_newer_failure_supersedes_older(self):
    active = [hb("f1", state="failed", finished_at="2026-09-20T10:00:00"),
              hb("f2", state="failed", finished_at="2026-09-20T11:00:00")]
    self.assertEqual(ids(ops_rules.classify_ops(active, {}, NOW)["failed"]), ["f2"])

  def test_running_cannot_supersede(self):
    active = [hb("f", state="failed", finished_at="2026-09-20T10:00:00"),
              hb("r", state="running", started_at="2026-09-24T17:00:00")]
    self.assertEqual(ids(ops_rules.classify_ops(active, {}, NOW)["failed"]), ["f"])

  def test_other_family_does_not_supersede(self):
    active = [hb("f", state="failed", finished_at="2026-09-20T10:00:00"),
              hb("c", state="complete", finished_at="2026-09-21T10:00:00",
                 subject="/Volumes/LaCie")]
    self.assertEqual(ids(ops_rules.classify_ops(active, {}, NOW)["failed"]), ["f"])

  def test_resolution_note_resolves(self):
    entry = log("f", state="failed", terminal_source="close",
                resolution_note="verified complete")
    self.assertEqual(ops_rules.classify_ops([], {"f": entry}, NOW)["failed"], [])

  def test_unlogged_terminal_heartbeat_without_finished_at_uses_started_at(self):
    failed = hb("f", state="failed", started_at="2026-09-20T10:00:00",
                finished_at=None)
    older = log("c", state="complete", finished_at="2026-09-19T10:00:00")
    self.assertEqual(ids(ops_rules.classify_ops([failed], {"c": older}, NOW)["failed"]),
                     ["f"])

  def test_paused_is_not_reported_but_supersedes(self):
    active = [hb("f", state="failed", finished_at="2026-09-20T10:00:00"),
              hb("p", state="paused", finished_at="2026-09-20T11:00:00")]
    out = ops_rules.classify_ops(active, {}, NOW)
    self.assertEqual(sum(len(v) for v in out.values()), 0)


class Abandoned(unittest.TestCase):
  def test_older_than_24h(self):
    out = ops_rules.classify_ops([hb("r", last_beat="2026-09-23T17:00:00",
                                     stale=True)], {}, NOW)
    self.assertEqual(ids(out["abandoned"]), ["r"])

  def test_missing_last_beat(self):
    out = ops_rules.classify_ops([hb("r", last_beat=None, stale=True)], {}, NOW)
    self.assertEqual(ids(out["abandoned"]), ["r"])

  def test_newer_same_family_run_exists(self):
    active = [hb("old", started_at="2026-09-24T10:00:00",
                 last_beat="2026-09-24T17:59:00"),
              hb("new", started_at="2026-09-24T11:00:00",
                 last_beat="2026-09-24T17:59:30")]
    self.assertEqual(ids(ops_rules.classify_ops(active, {}, NOW)["abandoned"]), ["old"])

  def test_sweep_auto_close_is_abandoned_not_failed(self):
    closed = log("a", state="failed", terminal_source="auto-close",
                 error="superseded — no clean finish",
                 finished_at=None, logged_at="2026-09-25T09:00:00")
    out = ops_rules.classify_ops([], {"a": closed}, datetime(2026, 9, 25, 10))
    self.assertEqual(ids(out["abandoned"]), ["a"])
    self.assertEqual(out["failed"], [])

  def test_auto_close_dropped_once_later_same_family_terminal_exists(self):
    closed = log("a", state="failed", terminal_source="auto-close",
                 error="superseded — no clean finish",
                 finished_at=None, logged_at="2026-09-25T09:00:00")
    later = log("b", state="complete", finished_at="2026-09-25T09:30:00",
                logged_at="2026-09-25T09:31:00")
    out = ops_rules.classify_ops([], {"a": closed, "b": later},
                                 datetime(2026, 9, 25, 10))
    self.assertEqual(out["abandoned"], [])

  def test_legacy_checkin_scan_is_stalled_not_abandoned(self):
    legacy = hb("legacy_digitize-station_GW-D09", op_type="checkin_scan",
                subject="GW-D09", started_at=None,
                last_beat="2026-09-20T10:00:00", stale=True)
    out = ops_rules.classify_ops([legacy], {}, NOW)
    self.assertEqual(ids(out["stalled"]), ["legacy_digitize-station_GW-D09"])
    self.assertEqual(out["abandoned"], [])


class StalledAndRunning(unittest.TestCase):
  def test_synthetic_unreadable_is_stalled(self):
    synth = hb("x", op_type=None, subject=None, station=None, started_at=None,
               last_beat=None, stale=True,
               subject_label="unreadable heartbeat — inspect")
    out = ops_rules.classify_ops([synth], {}, NOW)
    self.assertEqual(ids(out["stalled"]), ["x"])

  def test_sparse_real_heartbeat_not_mistaken_for_synthetic(self):
    sparse = hb("s", op_type=None, last_beat=None, stale=True,
                subject_label=None)
    out = ops_rules.classify_ops([sparse], {}, NOW)
    self.assertEqual(ids(out["abandoned"]), ["s"])

  def test_fresh_running_is_running(self):
    out = ops_rules.classify_ops([hb("r")], {}, NOW)
    self.assertEqual(ids(out["running"]), ["r"])

  def test_stale_but_recent_is_stalled(self):
    out = ops_rules.classify_ops([hb("r", last_beat="2026-09-24T17:00:00",
                                     stale=True)], {}, NOW)
    self.assertEqual(ids(out["stalled"]), ["r"])


if __name__ == "__main__":
  unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd $WT && python3 -m unittest tests.test_storage_status_ops_rules 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'ops_rules'`.

- [ ] **Step 3: Implement** — create `mcp-server/ops_rules.py`:

```python
"""ops_sweep's resolution rules (summarize.py steps 3, 5, 6) applied in
memory at read time, WITHOUT any of the sweep's writes. The sweep isn't
loaded on this laptop (hub decision parked), so without this the tool
would announce alarms nothing can ever clear. Mirrors the sweep exactly
except one ruling (2026-09-24): the sweep's own step-6 auto-closures are
reported as abandoned (warn), not failed, so the answer doesn't flip the
moment a hub starts sweeping."""
import datetime

import _paths  # noqa: F401
import summarize  # noqa: E402

TERMINAL_STATES = ("complete", "failed", "paused")
SYNTHETIC_LABEL = "unreadable heartbeat — inspect"
AUTO_CLOSE_ERROR = "superseded — no clean finish"


def _parse(value):
  try:
    return datetime.datetime.fromisoformat(value) if value else None
  except (TypeError, ValueError):
    return None


def is_synthetic(entry):
  return (entry.get("subject_label") == SYNTHETIC_LABEL
          and entry.get("station") is None and entry.get("started_at") is None)


def is_legacy(entry):
  return str(entry.get("op_id") or "").startswith("legacy_")


def is_auto_close(entry):
  return (entry.get("terminal_source") == "auto-close"
          and entry.get("error") == AUTO_CLOSE_ERROR)


def build_pool(active_ops, ops_log):
  """Sweep step 3: one entry per op_id across heartbeats and the log."""
  pool = {op_id: dict(e, _from="log") for op_id, e in (ops_log or {}).items()}
  for heartbeat in active_ops or []:
    op_id = heartbeat.get("op_id")
    logged = pool.get(op_id)
    if logged is None:
      pool[op_id] = dict(heartbeat, _from="heartbeat")
    elif logged.get("terminal_source") == "finish":
      continue  # dead weight: the log already has the real finish
    elif heartbeat.get("terminal_source") == "finish":
      # finish always outranks close; resolution fields stay from the log
      logged["state"] = heartbeat.get("state")
      logged["terminal_source"] = "finish"
  return pool


def entry_time(entry):
  value = entry.get("finished_at") or entry.get("logged_at")
  if value:
    return value
  # An unlogged terminal heartbeat would get logged_at=now from the sweep
  # before step 5; "" would let anything older supersede it.
  if entry.get("_from") == "heartbeat" and entry.get("state") in TERMINAL_STATES:
    return entry.get("started_at") or ""
  return ""


def _superseded(entry, pool):
  mine = entry_time(entry)
  for other in pool.values():
    if other.get("op_id") == entry.get("op_id"):
      continue
    if not summarize._same_run_family(entry, other):
      continue
    theirs = entry_time(other)
    if theirs and theirs > mine:
      return True
  return False


def _is_abandoned(entry, candidates, now):
  last_beat = _parse(entry.get("last_beat"))
  if last_beat is None:
    return True
  if (now - last_beat).total_seconds() > summarize.ABANDON_S:
    return True
  return summarize._find_newer_match(entry, candidates, entry.get("op_id")) is not None


def classify_ops(active_ops, ops_log, now):
  pool = build_pool(active_ops, ops_log)
  candidates = list(pool.values())
  out = {"failed": [], "abandoned": [], "stalled": [], "running": []}
  for entry in candidates:
    state = entry.get("state")
    if state == "failed":
      if summarize.is_resolved(entry) or _superseded(entry, pool):
        continue
      out["abandoned" if is_auto_close(entry) else "failed"].append(entry)
    elif state == "running":
      if is_synthetic(entry):
        out["stalled"].append(entry)
      elif not is_legacy(entry) and _is_abandoned(entry, candidates, now):
        out["abandoned"].append(entry)
      elif entry.get("stale"):
        out["stalled"].append(entry)
      else:
        out["running"].append(entry)
  return out
```

- [ ] **Step 4: Run tests**

Run: `cd $WT && python3 -m unittest tests.test_storage_status_ops_rules -v 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd $WT && git add mcp-server/ops_rules.py tests/test_storage_status_ops_rules.py && git commit -m "feat(storage-status): apply sweep resolution rules in memory"
```

---

### Task 4: `sections.py` — the four section dicts

**Files:**
- Create: `mcp-server/sections.py`
- Test: `tests/test_storage_status_sections.py`

**Interfaces:**
- Consumes: snapshot source dicts `{"value", "ok", "error", "as_of"}` (Task 1); `ops_rules.classify_ops`, `ops_rules.entry_time` (Task 3).
- Produces: `capacity_section(src)`, `backups_section(src, today: date)`, `lanes_section(src)`, `ops_section(active_src, log_src, recent_src, drives_src, now)` — each returns a section dict per the spec's Output shape. Ops item dicts are `{"op_id", "op_type", "time", "started_at"}`; drive warnings `{"label", "health", "note"}`.
- Produces: constants `CAP_WARN = 85`, `CAP_CRIT = 95`, `BACKUP_STALE_DAYS = 30`, `EXPECTED_MOUNTS`, and `worst(levels) -> str`.
- Produces: `ops_section(...)["unavailable_sources"]` — list of keys among `"active_ops"`, `"ops_log"`, `"drives"`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_storage_status_sections.py`:

```python
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))

import sections  # noqa: E402

NOW = datetime(2026, 9, 24, 18, 0, 0)


def src(value, ok=True, error=None, as_of="2026-09-24T18:00:00"):
  return {"value": value, "ok": ok, "error": error, "as_of": as_of}


def vol(name, pct):
  return {"name": name, "mounted": True, "used_bytes": pct, "size_bytes": 100,
          "pct": pct}


EXPECTED_OK = [vol("Projects-Syno", 10), vol("_Inbox", 1)]


class Capacity(unittest.TestCase):
  def level_of(self, pct):
    sec = sections.capacity_section(src(EXPECTED_OK + [vol("X", pct)]))
    return [i for i in sec["items"] if i["name"] == "X"][0]["level"]

  def test_threshold_edges(self):
    self.assertEqual(self.level_of(84), "ok")
    self.assertEqual(self.level_of(85), "warn")
    self.assertEqual(self.level_of(94), "warn")
    self.assertEqual(self.level_of(95), "critical")

  def test_missing_expected_mount(self):
    sec = sections.capacity_section(src([vol("Projects-Syno", 10),
                                         {"name": "_Inbox", "mounted": False}]))
    self.assertEqual(sec["missing"], ["_Inbox"])
    self.assertEqual(sec["status"], "warn")

  def test_timed_out_expected_mount_is_not_also_missing(self):
    sec = sections.capacity_section(src([vol("_Inbox", 1),
                                         {"name": "Projects-Syno", "timed_out": True}]))
    self.assertEqual(sec["missing"], [])
    self.assertEqual(sec["status"], "warn")

  def test_errored_volume_is_warn(self):
    sec = sections.capacity_section(src(EXPECTED_OK + [{"name": "Z", "error": "OSError: EIO"}]))
    self.assertEqual(sec["status"], "warn")

  def test_unmounted_non_expected_entry_ignored(self):
    sec = sections.capacity_section(src(EXPECTED_OK + [{"name": "Macintosh HD", "mounted": False}]))
    self.assertEqual(sec["status"], "ok")
    self.assertNotIn("Macintosh HD", [i["name"] for i in sec["items"]])

  def test_source_failure_is_unavailable(self):
    sec = sections.capacity_section(src(None, ok=False, error="timed out"))
    self.assertEqual(sec["status"], "unavailable")
    self.assertTrue(sec["reason"])


class Backups(unittest.TestCase):
  def mirror(self, day, status="active"):
    return {"dest": "Projects-Syno (13-folder core)", "mirror_target": "Vault",
            "method": "HyperBackup-local", "last_verified_sync": day,
            "status": status}

  def test_age_edges(self):
    today = date(2026, 9, 24)
    ok = sections.backups_section(src([self.mirror("2026-08-25")]), today)
    self.assertEqual((ok["items"][0]["age_days"], ok["status"]), (30, "ok"))
    warn = sections.backups_section(src([self.mirror("2026-08-24")]), today)
    self.assertEqual((warn["items"][0]["age_days"], warn["status"]), (31, "warn"))

  def test_non_active_and_unparseable_warn(self):
    today = date(2026, 9, 24)
    for m in (self.mirror("2026-09-20", status="paused"), self.mirror("soon")):
      self.assertEqual(sections.backups_section(src([m]), today)["status"], "warn")

  def test_empty_is_ok_with_reason(self):
    sec = sections.backups_section(src([]), date(2026, 9, 24))
    self.assertEqual((sec["status"], sec["reason"]), ("ok", "no mirrors configured"))
    self.assertEqual(sec["source"], "manual")


class Lanes(unittest.TestCase):
  def test_open_means_has_planned_rows(self):
    value = {
      "letter-normalize": {"total": 67, "by_status": {"planned": 66, "executed": 1},
                           "bytes_planned": 10, "bytes_verified": 0},
      "small-folders-triage": {"total": 4, "by_status": {"executed": 3,
                               "reviewed-no-action": 1},
                               "bytes_planned": 5, "bytes_verified": 0},
      "PILOT_Docs": {"total": 1, "by_status": {"verified": 1},
                     "bytes_planned": 2, "bytes_verified": 2},
    }
    sec = sections.lanes_section(src(value))
    open_lanes = sorted(i["lane"] for i in sec["items"] if i["open"])
    self.assertEqual(open_lanes, ["letter-normalize"])
    self.assertEqual(sec["status"], "warn")
    docs = [i for i in sec["items"] if i["lane"] == "PILOT_Docs"][0]
    self.assertEqual((docs["done"], docs["bytes_done"]), (1, 2))


class Ops(unittest.TestCase):
  def drive(self, label, health, notes=""):
    return {"drive_id": "GW-D02", "label": label, "health": health, "notes": notes}

  def test_drive_warning_note_cut(self):
    note = ("warn: cmd-timeout(188)=42 raw over 1866 power-on hrs; reallocated "
            "0 -- bridge-related")
    sec = sections.ops_section(src([]), src({}), src([]),
                               src([self.drive("LaCie", "warn", note),
                                    self.drive("Scratch", ""),
                                    self.drive("Evo", "ok")]), NOW)
    self.assertEqual(sec["drive_warnings"], [{
      "label": "LaCie", "health": "warn",
      "note": "warn: cmd-timeout(188)=42 raw over 1866 power-on hrs"}])
    self.assertEqual(sec["status"], "warn")

  def test_note_keeps_version_dots(self):
    sec = sections.ops_section(src([]), src({}), src([]),
                               src([self.drive("A", "warn", "firmware v1.2 flagged")]), NOW)
    self.assertEqual(sec["drive_warnings"][0]["note"], "firmware v1.2 flagged")

  def test_sub_source_failure_is_warn_and_listed(self):
    sec = sections.ops_section(src(None, ok=False, error="timed out"), src({}),
                               src([]), src([]), NOW)
    self.assertEqual(sec["status"], "warn")
    self.assertEqual(sec["unavailable_sources"], ["active_ops"])
    self.assertTrue(sec["reason"])

  def test_all_three_fail_is_unavailable(self):
    bad = src(None, ok=False, error="timed out")
    sec = sections.ops_section(bad, bad, src([]), bad, NOW)
    self.assertEqual(sec["status"], "unavailable")

  def test_failed_op_is_critical(self):
    failed = {"op_id": "f", "op_type": "presort_execute", "state": "failed",
              "subject": "/Volumes/Projects-Syno", "target": None,
              "finished_at": "2026-09-20T12:55:24", "started_at": "2026-09-20T12:50:00",
              "subject_label": "-volumes-projects-syno", "station": "mini",
              "resolved_by": None, "resolution_note": None}
    sec = sections.ops_section(src([failed]), src({}), src([]), src([]), NOW)
    self.assertEqual(sec["status"], "critical")
    self.assertEqual(sec["failed"], [{"op_id": "f", "op_type": "presort_execute",
                                      "time": "2026-09-20T12:55:24",
                                      "started_at": "2026-09-20T12:50:00"}])


if __name__ == "__main__":
  unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd $WT && python3 -m unittest tests.test_storage_status_sections 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'sections'`.

- [ ] **Step 3: Implement** — create `mcp-server/sections.py`:

```python
"""Pure: turn snapshot sources into the four section dicts. No I/O."""
import datetime

import ops_rules

CAP_WARN = 85
CAP_CRIT = 95
BACKUP_STALE_DAYS = 30
# mount name -> spoken name
EXPECTED_MOUNTS = {"Projects-Syno": "Projects-Syno", "_Inbox": "the Inbox share"}
LEVELS = ("ok", "warn", "critical")
OPS_SUB_SOURCES = ("active_ops", "ops_log", "drives")


def worst(levels):
  return max(levels, key=LEVELS.index, default="ok")


def _unavailable(src, source, what, **extra):
  section = {"status": "unavailable", "as_of": src.get("as_of"), "source": source,
             "reason": "couldn't read {} ({})".format(what, src.get("error"))}
  section.update(extra)
  return section


def _cap_level(pct):
  if pct >= CAP_CRIT:
    return "critical"
  return "warn" if pct >= CAP_WARN else "ok"


def capacity_section(src):
  if not src["ok"]:
    return _unavailable(src, "live", "disk capacity", items=[], missing=[])
  items, levels, mounted, unresponsive = [], [], set(), set()
  for v in src["value"]:
    name = v["name"]
    if v.get("timed_out"):
      items.append({"name": name, "timed_out": True, "level": "warn"})
      unresponsive.add(name)
    elif "error" in v:
      items.append({"name": name, "error": v["error"], "level": "warn"})
      unresponsive.add(name)
    elif v.get("mounted"):
      items.append({"name": name, "used_bytes": v["used_bytes"],
                    "size_bytes": v["size_bytes"], "pct": v["pct"],
                    "level": _cap_level(v["pct"])})
      mounted.add(name)
    else:
      continue
    levels.append(items[-1]["level"])
  missing = [n for n in EXPECTED_MOUNTS if n not in mounted and n not in unresponsive]
  if missing:
    levels.append("warn")
  return {"status": worst(levels), "as_of": src["as_of"], "source": "live",
          "items": items, "missing": missing}


def backups_section(src, today):
  if not src["ok"]:
    return _unavailable(src, "manual", "the backup list", items=[])
  if not src["value"]:
    return {"status": "ok", "as_of": src["as_of"], "source": "manual",
            "reason": "no mirrors configured", "items": []}
  items = []
  for m in src["value"]:
    try:
      age = (today - datetime.date.fromisoformat(m.get("last_verified_sync") or "")).days
    except ValueError:
      age = None
    bad = age is None or age > BACKUP_STALE_DAYS or m.get("status") != "active"
    items.append({"dest": m.get("dest"), "target": m.get("mirror_target"),
                  "method": m.get("method"), "last_verified": m.get("last_verified_sync"),
                  "status": m.get("status"), "age_days": age,
                  "level": "warn" if bad else "ok"})
  return {"status": worst(i["level"] for i in items), "as_of": src["as_of"],
          "source": "manual", "items": items}


def lanes_section(src):
  if not src["ok"]:
    return _unavailable(src, "live", "the migration ledger", items=[])
  items = []
  for lane, data in sorted(src["value"].items()):
    by_status = data["by_status"]
    items.append({"lane": lane,
                  "done": by_status.get("verified", 0) + by_status.get("cleared", 0),
                  "total": data["total"], "bytes_done": data["bytes_verified"],
                  "bytes_planned": data["bytes_planned"],
                  "open": by_status.get("planned", 0) > 0})
  status = "warn" if any(i["open"] for i in items) else "ok"
  return {"status": status, "as_of": src["as_of"], "source": "live", "items": items}


def _cut_note(notes):
  note = notes or ""
  for sep in ("; ", " -- ", ". "):
    note = note.split(sep, 1)[0]
  return note.strip()


def _op_item(entry):
  return {"op_id": entry.get("op_id"), "op_type": entry.get("op_type"),
          "time": ops_rules.entry_time(entry) or entry.get("started_at"),
          "started_at": entry.get("started_at")}


def ops_section(active_src, log_src, recent_src, drives_src, now):
  by_key = {"active_ops": active_src, "ops_log": log_src, "drives": drives_src}
  unavailable = [k for k in OPS_SUB_SOURCES if not by_key[k]["ok"]]
  if len(unavailable) == len(OPS_SUB_SOURCES):
    return _unavailable(active_src, "live", "operations or drive health",
                        unavailable_sources=unavailable)
  groups = ops_rules.classify_ops(active_src["value"] or [],
                                  log_src["value"] or {}, now)
  drive_warnings = [
    {"label": d.get("label"), "health": d.get("health"),
     "note": _cut_note(d.get("notes"))}
    for d in (drives_src["value"] or [])
    if d.get("health") and d.get("health") != "ok"]
  levels = []
  if groups["failed"]:
    levels.append("critical")
  if groups["abandoned"] or groups["stalled"] or drive_warnings or unavailable:
    levels.append("warn")
  section = {"status": worst(levels), "as_of": active_src["as_of"], "source": "live",
             "failed": [_op_item(e) for e in groups["failed"]],
             "abandoned": [_op_item(e) for e in groups["abandoned"]],
             "stalled": [_op_item(e) for e in groups["stalled"]],
             "running": [_op_item(e) for e in groups["running"]],
             "recent": [_op_item(e) for e in (recent_src["value"] or [])],
             "recent_as_of": recent_src["as_of"],
             "drive_warnings": drive_warnings,
             "unavailable_sources": unavailable}
  reasons = ["couldn't read {} ({})".format(k, by_key[k]["error"]) for k in unavailable]
  if not recent_src["ok"]:
    reasons.append("couldn't read recent operations ({})".format(recent_src["error"]))
  if reasons:
    section["reason"] = "; ".join(reasons)
  return section
```

- [ ] **Step 4: Run tests**

Run: `cd $WT && python3 -m unittest tests.test_storage_status_sections -v 2>&1 | tail -5`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd $WT && git add mcp-server/sections.py tests/test_storage_status_sections.py && git commit -m "feat(storage-status): capacity, backups, lanes and ops section builders"
```

---

### Task 5: `headline.py` + `summary.py` — spoken headline and assembly

**Files:**
- Create: `mcp-server/headline.py`, `mcp-server/summary.py`
- Test: `tests/test_storage_status_summary.py`

**Interfaces:**
- Consumes: section dicts from Task 4; `sections.EXPECTED_MOUNTS`.
- Produces: `headline.build_headline(sections: dict, now: datetime) -> str`.
- Produces: `summary.build_summary(snapshot: dict, now: datetime) -> {"ok": bool, "headline": str, "generated_at": str, "sections": dict}` (Task 6's server calls this).

- [ ] **Step 1: Write the failing tests** — create `tests/test_storage_status_summary.py`:

```python
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp-server"))

import headline  # noqa: E402
import summary  # noqa: E402

NOW = datetime(2026, 9, 24, 18, 0, 0)


def src(value, ok=True, error=None, as_of="2026-09-24T18:00:00"):
  return {"value": value, "ok": ok, "error": error, "as_of": as_of}


def vol(name, pct):
  return {"name": name, "mounted": True, "used_bytes": 1, "size_bytes": 2, "pct": pct}


def hb(op_id, state, op_type="presort_plan", started_at="2026-09-24T17:00:00",
       last_beat="2026-09-24T17:59:00", finished_at=None, stale=False,
       subject="/Volumes/Projects-Syno"):
  return {"op_id": op_id, "op_type": op_type, "subject_kind": "path",
          "subject": subject, "target": None, "subject_label": "-volumes-x",
          "batch": None, "station": "mini", "started_at": started_at,
          "last_beat": last_beat, "phase": None, "progress": None, "bytes": None,
          "state": state, "terminal_source": "finish" if state != "running" else None,
          "error": None, "finished_at": finished_at, "stale": stale,
          "resolved_by": None, "resolution_note": None}


def healthy_sources():
  return {
    "volumes": src([vol("Projects-Syno", 40), vol("_Inbox", 1)]),
    "lanes": src({"PILOT": {"total": 1, "by_status": {"verified": 1},
                            "bytes_planned": 1, "bytes_verified": 1}}),
    "mirrors": src([{"dest": "Projects-Syno", "mirror_target": "Vault",
                     "method": "HyperBackup-local",
                     "last_verified_sync": "2026-09-20", "status": "active"}]),
    "drives": src([]),
    "active_ops": src([]),
    "ops_log": src({}),
    "recent_ops": src([]),
  }


def run(sources, now=NOW):
  return summary.build_summary({"collected_at": now.isoformat(), "sources": sources}, now)


class Assembly(unittest.TestCase):
  def test_all_healthy(self):
    r = run(healthy_sources())
    self.assertTrue(r["ok"])
    self.assertEqual(r["headline"], "Storage is healthy.")
    self.assertEqual(set(r["sections"]), {"capacity", "backups", "lanes", "ops"})

  def test_all_unavailable(self):
    bad = src(None, ok=False, error="timed out")
    r = run({k: bad for k in healthy_sources()})
    self.assertFalse(r["ok"])
    self.assertEqual(r["headline"], "I couldn't read any storage status right now.")


class Clauses(unittest.TestCase):
  def test_capacity_single_and_plural(self):
    s = healthy_sources()
    s["volumes"] = src([vol("Projects-Syno", 96), vol("_Inbox", 1)])
    self.assertEqual(run(s)["headline"], "Projects-Syno is at 96 percent.")
    s["volumes"] = src([vol("Projects-Syno", 96), vol("_Inbox", 97)])
    self.assertEqual(run(s)["headline"],
                     "Projects-Syno and the Inbox share are over 95 percent.")
    s["volumes"] = src([vol("Projects-Syno", 90), vol("_Inbox", 86), vol("LaCie", 88)])
    self.assertEqual(run(s)["headline"],
                     "Projects-Syno, the Inbox share and LaCie are over 85 percent.")
    s["volumes"] = src([vol("Projects-Syno", 10), vol("_Inbox", 97)])
    self.assertEqual(run(s)["headline"], "The Inbox share is at 97 percent.")

  def test_failed_wording_and_relative_day(self):
    s = healthy_sources()
    s["active_ops"] = src([hb("f", "failed", op_type="presort_execute",
                              finished_at="2026-09-20T12:55:24")])
    self.assertEqual(run(s)["headline"], "A presort run failed on the 20th.")
    s["active_ops"] = src([
      hb("f1", "failed", op_type="index_build", finished_at="2026-09-24T09:00:00",
         subject="a"),
      hb("f2", "failed", op_type="index_build", finished_at="2026-09-23T09:00:00",
         subject="b")])
    self.assertEqual(run(s)["headline"], "Two index builds failed, most recently today.")

  def test_relative_day_forms(self):
    self.assertEqual(headline.relative_day("2026-09-23T10:00:00", NOW), "yesterday")
    self.assertEqual(headline.relative_day("2026-09-01T10:00:00", NOW), "on the 1st")
    self.assertEqual(headline.relative_day("2026-09-22T10:00:00", NOW), "on the 22nd")
    self.assertEqual(headline.relative_day("2026-09-13T10:00:00", NOW), "on the 13th")
    self.assertEqual(headline.relative_day("2026-08-03T10:00:00", NOW), "on August 3rd")

  def test_mixed_op_types_say_operations(self):
    s = healthy_sources()
    s["active_ops"] = src([
      hb("f1", "failed", op_type="index_build", finished_at="2026-09-24T09:00:00", subject="a"),
      hb("f2", "failed", op_type="hash_compare", finished_at="2026-09-24T08:00:00", subject="b")])
    self.assertEqual(run(s)["headline"], "Two operations failed, most recently today.")

  def test_backup_wording(self):
    s = healthy_sources()
    s["mirrors"] = src([
      {"dest": "A", "mirror_target": "x", "method": "m", "last_verified_sync": "2026-08-05", "status": "active"},
      {"dest": "B", "mirror_target": "x", "method": "m", "last_verified_sync": "2026-09-01", "status": "active"}])
    self.assertEqual(run(s)["headline"], "The oldest backup check is 50 days old.")
    s["mirrors"]["value"][1]["status"] = "paused"
    self.assertEqual(run(s)["headline"], "A backup mirror needs checking.")

  def test_drive_and_lane_wording(self):
    s = healthy_sources()
    s["drives"] = src([{"label": "4TB_Scratch", "health": "warn", "notes": ""}])
    self.assertEqual(run(s)["headline"], "Drive 4TB_Scratch has a health warning.")
    s["drives"] = src([{"label": "A", "health": "warn", "notes": ""},
                       {"label": "B", "health": "fail", "notes": ""}])
    self.assertEqual(run(s)["headline"], "Two drives have health warnings.")
    s = healthy_sources()
    s["lanes"] = src({"a": {"total": 2, "by_status": {"planned": 2}, "bytes_planned": 0, "bytes_verified": 0}})
    self.assertEqual(run(s)["headline"], "One migration lane is still open.")

  def test_stalled_wording(self):
    s = healthy_sources()
    s["active_ops"] = src([hb("r", "running", last_beat="2026-09-24T17:00:00", stale=True)])
    self.assertEqual(run(s)["headline"], "A running operation has stopped reporting.")

  def test_numbers_over_ten_are_digits(self):
    s = healthy_sources()
    s["active_ops"] = src([hb("r{}".format(i), "running", last_beat=None,
                              stale=True, subject=str(i)) for i in range(12)])
    self.assertEqual(run(s)["headline"], "12 old presort runs never finished cleanly.")


class Ordering(unittest.TestCase):
  def test_priority_and_three_clause_cap(self):
    s = healthy_sources()
    s["volumes"] = src([vol("Projects-Syno", 90), vol("_Inbox", 1)])
    s["drives"] = src([{"label": "LaCie", "health": "warn", "notes": ""}])
    s["mirrors"]["value"][0]["last_verified_sync"] = "2026-08-05"
    s["lanes"] = src({"a": {"total": 2, "by_status": {"planned": 2}, "bytes_planned": 0, "bytes_verified": 0}})
    s["active_ops"] = src([hb("r", "running", last_beat="2026-09-20T10:00:00", stale=True)])
    self.assertEqual(run(s)["headline"],
                     "An old presort run never finished cleanly. "
                     "Drive LaCie has a health warning. "
                     "The oldest backup check is 50 days old.")

  def test_reach_clause_last_with_two_problems(self):
    s = healthy_sources()
    s["volumes"] = src([vol("Projects-Syno", 96)])  # _Inbox missing
    s["drives"] = src([{"label": "LaCie", "health": "warn", "notes": ""}])
    s["mirrors"]["value"][0]["last_verified_sync"] = "2026-08-05"
    self.assertEqual(run(s)["headline"],
                     "Projects-Syno is at 96 percent. "
                     "Drive LaCie has a health warning. "
                     "The Inbox share isn't mounted.")

  def test_reach_groups_merge_into_one_sentence(self):
    s = healthy_sources()
    s["volumes"] = src([{"name": "LaCie", "timed_out": True}])  # both expected missing
    s["active_ops"] = src(None, ok=False, error="timed out")
    s["ops_log"] = src(None, ok=False, error="ValueError: bad")
    s["lanes"] = src({"a": {"total": 2, "by_status": {"planned": 2}, "bytes_planned": 0, "bytes_verified": 0}})
    self.assertEqual(run(s)["headline"],
                     "One migration lane is still open. "
                     "Projects-Syno and the Inbox share aren't mounted, "
                     "and LaCie didn't respond, "
                     "and I couldn't read live operations and the operations log.")

  def test_reach_only_gets_prefix(self):
    s = healthy_sources()
    s["active_ops"] = src(None, ok=False, error="timed out")
    self.assertEqual(run(s)["headline"],
                     "Everything I could read looks fine, but I couldn't read live operations.")

  def test_unavailable_section_nouns(self):
    s = healthy_sources()
    s["mirrors"] = src(None, ok=False, error="x")
    s["lanes"] = src(None, ok=False, error="x")
    self.assertEqual(run(s)["headline"],
                     "Everything I could read looks fine, but I couldn't read "
                     "the backup list and the migration ledger.")

  def test_no_paths_or_ids_ever(self):
    s = healthy_sources()
    s["active_ops"] = src([hb("20260917T165240_mini_presort_execute_x", "failed",
                              op_type="presort_execute", finished_at="2026-09-17T16:52:40")])
    text = run(s)["headline"]
    for bad in ("/", "_mini_", "-volumes-"):
      self.assertNotIn(bad, text)


class TodayFixture(unittest.TestCase):
  """Reproduces the live state both reviewers derived by hand (2026-09-24)."""

  def test_today(self):
    s = healthy_sources()
    s["volumes"] = src([vol("Projects-Syno", 90), vol("_Inbox", 1), vol("4TB_Scratch", 14)])
    s["mirrors"] = src([{"dest": d, "mirror_target": "x", "method": "m",
                         "last_verified_sync": "2026-08-05", "status": "active"}
                        for d in ("Projects-Syno", "Photos-Syno", "Apple Media-Syno")])
    s["drives"] = src([{"label": "LaCie", "health": "warn",
                        "notes": "warn: cmd-timeout(188)=42; reallocated 0 -- bridge"}])
    s["lanes"] = src({
      "letter-normalize": {"total": 67, "by_status": {"planned": 66, "executed": 1}, "bytes_planned": 1, "bytes_verified": 0},
      "PRESORT_Projects": {"total": 6, "by_status": {"planned": 6}, "bytes_planned": 0, "bytes_verified": 0},
      "small-folders-triage": {"total": 4, "by_status": {"executed": 3, "reviewed-no-action": 1}, "bytes_planned": 1, "bytes_verified": 0}})
    active = [
      hb("jw37", "failed", op_type="presort_execute", started_at="2026-09-17T16:50:00", finished_at="2026-09-17T16:52:40"),
      hb("ceqo", "failed", op_type="presort_execute", started_at="2026-09-17T16:51:00", finished_at="2026-09-17T16:53:13"),
      hb("ti0w", "complete", op_type="presort_execute", started_at="2026-09-17T16:53:00", finished_at="2026-09-17T16:53:22"),
      hb("l3pn", "failed", op_type="presort_execute", started_at="2026-09-20T12:50:00", finished_at="2026-09-20T12:55:24"),
      hb("lmdp", "complete", op_type="presort_execute", started_at="2026-09-20T12:56:00", finished_at="2026-09-20T12:58:33"),
      hb("uotk", "complete", op_type="presort_plan", started_at="2026-09-18T09:49:00", finished_at="2026-09-18T10:10:00"),
    ]
    for i, day in enumerate(("12", "15", "17", "17", "12")):
      active.append(hb("plan{}".format(i), "running", op_type="presort_plan",
                       started_at="2026-09-{}T09:00:00".format(day),
                       last_beat="2026-09-{}T10:00:00".format(day), stale=True))
    s["active_ops"] = src(active)
    r = run(s)
    self.assertEqual(r["sections"]["ops"]["failed"], [])
    self.assertEqual(len(r["sections"]["ops"]["abandoned"]), 5)
    self.assertEqual(r["headline"],
                     "Five old presort runs never finished cleanly. "
                     "Drive LaCie has a health warning. "
                     "The oldest backup check is 50 days old.")


if __name__ == "__main__":
  unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd $WT && python3 -m unittest tests.test_storage_status_summary 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'headline'`.

- [ ] **Step 3: Implement** — create `mcp-server/headline.py`:

```python
"""Pure: the spoken headline. Worst-first, at most three clauses; any
"can't reach" items merge into ONE sentence that always goes last, so an
unreachable NAS is never crowded out. Never speaks a path, op_id or
subject_label (new-style labels are slugged paths)."""
import datetime

import sections

WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
         7: "seven", 8: "eight", 9: "nine", 10: "ten"}
OP_NOUNS = {"presort_plan": "presort run", "presort_execute": "presort run",
            "index_build": "index build", "checkin_scan": "drive scan",
            "hash_compare": "hash compare"}
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
READ_NOUNS = {"capacity": "disk capacity", "backups": "the backup list",
              "lanes": "the migration ledger", "active_ops": "live operations",
              "ops_log": "the operations log", "drives": "drive health"}
SECTION_ORDER = ("capacity", "backups", "lanes")


def count(n):
  return WORDS.get(n, str(n))


def cap(text):
  return text[:1].upper() + text[1:]


def join_names(names):
  names = list(names)
  if len(names) == 1:
    return names[0]
  return "{} and {}".format(", ".join(names[:-1]), names[-1])


def ordinal(day):
  if 11 <= day % 100 <= 13:
    return "{}th".format(day)
  return "{}{}".format(day, {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th"))


def relative_day(iso, now):
  try:
    when = datetime.datetime.fromisoformat(iso).date()
  except (TypeError, ValueError):
    return "recently"
  delta = (now.date() - when).days
  if delta == 0:
    return "today"
  if delta == 1:
    return "yesterday"
  if (when.year, when.month) == (now.year, now.month):
    return "on the {}".format(ordinal(when.day))
  return "on {} {}".format(MONTHS[when.month - 1], ordinal(when.day))


def _noun(items):
  nouns = {OP_NOUNS.get(i.get("op_type"), "operation") for i in items}
  return nouns.pop() if len(nouns) == 1 else "operation"


def _plural(noun):
  return noun + "s"


def _article(phrase):
  return "An" if phrase[:1].lower() in "aeiou" else "A"


def spoken(name):
  # "_Inbox" read aloud is "underscore Inbox"
  return sections.EXPECTED_MOUNTS.get(name, name)


def _capacity_clause(items, level, threshold):
  hits = [i for i in items if i.get("level") == level and "pct" in i]
  if not hits:
    return None
  if len(hits) == 1:
    return cap("{} is at {} percent.".format(spoken(hits[0]["name"]), hits[0]["pct"]))
  return cap("{} are over {} percent.".format(
    join_names(spoken(i["name"]) for i in hits), threshold))


def _failed_clause(items, now):
  if not items:
    return None
  noun = _noun(items)
  latest = max(items, key=lambda i: i.get("time") or "")
  when = relative_day(latest.get("time") or latest.get("started_at"), now)
  if len(items) == 1:
    return "{} {} failed {}.".format(_article(noun), noun, when)
  return "{} {} failed, most recently {}.".format(cap(count(len(items))),
                                                 _plural(noun), when)


def _abandoned_clause(items):
  if not items:
    return None
  noun = _noun(items)
  if len(items) == 1:
    return "An old {} never finished cleanly.".format(noun)
  return "{} old {} never finished cleanly.".format(cap(count(len(items))), _plural(noun))


def _stalled_clause(items):
  if not items:
    return None
  if len(items) == 1:
    return "A running operation has stopped reporting."
  return "{} running operations have stopped reporting.".format(cap(count(len(items))))


def _drive_clause(warnings):
  if not warnings:
    return None
  if len(warnings) == 1:
    return "Drive {} has a health warning.".format(warnings[0]["label"])
  return "{} drives have health warnings.".format(cap(count(len(warnings))))


def _backup_clause(backups):
  items = [i for i in backups.get("items", []) if i["level"] == "warn"]
  if not items:
    return None
  if any(i["status"] != "active" or i["age_days"] is None for i in items):
    return "A backup mirror needs checking."
  return "The oldest backup check is {} days old.".format(max(i["age_days"] for i in items))


def _lanes_clause(lanes):
  n = sum(1 for i in lanes.get("items", []) if i["open"])
  if not n:
    return None
  if n == 1:
    return "One migration lane is still open."
  return "{} migration lanes are still open.".format(cap(count(n)))


def _problem_clauses(secs, now):
  capacity, ops = secs["capacity"], secs["ops"]
  cap_items = capacity.get("items", [])
  candidates = [
    _capacity_clause(cap_items, "critical", sections.CAP_CRIT),
    _failed_clause(ops.get("failed", []), now),
    _abandoned_clause(ops.get("abandoned", [])),
    _stalled_clause(ops.get("stalled", [])),
    _drive_clause(ops.get("drive_warnings", [])),
    _backup_clause(secs["backups"]),
    _capacity_clause(cap_items, "warn", sections.CAP_WARN),
    _lanes_clause(secs["lanes"]),
  ]
  return [c for c in candidates if c]


def _reach_sentence(secs):
  capacity = secs["capacity"]
  missing = [spoken(n) for n in capacity.get("missing", [])]
  unresponsive = [spoken(i["name"]) for i in capacity.get("items", [])
                  if i.get("timed_out") or "error" in i]
  unread = [READ_NOUNS[k] for k in SECTION_ORDER if secs[k]["status"] == "unavailable"]
  ops = secs["ops"]
  unread += [READ_NOUNS[k] for k in ops.get("unavailable_sources", [])]
  groups = []
  if missing:
    verb = "isn't" if len(missing) == 1 else "aren't"
    groups.append("{} {} mounted".format(join_names(missing), verb))
  if unresponsive:
    groups.append("{} didn't respond".format(join_names(unresponsive)))
  if unread:
    groups.append("I couldn't read {}".format(join_names(unread)))
  return ", and ".join(groups) if groups else None


def build_headline(secs, now):
  problems = _problem_clauses(secs, now)
  reach = _reach_sentence(secs)
  if reach and not problems:
    return "Everything I could read looks fine, but {}.".format(reach)
  if reach:
    return " ".join(problems[:2] + [cap(reach) + "."])
  if problems:
    return " ".join(problems[:3])
  return "Storage is healthy."
```

Create `mcp-server/summary.py`:

```python
"""Pure assembly: snapshot (from snapshot.collect) -> the tool's result."""
import headline
import sections


def build_summary(snapshot, now):
  src = snapshot["sources"]
  secs = {
    "capacity": sections.capacity_section(src["volumes"]),
    "backups": sections.backups_section(src["mirrors"], now.date()),
    "lanes": sections.lanes_section(src["lanes"]),
    "ops": sections.ops_section(src["active_ops"], src["ops_log"],
                                src["recent_ops"], src["drives"], now),
  }
  ok = any(s["status"] != "unavailable" for s in secs.values())
  text = (headline.build_headline(secs, now) if ok
          else "I couldn't read any storage status right now.")
  return {"ok": ok, "headline": text,
          "generated_at": now.isoformat(timespec="seconds"), "sections": secs}
```

- [ ] **Step 4: Run tests, including Task 2's tripwire**

Run: `cd $WT && python3 -m unittest tests.test_storage_status_summary tests.test_storage_status_snapshot -v 2>&1 | tail -6`
Expected: all pass, including `NoWriteTripwire.test_collect_and_summarize_never_write`.

- [ ] **Step 5: Commit**

```bash
cd $WT && git add mcp-server/headline.py mcp-server/summary.py tests/test_storage_status_summary.py && git commit -m "feat(storage-status): spoken headline and summary assembly"
```

---

### Task 6: MCP server wrapper + venv

**Files:**
- Create: `mcp-server/storage_status_server.py`, `mcp-server/requirements.txt`
- Modify: `.gitignore` (append `mcp-server/.venv/`)

**Interfaces:**
- Consumes: `snapshot.collect()`, `summary.build_summary(snapshot, now)`.
- Produces: MCP tool `get_storage_status` (async, no args) → the `build_summary` dict, or `{"ok": False, "headline": ..., "sections": {}}` on failure.

- [ ] **Step 1: Write the files**

`mcp-server/requirements.txt`:
```
mcp>=1.0.0,<2
```

Append to `.gitignore`:
```
# storage-status MCP server virtualenv (JARVIS), rebuilt from mcp-server/requirements.txt
mcp-server/.venv/
```

`mcp-server/storage_status_server.py`:
```python
"""MCP server exposing one read-only tool, get_storage_status, for JARVIS.
Design: jarvis repo, docs/superpowers/specs/2026-09-24-storage-status-mcp-server-design.md.

Named with a `get_` prefix because JARVIS's bridge (bridge/server.mjs,
decideTool()) only auto-approves tools whose names start with a read verb;
anything else is silently denied."""
import contextlib
import datetime
import sys
import traceback

import anyio
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("storage-status")

LOAD_FAILED = "The storage status tool couldn't load file-management's code."
FAILED = "The storage status tool hit an error."


def _collect_and_summarize():
  # stdout is the JSON-RPC channel; nothing on the read path prints there
  # today, this is belt-and-braces. The stdio transport wrapped
  # sys.stdout.buffer at startup, so swapping sys.stdout can't touch it.
  with contextlib.redirect_stdout(sys.stderr):
    try:
      import snapshot
      import summary
    except Exception:  # noqa: BLE001 — repo moved, upstream syntax error
      traceback.print_exc(file=sys.stderr)
      return {"ok": False, "headline": LOAD_FAILED, "sections": {}}
    return summary.build_summary(snapshot.collect(), datetime.datetime.now())


@mcp.tool()
async def get_storage_status() -> dict:
  """Read-only storage health for file-management: disk capacity, backup checks, migration lanes, and operations/drive health. Speak `headline` as your reply. Then show a compact panel: one `.hud-row` per section in this order — Capacity, Backups, Lanes, Ops — with the section name as `.hud-label`, its `status` as a `.hud-tag` (add `.hud-hot` when warn or critical), and one `.hud-sub` line naming the single worst item. Add a `.hud-dim` "as of" line to any section whose `as_of` is over a day old. Do not list every volume, lane, mirror or operation in the panel. Only when asked for detail, open a `markup` blade with a `.hud-bar` (`--v`) per volume and per lane and a row per mirror and operation."""
  try:
    # FastMCP runs sync tools on the event loop; a hung mount would block
    # pings for the full deadline without this.
    return await anyio.to_thread.run_sync(_collect_and_summarize)
  except Exception:  # noqa: BLE001 — never raise to the client
    traceback.print_exc(file=sys.stderr)
    return {"ok": False, "headline": FAILED, "sections": {}}


if __name__ == "__main__":
  mcp.run()
```

- [ ] **Step 2: STOP — confirm with Gardiner before installing packages.** Ask: "Create `mcp-server/.venv` in the file-management worktree and `pip install -r mcp-server/requirements.txt` (mcp <2, same pin as 2nd-brain)?" Proceed only on yes:

```bash
cd $WT && python3 -m venv mcp-server/.venv && mcp-server/.venv/bin/pip install -q -r mcp-server/requirements.txt && mcp-server/.venv/bin/python3 -c "import mcp, importlib.metadata as m; print(m.version('mcp'))"
```
Expected: a `1.x` version (1.30.0 at time of writing).

- [ ] **Step 3: Smoke-test the tool in-process**

```bash
cd $WT/mcp-server && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -c "
import asyncio, json, storage_status_server as s
tools = asyncio.run(s.mcp.list_tools())
print([t.name for t in tools])
r = asyncio.run(s.get_storage_status())
print(r['ok'], r['headline'])
print({k: v['status'] for k, v in r['sections'].items()})
json.dumps(r)
"
```
Expected: `['get_storage_status']`, `True <a headline>`, four section statuses, and no exception from `json.dumps`. (Ops will read an empty log here — `ops-log.jsonl` is untracked and absent from the worktree; that's Review Focus #4, expected.)

- [ ] **Step 4: Smoke-test the load-failure path**

```bash
cd /tmp && PYTHONDONTWRITEBYTECODE=1 $WT/mcp-server/.venv/bin/python3 -c "
import asyncio, sys
sys.path.insert(0, '$WT/mcp-server')
import storage_status_server as s
sys.modules['snapshot'] = None  # simulate an import failure
print(asyncio.run(s.get_storage_status()))
"
```
Expected: `{'ok': False, 'headline': "The storage status tool couldn't load file-management's code.", 'sections': {}}` and a traceback on stderr only.

- [ ] **Step 5: Commit**

```bash
cd $WT && git status --short && git add mcp-server/storage_status_server.py mcp-server/requirements.txt .gitignore && git commit -m "feat(storage-status): FastMCP server exposing get_storage_status"
```
Expected before commit: no `.venv` or `__pycache__` paths in `git status`.

---

### Task 7: Full suite, merge, register, live JARVIS test, deferred items

**Files:**
- Modify: `~/.claude.json` (one `mcpServers` entry — stop-and-confirm)
- Modify: file-management's deferred-items ledger (via the `deferred-item-ledger` skill)

- [ ] **Step 1: Full suite in the worktree**

Run: `cd $WT && python3 -m unittest discover tests 2>&1 | tail -3`
Expected: `OK`, test count = Task 0 baseline + the new tests.

- [ ] **Step 2: Whole-branch review** — run the final code review per the chosen execution method (subagent-driven's final reviewer, or `/review`). Fix findings before merging.

- [ ] **Step 3: STOP — confirm the merge with Gardiner**, then merge per `superpowers:finishing-a-development-branch` (merge to file-management `main` needs `ALLOW_MAIN_COMMIT=1` for the global pre-commit hook; run `ListAgents` first and ping any peer working in the file-management root checkout, per CLAUDE.md rule 3).

- [ ] **Step 4: Build the venv in the main checkout** (the registered server runs from the main checkout, which has the real untracked `ops-log.jsonl`; `.venv` is gitignored so it didn't come with the merge). Stop-and-confirm the install, same as Task 6 Step 2:

```bash
cd /Users/gardinerwelch/Documents/_Projects/file-management && python3 -m venv mcp-server/.venv && mcp-server/.venv/bin/pip install -q -r mcp-server/requirements.txt
```

- [ ] **Step 5: Record the real live result** (spec says the build records the actual result; the spec is LOCKED, so record it in the commit log here rather than editing the spec — editing it would re-trigger Gate 5):

```bash
cd /Users/gardinerwelch/Documents/_Projects/file-management/mcp-server && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3 -c "
import asyncio, storage_status_server as s
r = asyncio.run(s.get_storage_status())
print(r['headline']); print({k: v['status'] for k, v in r['sections'].items()})
"
```
Compare with the spec's expected headline ("Five old presort runs never finished cleanly. Drive LaCie has a health warning. The oldest backup check is 50 days old."). A difference is not automatically a bug — the live data may have moved — but explain every difference to Gardiner before continuing.

- [ ] **Step 6: STOP — confirm with Gardiner, then register in `~/.claude.json`.** This is a full read-modify-write of Claude Code's global state file; other sessions may write it concurrently. Back it up first, change only `mcpServers`, validate JSON after:

```bash
cp ~/.claude.json ~/.claude.json.bak-2026-09-24-storage-status && python3 - <<'EOF'
import json, pathlib
p = pathlib.Path.home() / ".claude.json"
cfg = json.loads(p.read_text())
root = "/Users/gardinerwelch/Documents/_Projects/file-management/mcp-server"
cfg.setdefault("mcpServers", {})["storage-status"] = {
  "type": "stdio",
  "command": root + "/.venv/bin/python3",
  "args": [root + "/storage_status_server.py"],
  "env": {"PYTHONDONTWRITEBYTECODE": "1"},
}
p.write_text(json.dumps(cfg, indent=2))
print(json.loads(p.read_text())["mcpServers"]["storage-status"])
EOF
```

- [ ] **Step 7: Live end-to-end through JARVIS** (required before "done" — the receipts-status bridge test caught a bug no review could). Restart the JARVIS bridge (it reads `~/.claude.json` once at start), then Gardiner asks aloud: "How's my storage?" Confirm: (a) a spoken answer matching Step 5's headline, (b) a compact four-row HUD panel (Capacity, Backups, Lanes, Ops) with status tags, (c) asking "show me the detail" opens a blade with per-volume and per-lane bars. If the tool is silently denied, check the name against `READ_VERB` in `jarvis/bridge/server.mjs` first.

- [ ] **Step 8: Log deferred items** — invoke the `deferred-item-ledger` skill for file-management with these five entries (from the spec's Out of scope): loading `com.gabba.fleet-sweep` / choosing the hub (Mac Mini session); write/action tools; live Hyper Backup / Snapshot Replication telemetry instead of `mirrors.csv`; `summarize.volume_fullness()` space-in-mount-name parse bug; `recent_operations()` ordering degrading after a bulk sweep.

- [ ] **Step 9: Update the JARVIS pattern memory** — add storage-status as the second instance in `jarvis-mcp-server-pattern.md` (the `in-memory sweep-rules` and `shared deadline` techniques are the new reusable parts).
