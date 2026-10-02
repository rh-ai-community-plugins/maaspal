import time

from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

# User B counts as having had "a budget of their own" when they got at least
# this share of the limit through before their first 429, and as drawing on
# an already-spent shared pool below the lower share. In between the result
# is ambiguous (e.g. a window reset mid-run) and reported as inconclusive.
_OWN_BUDGET_SHARE = 0.8
_SHARED_POOL_SHARE = 0.5


class ClassifyRateLimitPoolingTask(Task):
    """Reads the two users' bursts from scenarios/rate_limit_per_user_or_shared
    and reports what they show — per-user budgets or one shared pool — as a
    finding, instead of checking against an expectation the user had to state.

    Precondition: user A really used up the whole budget (throttled at or past
    the limit). Then user B's tokens-before-first-429 decides it: about a full
    budget → per user; well short of one → shared."""

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        limit = float(self.params["token_limit"])
        a = ctx.shared_state.get(str(self.params.get("user_a_results", "inference_results_user_a"))) or {}
        b = ctx.shared_state.get(str(self.params.get("user_b_results", "inference_results_user_b"))) or {}
        a_at = a.get("tokens_before_first_429")
        b_at = b.get("tokens_before_first_429")

        if a_at is None or a_at < limit:
            outcome, title = "inconclusive", "Inconclusive"
            text = (
                f"User A was never throttled at the {limit:g}-token limit "
                f"({a.get('total_tokens_sent', 0)} tokens sent), so the shared budget was never "
                "used up and user B's result can't tell the two cases apart."
            )
        elif b_at is None:
            outcome, title = "inconclusive", "Inconclusive"
            text = "User B was never throttled, so there's no point to compare with user A."
        elif b_at >= _OWN_BUDGET_SHARE * limit:
            outcome, title = "per_user", "Limits are per user"
            text = (
                f"User A used {a_at:,} tokens before being throttled; user B then still got "
                f"{b_at:,} tokens through on their own key. Each user on this subscription gets "
                f"their own {limit:g}-token budget."
            )
        elif b_at <= _SHARED_POOL_SHARE * limit:
            outcome, title = "shared", "Limits are shared"
            text = (
                f"User A used {a_at:,} tokens before being throttled; user B was then throttled after "
                f"only {b_at:,}. Both users draw on one {limit:g}-token budget for the subscription."
            )
        else:
            outcome, title = "inconclusive", "Inconclusive"
            text = (
                f"User B got {b_at:,} tokens through — neither a full budget nor clearly none of it. "
                "Try again; a window reset between the two bursts can cause this."
            )

        ctx.shared_state["pooling"] = {
            "conclusive": int(outcome != "inconclusive"),
            "per_user": int(outcome == "per_user"),
        }
        ctx.shared_state.setdefault("_findings", []).append({"title": title, "text": text, "outcome": outcome})
        ctx.shared_state["_verdict_text"] = f"{title}. {text}"
        ctx.shared_state["task_summary"] = title
        await ctx.emit_assertion_state()
        return TaskResult(task_name=self.name, status="PASS", duration_ms=(time.monotonic() - start) * 1000)

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


REGISTRY["classify_rate_limit_pooling"] = ClassifyRateLimitPoolingTask
