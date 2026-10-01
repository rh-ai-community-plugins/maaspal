import pytest

from harness.tasks.analysis import ClassifyRateLimitPoolingTask
from harness.tasks.base import TaskContext


def _ctx(a: dict, b: dict) -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="r", scenario_name="s", maas_api_url="", sa_token="",
        shared_state={"inference_results_user_a": a, "inference_results_user_b": b},
        config={}, assertions={}, emit_assertion_state=_emit,
    )


@pytest.mark.parametrize(
    ("a", "b", "outcome", "title"),
    [
        ({"tokens_before_first_429": 60}, {"tokens_before_first_429": 58}, "per_user", "Limits are per user"),
        ({"tokens_before_first_429": 60}, {"tokens_before_first_429": 0}, "shared", "Limits are shared"),
        ({"total_tokens_sent": 30}, {"tokens_before_first_429": 0}, "inconclusive", "Inconclusive"),
        ({"tokens_before_first_429": 60}, {"tokens_before_first_429": 33}, "inconclusive", "Inconclusive"),
        ({"tokens_before_first_429": 60}, {}, "inconclusive", "Inconclusive"),
    ],
)
async def test_classifies_what_the_two_bursts_show(a: dict, b: dict, outcome: str, title: str) -> None:
    ctx = _ctx(a, b)
    await ClassifyRateLimitPoolingTask("classify_rate_limit_pooling", {"token_limit": 50}).run(ctx)
    [finding] = ctx.shared_state["_findings"]
    assert (finding["outcome"], finding["title"]) == (outcome, title)
    assert ctx.shared_state["pooling"]["conclusive"] == int(outcome != "inconclusive")
    assert ctx.shared_state["_verdict_text"].startswith(title)
