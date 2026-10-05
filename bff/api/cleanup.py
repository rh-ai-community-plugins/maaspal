"""Manual "Clean Up Now" execution — runs a run's task cleanup() methods
in-process, inside the API server, after the harness Job pod that originally
ran it has already exited.

This reuses harness/tasks/*.py's own cleanup() implementations directly
(same container image as the harness Job, ADR-001) rather than re-implementing
key revocation / CR restore-or-delete here: none of those cleanup() methods
read self.params (confirmed by inspection — every self.params access lives in
run(), never cleanup()), so a bare TaskClass(name=name, params={}) reproduces
the exact same cleanup call the harness's own end-of-run loop would have made,
driven by whatever shared_state the original run persisted (see
harness/cleanup_state.py) instead of a live in-memory dict.
"""

import json
import traceback
from pathlib import Path

import aiosqlite

from api import maas_client
from api.db import get_db_path
from harness.cleanup_state import (
    mark_task_cleanup,
    read_cleanup_state,
    rewrite_progress_resources,
    write_cleanup_state,
    write_cleanup_status,
)
from harness.tasks.base import TaskContext
from harness.tasks.registry import REGISTRY

_RESULTS_DIR = Path("/data/results")


async def _noop_emit() -> None:
    return None


def _task_names(run_id: str) -> list[str]:
    """Every task the scenario defined, in original order — not just ones that
    completed. A task whose run() failed partway through may still have left
    partial shared_state its own cleanup() needs to act on, and a task never
    reached simply no-ops against absent state, so the full declared list is
    the right (and safe) input, not the -progress.json completion list."""
    path = _RESULTS_DIR / f"{run_id}-config.json"
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        return [t["name"] for t in snapshot.get("tasks") or []]
    except (OSError, ValueError, KeyError):
        return []


async def run_manual_cleanup(run_id: str) -> None:
    error: str | None = None
    try:
        maas_client._kube()  # noqa: SLF001 — same lazy in-cluster/kubeconfig loader every CR-based task needs

        task_names = _task_names(run_id)
        shared_state = read_cleanup_state(_RESULTS_DIR, run_id)

        ctx = TaskContext(
            run_id=run_id,
            scenario_name="",
            maas_api_url=maas_client.maas_api_url(),
            sa_token=maas_client.sa_token(),
            shared_state=shared_state,
            config={},
            assertions={},
            emit_assertion_state=_noop_emit,
        )

        any_failed = False
        for name in reversed(task_names):
            task_class = REGISTRY.get(name)
            if task_class is None:
                print(f"[api] manual cleanup: unknown task {name!r}, skipping", flush=True)
                continue
            print(f"[api] manual cleanup: {name}", flush=True)
            try:
                await task_class(name=name, params={}).cleanup(ctx)
                mark_task_cleanup(shared_state, name, ok=True)
            except Exception:
                any_failed = True
                mark_task_cleanup(shared_state, name, ok=False)
                print(f"[api] manual cleanup FAILED: {name}\n{traceback.format_exc()}", flush=True)

        # The run page's per-resource list reflects this cleanup too.
        write_cleanup_state(_RESULTS_DIR, run_id, shared_state)
        rewrite_progress_resources(_RESULTS_DIR, run_id, shared_state)

        status = "failed" if any_failed else "done"
    except Exception as exc:
        status = "failed"
        error = str(exc)
        print(f"[api] manual cleanup FAILED for {run_id}\n{traceback.format_exc()}", flush=True)

    write_cleanup_status(_RESULTS_DIR, run_id, status, error)
    async with aiosqlite.connect(get_db_path()) as db:
        await db.execute(
            "UPDATE runs SET cleanup_status=?, cleanup_error=? WHERE id=?",
            (status, error, run_id),
        )
        await db.commit()
