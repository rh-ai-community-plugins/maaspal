import time

from harness.metrics_client import fetch_metrics
from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY


class CheckMaasMetricsTask(Task):
    """Optional explicit final metrics check with a printed summary.

    Standard scenarios no longer include this task — MaaS metrics are polled
    continuously in the background by ScenarioRunner (see harness/runner.py).
    This task remains available for scenarios that want an explicit final
    check plus a human-readable log summary.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()

        metrics_url = self.params.get("metrics_url") or ctx.config.get("MAAS_METRICS_URL", "")
        queries = self.params.get("queries") or ctx.metrics_queries

        if metrics_url and queries:
            raw = await fetch_metrics(metrics_url, queries, ctx.sa_token)
        else:
            print(
                "[check_maas_metrics] no metrics URL/queries configured — using stub zeroes",
                flush=True,
            )
            raw = {"total_requests": 0, "total_tokens": 0}

        ctx.shared_state["metrics"] = {**ctx.shared_state.get("metrics", {}), **raw}

        print("[check_maas_metrics] --- metrics summary ---", flush=True)
        for key, val in raw.items():
            print(f"  {key}: {val}", flush=True)
        print("[check_maas_metrics] ----------------------", flush=True)

        await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


REGISTRY["check_maas_metrics"] = CheckMaasMetricsTask
