from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError

from harness.tasks.base import TaskContext
from harness.tasks.subscription_check import (
    DiscoverSubscriptionModelsTask,
    ReadSubscriptionLimitsTask,
    SendRequestsToEachModelTask,
)

_SUB = {
    "name": "team-a",
    "namespace": "models-as-a-service",
    "priority": 10,
    "owner": {"groups": ["system:authenticated"], "users": []},
    "model_refs": [
        {"name": "small", "namespace": "llm", "token_rate_limits": [{"limit": 50, "window": "1m"}]},
        {"name": "blocked", "namespace": "llm", "token_rate_limits": []},
        {"name": "unlisted", "namespace": "llm", "token_rate_limits": [{"limit": 1000, "window": "1h"}]},
    ],
}


def _ctx(state: dict | None = None) -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="abcdef123", scenario_name="s", maas_api_url="http://maas.test", sa_token="sa",
        shared_state=state or {}, config={}, assertions={}, emit_assertion_state=_emit,
    )


def _status_error(code: int) -> APIStatusError:
    request = httpx.Request("POST", "http://m.test/v1/chat/completions")
    return APIStatusError(f"status {code}", response=httpx.Response(code, request=request), body=None)  # type: ignore[arg-type]


def _ok(tokens: int = 30) -> MagicMock:
    r = MagicMock()
    r.usage.total_tokens = tokens
    return r


@pytest.fixture(autouse=True)
def _cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harness.tasks.subscription_check._find_subscription", lambda name: _SUB)
    monkeypatch.setattr(
        "harness.tasks.subscription_check._discovered_models",
        AsyncMock(return_value={
            "llm/small": ("http://maas.test/llm/small/v1", "small-id"),
            "llm/blocked": ("http://maas.test/llm/blocked/v1", "blocked-id"),
        }),
    )


async def test_read_subscription_limits_reads_the_configured_limit() -> None:
    ctx = _ctx()
    await ReadSubscriptionLimitsTask(
        "read_subscription_limits", {"subscription": "team-a", "model_name": "small", "model_namespace": "llm"}
    ).run(ctx)
    assert ctx.shared_state["subscription_limits"] == {"token_limit": 50, "window": "1m", "window_s": 60.0}
    assert ctx.shared_state["task_summary"] == "team-a allows 50 tokens per 1m on llm/small"


@pytest.mark.parametrize(
    ("model", "message"),
    [("other", "doesn't cover llm/other"), ("blocked", "has no token rate limit")],
)
async def test_read_subscription_limits_explains_what_is_missing(model: str, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        await ReadSubscriptionLimitsTask(
            "read_subscription_limits", {"subscription": "team-a", "model_name": model, "model_namespace": "llm"}
        ).run(_ctx())


async def test_discover_lists_the_subscription_models_with_their_limits() -> None:
    ctx = _ctx()
    await DiscoverSubscriptionModelsTask("discover_subscription_models", {"subscription": "team-a"}).run(ctx)
    models = ctx.shared_state["subscription_models"]
    assert [m["ref"] for m in models] == ["llm/small", "llm/blocked", "llm/unlisted"]
    # Not in /v1/models: its own per-model route, never discovery's fallback.
    assert models[2]["url"] == "http://maas.test/llm/unlisted/v1"
    rows = ctx.shared_state["_tables"]["Subscription models"]["rows"]
    assert rows[0][:3] == ["llm/small", "50 tokens / 1m", "✓"]
    assert rows[1][1] == "no limit configured"
    assert rows[2][2] == "✗"


async def test_send_to_each_model_fills_in_reachability() -> None:
    ctx = _ctx({"api_keys": [{"id": "k1", "key": "sk-oai-test"}]})
    await DiscoverSubscriptionModelsTask("discover_subscription_models", {"subscription": "team-a"}).run(ctx)
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=[_ok(), _ok(), _ok()] + [_status_error(403)] * 3 + [_ok(), _status_error(503), _ok()]
        )
        mock_cls.return_value = client
        await SendRequestsToEachModelTask("send_requests_to_each_model", {"requests_per_model": 3}).run(ctx)

    assert ctx.shared_state["subscription_check"]["reachable_count"] == 2
    rows = ctx.shared_state["_tables"]["Subscription models"]["rows"]
    assert rows[0][3] == "✓ 3/3 OK"
    assert rows[1][3] == "✗ 3× denied (401/403)"
    assert rows[2][3] == "✓ 2/3 OK"
    for call in mock_cls.call_args_list:
        assert call.kwargs["api_key"] == "sk-oai-test"
    assert "2 of 3 model(s) answered" in ctx.shared_state["task_summary"]


async def test_send_to_each_model_needs_a_key() -> None:
    with pytest.raises(RuntimeError, match="No API key"):
        await SendRequestsToEachModelTask("send_requests_to_each_model", {}).run(_ctx())
