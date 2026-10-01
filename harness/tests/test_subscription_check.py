from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError

from harness.tasks.base import TaskContext
from harness.tasks.subscription_check import VerifySubscriptionModelsTask

_SUB = {
    "name": "team-a",
    "namespace": "models-as-a-service",
    "priority": 10,
    "owner": {"groups": ["system:authenticated"], "users": []},
    "model_refs": [
        {"name": "small", "namespace": "llm", "token_rate_limits": [{"limit": 50, "window": "1m"}]},
        {"name": "blocked", "namespace": "llm", "token_rate_limits": [{"limit": 50, "window": "1m"}]},
        {"name": "big", "namespace": "llm", "token_rate_limits": [{"limit": 100000, "window": "1m"}]},
    ],
}


def _status_error(code: int) -> APIStatusError:
    request = httpx.Request("POST", "http://m.test/v1/chat/completions")
    return APIStatusError(
        f"status {code}", response=httpx.Response(code, request=request), body=None
    )


def _ok(tokens: int = 30) -> MagicMock:
    r = MagicMock()
    r.usage.total_tokens = tokens
    return r


def _ctx() -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="abcdef123", scenario_name="s", maas_api_url="http://maas.test", sa_token="sa",
        shared_state={}, config={}, assertions={}, emit_assertion_state=_emit,
    )


async def _fake_provision(self, ctx: TaskContext) -> None:
    ctx.shared_state.setdefault("api_keys", []).append(
        {"id": "k1", "key": "sk-oai-test", "name": "maaspal-verify", "subscription": "team-a"}
    )


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("harness.tasks.subscription_check._find_subscription", lambda name: _SUB)
    monkeypatch.setattr(
        "harness.tasks.subscription_check._discovered_models",
        AsyncMock(return_value={
            "llm/small": ("http://maas.test/llm/small/v1", "small-id"),
            "llm/blocked": ("http://maas.test/llm/blocked/v1", "blocked-id"),
            "llm/big": ("http://maas.test/llm/big/v1", "big-id"),
        }),
    )
    monkeypatch.setattr(
        "harness.tasks.auth.ProvisionApiKeyTask.run", _fake_provision
    )


async def test_reports_reachability_and_limit_enforcement_per_model(_patched) -> None:
    # small: probed (limit 50 ≤ cap) — 2 OK at 30 tokens, then 429 forever.
    small = [_ok(), _ok()] + [_status_error(429)] * 38
    # blocked: probe-eligible too (same small limit), every request denied.
    blocked = [_status_error(403)] * 40
    # big: limit too high to probe — just the short reachability burst.
    big = [_ok(), _ok(), _ok()]

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=small + blocked + big)
        mock_cls.return_value = client
        ctx = _ctx()
        await VerifySubscriptionModelsTask(
            "verify_subscription_models", {"subscription": "team-a", "requests_per_model": 3}
        ).run(ctx)

    check = ctx.shared_state["subscription_check"]
    assert check == {"model_count": 3, "reachable_count": 2, "probed_count": 1, "enforced_count": 1}

    rows = ctx.shared_state["_tables"]["Subscription models"]["rows"]
    assert rows[0][0] == "llm/small"
    assert rows[0][3] == "✓ throttled at 60 tokens"
    assert rows[1][2].startswith("✗ 40× 401/403")
    assert rows[1][3] == "not probed — unreachable"
    assert rows[2][3].startswith("not probed — limit above probe cap")

    # Every burst went out with the one pinned key and SDK retries off, so
    # every 429 counts exactly once.
    for call in mock_cls.call_args_list:
        assert call.kwargs["api_key"] == "sk-oai-test"
        assert call.kwargs["max_retries"] == 0
    assert ctx.shared_state["_traffic"]["subscription_model_1"]["label"] == "llm/small"
    assert "2/3 models reachable" in ctx.shared_state["task_summary"]


async def test_key_creation_failure_explains_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("harness.tasks.subscription_check._find_subscription", lambda name: _SUB)

    async def _denied(self, ctx: TaskContext) -> None:
        request = httpx.Request("POST", "http://maas.test/maas-api/v1/api-keys")
        response = httpx.Response(403, request=request, text="not an owner")
        raise httpx.HTTPStatusError("denied", request=request, response=response)

    monkeypatch.setattr("harness.tasks.auth.ProvisionApiKeyTask.run", _denied)
    with pytest.raises(RuntimeError, match="is this harness's identity one of its owners"):
        await VerifySubscriptionModelsTask(
            "verify_subscription_models", {"subscription": "team-a"}
        ).run(_ctx())


async def test_requires_a_subscription() -> None:
    with pytest.raises(RuntimeError, match="Pick a subscription"):
        await VerifySubscriptionModelsTask("verify_subscription_models", {}).run(_ctx())
