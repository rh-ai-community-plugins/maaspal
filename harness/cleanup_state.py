"""Small PVC-file read/write helpers backing the per-run auto-cleanup toggle.

Shared between harness/runner.py (writes the run's shared_state + its own
cleanup outcome; reads the live toggle right before deciding whether to run
task cleanup) and the API server (writes the toggle at launch/on flip; reads
the persisted shared_state + outcome to sync history and to perform a manual
"Clean Up Now" after the harness process has already exited).

Every function takes `results_dir` explicitly (the same directory the caller
already writes/reads -progress.json/-assertions.json/-config.json from)
rather than hardcoding a module-level constant, so tests can point it at a
tmp_path exactly like the existing harness/runner.py tests already do for
those other files, with no extra monkeypatching required here.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

# status: "pending" (not yet reached) | "cleaning" | "skipped" (toggle was off)
# | "done" | "failed"


def _path(results_dir: Path, run_id: str, suffix: str) -> Path:
    return results_dir / f"{run_id}-{suffix}"


def read_auto_cleanup_flag(results_dir: Path, run_id: str) -> bool:
    """Defaults to True (today's unconditional-cleanup behavior) if the flag
    file is missing or unreadable — a run that predates this feature, or a
    write that hasn't landed yet, must never silently skip cleanup."""
    try:
        data = json.loads(_path(results_dir, run_id, "cleanup-flag.json").read_text())
        return bool(data.get("auto_cleanup", True))
    except (OSError, ValueError):
        return True


def write_auto_cleanup_flag(results_dir: Path, run_id: str, enabled: bool) -> None:
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        _path(results_dir, run_id, "cleanup-flag.json").write_text(
            json.dumps({"auto_cleanup": enabled}), encoding="utf-8"
        )
    except OSError as exc:
        print(f"[cleanup_state] could not write auto-cleanup flag: {exc}", flush=True)


def write_cleanup_state(results_dir: Path, run_id: str, shared_state: dict) -> None:
    """Persist shared_state so a later manual cleanup (run by the API server,
    after this process has exited) has what each task's cleanup() needs."""
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        _path(results_dir, run_id, "cleanup-state.json").write_text(
            json.dumps(shared_state, default=str), encoding="utf-8"
        )
    except OSError as exc:
        print(f"[cleanup_state] could not write cleanup state: {exc}", flush=True)


def read_cleanup_state(results_dir: Path, run_id: str) -> dict:
    try:
        return json.loads(_path(results_dir, run_id, "cleanup-state.json").read_text())
    except (OSError, ValueError):
        return {}


def write_cleanup_status(
    results_dir: Path, run_id: str, status: str, error: str | None = None
) -> None:
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
            "error": error,
        }
        _path(results_dir, run_id, "cleanup-status.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError as exc:
        print(f"[cleanup_state] could not write cleanup status: {exc}", flush=True)


def read_cleanup_status(results_dir: Path, run_id: str) -> dict:
    try:
        return json.loads(_path(results_dir, run_id, "cleanup-status.json").read_text())
    except (OSError, ValueError):
        return {"status": "pending", "updated_at": None, "error": None}


# ---------- Per-resource cleanup tracking (ADR-025) ----------
#
# Tasks record what they create via harness/tasks/base.py:record_created(),
# into shared_state["_created"]. Each record carries the owning task's name,
# so whoever runs cleanup (the harness at run end, or the API server's manual
# "Clean Up Now" later) can mark exactly that task's resources with the
# outcome of that task's own cleanup(). Shared here so both paths agree.

# status values: "active" (exists, cleanup not run yet) | "removed" |
# "restored" (a pre-existing object we patched, put back) | "revoked" (API key
# revoked mid-run) | "cleanup failed" | "left in place" (auto cleanup off)


def resources_from_state(shared_state: dict) -> list[dict]:
    """The run page's "created" list — names only, never key values/tokens."""
    return [dict(r) for r in shared_state.get("_created") or []]


def mark_task_cleanup(shared_state: dict, task_name: str, ok: bool) -> None:
    for rec in shared_state.get("_created") or []:
        if rec.get("task") != task_name or rec.get("status") not in ("active", "cleanup failed", "left in place"):
            continue
        if not ok:
            rec["status"] = "cleanup failed"
        else:
            rec["status"] = "restored" if rec.get("action") == "patched" else "removed"


def mark_left_in_place(shared_state: dict) -> None:
    for rec in shared_state.get("_created") or []:
        if rec.get("status") == "active":
            rec["status"] = "left in place"


def rewrite_progress_resources(results_dir: Path, run_id: str, shared_state: dict) -> None:
    """Refresh the resources list in an already-written -progress.json — used
    by the API's manual cleanup, after the harness process has exited."""
    path = _path(results_dir, run_id, "progress.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["resources"] = resources_from_state(shared_state)
        path.write_text(json.dumps(payload, default=str), encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"[cleanup_state] could not refresh progress resources: {exc}", flush=True)
