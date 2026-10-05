"""Stub tasks used only for Phase 1 testing. Not for production scenarios."""
import asyncio
import time

from harness.durations import parse_duration_s
from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY


class StubPassTask(Task):
    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        print(f"[stub_pass] running (run_id={ctx.run_id})", flush=True)
        ctx.shared_state.setdefault("stub_ran", []).append("stub_pass")
        await ctx.emit_assertion_state()
        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        print("[stub_pass] cleanup", flush=True)


class StubFailTask(Task):
    async def run(self, ctx: TaskContext) -> TaskResult:
        print(f"[stub_fail] running (run_id={ctx.run_id})", flush=True)
        raise RuntimeError("stub task intentionally failed")

    async def cleanup(self, ctx: TaskContext) -> None:
        print("[stub_fail] cleanup", flush=True)


class PauseTask(Task):
    """Pauses execution for a configurable duration — a wait step between
    tasks that need time for external state to settle, e.g. a rate-limit
    window resetting (scenarios/rate_limit_window_recovery.yaml). Production
    use, despite living next to the test stubs. A graceful stop still
    interrupts it immediately: the runner races every task.run() against the
    stop event (harness/runner.py:_run_task_or_stop).

    `duration_s` takes seconds or a window string ("1m", "24h"), plus an
    optional `buffer_s` added on top (e.g. to land safely past a window
    reset). `max_s` (default 900) caps the total so a long real-world window
    (a "24h" subscription) can't silently stall a run for a day — capped
    waits are called out in the narration. Optional `reason` is shown there too."""

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        requested_s = parse_duration_s(self.params.get("duration_s", 5)) + float(
            self.params.get("buffer_s", 0)
        )
        max_s = float(self.params.get("max_s", 900))
        duration_s = min(requested_s, max_s)
        reason = str(self.params.get("reason") or "")
        if requested_s > max_s:
            reason = (reason + " — " if reason else "") + (
                f"capped at {max_s:g}s (requested {requested_s:g}s)"
            )
        print(f"[pause] sleeping {duration_s}s {reason}".rstrip(), flush=True)
        total = max(int(duration_s), 1)
        ctx.shared_state["task_summary"] = f"Waiting {duration_s:g}s" + (f" — {reason}" if reason else "")
        elapsed = 0.0
        while elapsed < duration_s:
            step = min(1.0, duration_s - elapsed)
            await asyncio.sleep(step)
            elapsed += step
            ctx.shared_state["task_progress"] = {"current": min(int(elapsed), total), "total": total}
            await ctx.emit_assertion_state()
        ctx.shared_state["task_summary"] = f"Waited {duration_s:g}s" + (f" — {reason}" if reason else "")
        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


REGISTRY["stub_pass"] = StubPassTask
REGISTRY["stub_fail"] = StubFailTask
REGISTRY["pause"] = PauseTask
