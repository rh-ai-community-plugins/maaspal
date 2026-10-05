import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError

from harness.tasks.base import TaskContext
from harness.tasks.inference import SendRequestsTask, _distribute, _percentiles


def _api_status_error(status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "http://m.test/v1/chat/completions")
    response = httpx.Response(status_code, request=request, json={"error": "denied"})
    return APIStatusError(
        # The SDK is typed against httpx2 (an httpx fork); httpx objects work at runtime.
        f"status {status_code}", response=response, body={"error": "denied"}  # type: ignore[arg-type]
    )


def _make_ctx(shared_state: dict | None = None) -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="test-001",
        scenario_name="test",
        maas_api_url="http://maas.test",
        sa_token="test-token",
        shared_state=shared_state or {},
        config={},
        assertions={},
        emit_assertion_state=_emit,
    )


def _mock_client(*, fail: bool = False) -> MagicMock:
    m = MagicMock()
    if fail:
        m.chat.completions.create = AsyncMock(side_effect=Exception("request failed"))
    else:
        m.chat.completions.create = AsyncMock(return_value=MagicMock())
    return m


def _assert_client_built_with(mock_cls: MagicMock, *, api_key: str, base_url: str) -> None:
    """The last AsyncOpenAI(...) call targeted this key/URL — ignores the
    retry/http_client plumbing kwargs send_requests also passes."""
    kwargs = mock_cls.call_args.kwargs
    assert (kwargs["api_key"], kwargs["base_url"]) == (api_key, base_url)


async def test_send_requests_updates_inference_results() -> None:
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        task = SendRequestsTask(
            "send_requests",
            {"count": "3", "concurrency": "2", "prompt": "Hi", "url": "http://m.test", "token": "sk-t"},
        )
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    ir = ctx.shared_state["inference_results"]
    assert ir["total_requests"] == 3
    assert ir["success_count"] == 3
    assert ir["fail_count"] == 0
    assert ir["error_rate_pct"] == 0.0


async def test_send_requests_accumulates_token_usage() -> None:
    def _response_with_usage(*args, **kwargs) -> MagicMock:
        r = MagicMock()
        r.usage.total_tokens = 30
        r.usage.prompt_tokens = 10
        r.usage.completion_tokens = 20
        return r

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = AsyncMock(side_effect=_response_with_usage)
        mock_cls.return_value = m

        task = SendRequestsTask(
            "send_requests",
            {"count": "3", "concurrency": "2", "url": "http://m.test", "token": "sk-t"},
        )
        ctx = _make_ctx()
        await task.run(ctx)

    ir = ctx.shared_state["inference_results"]
    assert ir["total_tokens_sent"] == 90
    assert ir["prompt_tokens_sent"] == 30
    assert ir["completion_tokens_sent"] == 60
    # Same elapsed denominator as throughput_rps — see rate_limit_validation.yaml
    # (ADR-009's Update) for why this exists: MaaSSubscription rate limits are
    # token-based, not request-based, so an "is throughput under the limit"
    # assertion needs a token-denominated metric to compare against.
    assert ir["token_throughput_per_sec"] >= 0
    assert ir["token_throughput_per_sec"] == pytest.approx(
        ir["total_tokens_sent"] * ir["throughput_rps"] / ir["success_count"]
    )


async def test_send_requests_missing_usage_does_not_crash() -> None:
    """Responses without a usable .usage.total_tokens (e.g. a bare MagicMock) are treated as 0."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()  # default MagicMock() response, no real usage

        task = SendRequestsTask(
            "send_requests",
            {"count": "2", "concurrency": "2", "url": "http://m.test", "token": "sk-t"},
        )
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["inference_results"]["total_tokens_sent"] == 0


async def test_send_requests_counts_errors() -> None:
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client(fail=True)

        task = SendRequestsTask(
            "send_requests",
            {"count": "4", "concurrency": "2", "url": "http://m.test", "token": "sk-t"},
        )
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    ir = ctx.shared_state["inference_results"]
    assert ir["fail_count"] == 4
    assert ir["error_rate_pct"] == 100.0
    assert ir["rate_limited_count"] == 0
    assert ir["unauthorized_count"] == 0


async def test_send_requests_counts_rate_limited_status() -> None:
    """A 429 APIStatusError is tallied separately from other failures."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = AsyncMock(side_effect=_api_status_error(429))
        mock_cls.return_value = m

        task = SendRequestsTask(
            "send_requests",
            {"count": "3", "concurrency": "2", "url": "http://m.test", "token": "sk-t"},
        )
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    ir = ctx.shared_state["inference_results"]
    assert ir["fail_count"] == 3
    assert ir["rate_limited_count"] == 3
    assert ir["unauthorized_count"] == 0


@pytest.mark.parametrize("status_code", [401, 403])
async def test_send_requests_counts_unauthorized_status(status_code: int) -> None:
    """A 401/403 APIStatusError is tallied as an auth denial, not a rate limit."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = AsyncMock(side_effect=_api_status_error(status_code))
        mock_cls.return_value = m

        task = SendRequestsTask(
            "send_requests",
            {"count": "2", "concurrency": "2", "url": "http://m.test", "token": "sk-t"},
        )
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    ir = ctx.shared_state["inference_results"]
    assert ir["fail_count"] == 2
    assert ir["unauthorized_count"] == 2
    assert ir["rate_limited_count"] == 0


async def test_first_rate_limited_at_tokens_captures_cumulative_total() -> None:
    """Precisely calibrates rate-limit enforcement, not just "some requests
    eventually got denied": records total_tokens_sent at the moment of the
    FIRST 429, so a scenario can assert the limit triggered around its
    configured budget (allowing for one in-flight request's spillover)."""

    def _response_with_usage(*args, **kwargs) -> MagicMock:
        r = MagicMock()
        r.usage.total_tokens = 30
        return r

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = AsyncMock(
            side_effect=[_response_with_usage(), _response_with_usage(), _api_status_error(429)]
        )
        mock_cls.return_value = m

        task = SendRequestsTask(
            "send_requests",
            {"count": "3", "concurrency": "1", "url": "http://m.test", "token": "sk-t"},
        )
        ctx = _make_ctx()
        await task.run(ctx)

    ir = ctx.shared_state["inference_results"]
    assert ir["first_rate_limited_at_tokens"] == 60


async def test_first_rate_limited_at_tokens_absent_when_never_rate_limited() -> None:
    def _response_with_usage(*args, **kwargs) -> MagicMock:
        r = MagicMock()
        r.usage.total_tokens = 30
        return r

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = AsyncMock(side_effect=_response_with_usage)
        mock_cls.return_value = m

        task = SendRequestsTask(
            "send_requests",
            {"count": "2", "concurrency": "1", "url": "http://m.test", "token": "sk-t"},
        )
        ctx = _make_ctx()
        await task.run(ctx)

    assert "first_rate_limited_at_tokens" not in ctx.shared_state["inference_results"]


def _usage_response(tokens: int = 30) -> MagicMock:
    r = MagicMock()
    r.usage.total_tokens = tokens
    return r


async def _run_sequence(outcomes: list, **params) -> dict:
    """Run send_requests sequentially against a fixed list of outcomes
    (a response or an exception per request) and return shared_state."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = AsyncMock(side_effect=outcomes)
        mock_cls.return_value = m
        task = SendRequestsTask(
            "send_requests",
            {
                "count": str(len(outcomes)),
                "concurrency": "1",
                "url": "http://m.test",
                "token": "sk-t",
                **params,
            },
        )
        ctx = _make_ctx()
        await task.run(ctx)
    ctx.shared_state["_mock_cls"] = mock_cls
    return ctx.shared_state


async def test_retries_param_passed_as_max_retries() -> None:
    state = await _run_sequence([_usage_response()], retries="0")
    assert state["_mock_cls"].call_args.kwargs["max_retries"] == 0


async def test_retries_default_matches_sdk_default() -> None:
    state = await _run_sequence([_usage_response()])
    assert state["_mock_cls"].call_args.kwargs["max_retries"] == 2


async def test_first_429_context_counters() -> None:
    """tokens/requests before the first 429, plus leakage after it."""
    state = await _run_sequence(
        [
            _usage_response(30),
            _usage_response(30),
            _api_status_error(429),
            _usage_response(30),  # succeeded after being throttled — leakage
            _api_status_error(429),
        ]
    )
    ir = state["inference_results"]
    assert ir["tokens_before_first_429"] == 60
    assert ir["requests_before_first_429"] == 2
    assert ir["seconds_to_first_429"] >= 0
    assert ir["successes_after_first_429"] == 1
    assert ir["rate_limited_count"] == 2


async def test_successes_after_first_429_zero_when_never_throttled() -> None:
    ir = (await _run_sequence([_usage_response(), _usage_response()]))["inference_results"]
    assert ir["successes_after_first_429"] == 0
    assert "tokens_before_first_429" not in ir


async def test_error_breakdown_by_cause() -> None:
    ir = (
        await _run_sequence(
            [
                _usage_response(),
                _api_status_error(429),
                _api_status_error(401),
                _api_status_error(503),
                _api_status_error(400),
                Exception("connection reset"),
            ]
        )
    )["inference_results"]
    assert ir["rate_limited_count"] == 1
    assert ir["unauthorized_count"] == 1
    assert ir["server_error_count"] == 1
    assert ir["other_error_count"] == 2
    assert ir["fail_count"] == 5
    # 4 of 6 failed for a reason other than throttling.
    assert ir["non_throttle_error_rate_pct"] == pytest.approx(4 / 6 * 100)


async def test_timeline_records_each_request_outcome() -> None:
    state = await _run_sequence(
        [_usage_response(10), _api_status_error(429), _api_status_error(403)]
    )
    traffic = state["_traffic"]["inference_results"]
    assert traffic["task"] == "send_requests"
    assert traffic["planned"] == 3
    statuses = [entry[2] for entry in traffic["timeline"]]
    assert statuses == ["ok", "throttled", "denied"]
    cumulative_tokens = [entry[1] for entry in traffic["timeline"]]
    assert cumulative_tokens == [10, 10, 10]


async def test_task_summary_narrates_outcome() -> None:
    state = await _run_sequence([_usage_response(30), _api_status_error(429)])
    summary = state["task_summary"]
    assert "2/2 requests" in summary
    assert "1 OK" in summary
    assert "1 throttled (429)" in summary
    assert "first 429 after 30 tokens" in summary


async def test_zero_count_skips_cleanly() -> None:
    state = await _run_sequence([])
    assert "inference_results" not in state
    assert "skipped" in state["task_summary"]


async def test_send_requests_debounce() -> None:
    """Debounce: emit_assertion_state fires at most once per 100 ms burst."""
    emit_times: list[float] = []

    async def _track_emit() -> None:
        emit_times.append(time.monotonic())

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        task = SendRequestsTask(
            "send_requests",
            {"count": "10", "concurrency": "10", "url": "http://m.test", "token": "sk-t"},
        )
        ctx = TaskContext(
            run_id="test-001",
            scenario_name="test",
            maas_api_url="http://maas.test",
            sa_token="test-token",
            shared_state={},
            config={},
            assertions={},
            emit_assertion_state=_track_emit,
        )
        await task.run(ctx)

    # 10 rapid requests + 1 final emit; debounce must keep total well below 10
    assert len(emit_times) >= 1
    assert len(emit_times) < 10, f"debounce failed: {len(emit_times)} emits for 10 rapid requests"


async def test_key_pool_distribution() -> None:
    """Key-pool distribution: requests spread floor(M/N) each, remainder to first."""
    calls_by_key: dict[str | None, int] = {}

    def _make_tracking_client(api_key: str | None = None, base_url: str | None = None, **_kwargs) -> MagicMock:
        m = MagicMock()

        async def _complete(*args, **kwargs) -> MagicMock:
            calls_by_key[api_key] = calls_by_key.get(api_key, 0) + 1
            return MagicMock()

        m.chat.completions.create = _complete
        return m

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.side_effect = _make_tracking_client

        ctx = _make_ctx(
            {
                "api_keys": [
                    {"id": "id-1", "key": "sk-key-1"},
                    {"id": "id-2", "key": "sk-key-2"},
                    {"id": "id-3", "key": "sk-key-3"},
                ]
            }
        )
        task = SendRequestsTask(
            "send_requests",
            {
                "count": "6",
                "concurrency": "6",
                "url": "http://m.test",
                "token": "sk-default",
                "key_pool": True,
            },
        )
        await task.run(ctx)

    # 6 requests / 3 keys = 2 each (no remainder)
    assert sum(calls_by_key.values()) == 6
    assert all(v == 2 for v in calls_by_key.values()), f"uneven distribution: {calls_by_key}"


async def test_key_pool_distribution_with_remainder() -> None:
    """Key-pool distribution: remainder goes to first key."""
    calls_by_key: dict[str | None, int] = {}

    def _make_tracking_client(api_key: str | None = None, base_url: str | None = None, **_kwargs) -> MagicMock:
        m = MagicMock()

        async def _complete(*args, **kwargs) -> MagicMock:
            calls_by_key[api_key] = calls_by_key.get(api_key, 0) + 1
            return MagicMock()

        m.chat.completions.create = _complete
        return m

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.side_effect = _make_tracking_client

        ctx = _make_ctx(
            {
                "api_keys": [
                    {"id": "id-1", "key": "sk-key-1"},
                    {"id": "id-2", "key": "sk-key-2"},
                ]
            }
        )
        task = SendRequestsTask(
            "send_requests",
            {
                "count": "7",
                "concurrency": "7",
                "url": "http://m.test",
                "token": "sk-default",
                "key_pool": True,
            },
        )
        await task.run(ctx)

    # 7 requests / 2 keys: first gets 4 (3+1 remainder), second gets 3
    assert calls_by_key.get("sk-key-1") == 4
    assert calls_by_key.get("sk-key-2") == 3


async def test_key_index_selects_single_key_from_pool() -> None:
    """ADR-023: key_index targets exactly one dynamically-created key —
    bypassing key_pool's whole-pool distribution — so a scenario can run
    one user's key at a time (e.g. two sequential bursts to compare)."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        ctx = _make_ctx(
            {
                "api_keys": [
                    {"id": "id-1", "key": "sk-key-1"},
                    {"id": "id-2", "key": "sk-key-2"},
                ]
            }
        )
        task = SendRequestsTask(
            "send_requests",
            {"count": "1", "url": "http://m.test", "key_index": 1},
        )
        await task.run(ctx)

    _assert_client_built_with(mock_cls, api_key="sk-key-2", base_url="http://m.test")


async def test_key_index_overrides_static_token_param() -> None:
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        ctx = _make_ctx({"api_keys": [{"id": "id-1", "key": "sk-from-pool"}]})
        task = SendRequestsTask(
            "send_requests",
            {"count": "1", "url": "http://m.test", "token": "sk-static", "key_index": 0},
        )
        await task.run(ctx)

    _assert_client_built_with(mock_cls, api_key="sk-from-pool", base_url="http://m.test")


async def test_model_from_shared_state_sets_model_param() -> None:
    """Targets a model deploy_simulated_model just created at runtime — the
    same shared_state-lookup reason key_index exists above. Confirmed live:
    such a model is never listed in /v1/models (that needs full governance
    pairing), but its own dedicated per-model route works directly, with
    the bare model name (not "namespace/name") in the request body."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        client = _mock_client()
        mock_cls.return_value = client

        ctx = _make_ctx(
            {"deployed_models": [{"name": "maaspal-fail-closed-model-abcd1234-1", "namespace": "llm"}]}
        )
        task = SendRequestsTask(
            "send_requests",
            {"count": "1", "url": "http://m.test", "model_from_shared_state": "deployed_models"},
        )
        await task.run(ctx)

    assert client.chat.completions.create.call_args.kwargs["model"] == "maaspal-fail-closed-model-abcd1234-1"
    _assert_client_built_with(mock_cls, 
        api_key="test-token",
        base_url="http://maas.test/llm/maaspal-fail-closed-model-abcd1234-1/v1",
    )


async def test_key_pool_target_model_uses_dedicated_model_route() -> None:
    """A key pool whose entries carry their own target_model (e.g. from
    provision_keys_distributed) must not depend on GET /v1/models listing a
    freshly-deployed model yet — route to that model's own dedicated path
    instead (confirmed live: the generic /v1/chat/completions route 404s
    for a model that isn't registered there, but /{namespace}/{name}/v1/...
    reaches it directly and still goes through the same gateway auth)."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        client = _mock_client()
        mock_cls.return_value = client

        ctx = _make_ctx(
            {"api_keys": [{"id": "id-1", "key": "sk-key-1", "target_model": "maaspal/maaspal-sim-abcd-1"}]}
        )
        task = SendRequestsTask(
            "send_requests",
            {"count": "1", "key_pool": True},
        )
        await task.run(ctx)

    _assert_client_built_with(mock_cls, 
        api_key="sk-key-1",
        base_url="http://maas.test/maaspal/maaspal-sim-abcd-1/v1",
    )
    assert client.chat.completions.create.call_args.kwargs["model"] == "maaspal-sim-abcd-1"


async def test_key_pool_subscription_aware_model_resolution(httpx_mock) -> None:
    """No explicit model/target_model given — resolve each key's own model
    by cross-referencing its bound subscription against /v1/models'
    subscriptions[] list, so an auto-selected subscription can't end up
    paired with an incompatible auto-discovered/DEFAULT_MODEL model."""
    models_response = {
        "data": [
            {
                "id": "model-a-id",
                "owned_by": "llm/model-a",
                "url": "http://gateway.test",
                "subscriptions": [{"name": "sub-a"}],
            },
            {
                "id": "model-b-id",
                "owned_by": "llm/model-b",
                "url": "http://gateway.test",
                "subscriptions": [{"name": "sub-b"}],
            },
        ]
    }
    httpx_mock.add_response(url="http://maas.test/v1/models", json=models_response)
    httpx_mock.add_response(url="http://maas.test/v1/models", json=models_response)

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        client = _mock_client()
        mock_cls.return_value = client

        ctx = _make_ctx(
            {
                "api_keys": [
                    {"id": "id-1", "key": "sk-key-1", "subscription": "sub-a"},
                    {"id": "id-2", "key": "sk-key-2", "subscription": "sub-b"},
                ]
            }
        )
        task = SendRequestsTask(
            "send_requests", {"count": "2", "concurrency": "2", "key_pool": True}
        )
        await task.run(ctx)

    models_used = {call.kwargs["model"] for call in client.chat.completions.create.call_args_list}
    assert models_used == {"model-a-id", "model-b-id"}


async def test_key_pool_subscription_not_listed_falls_back(httpx_mock) -> None:
    """A key whose subscription isn't listed under any model must fall back
    to the default model rather than crash or silently omit the request."""
    httpx_mock.add_response(
        url="http://maas.test/v1/models",
        json={"data": [{"id": "model-a-id", "url": "http://gateway.test", "subscriptions": []}]},
    )
    httpx_mock.add_response(
        url="http://maas.test/v1/models",
        json={"data": [{"id": "model-a-id", "url": "http://gateway.test", "subscriptions": []}]},
    )

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        client = _mock_client()
        mock_cls.return_value = client

        ctx = _make_ctx({"api_keys": [{"id": "id-1", "key": "sk-key-1", "subscription": "unlisted-sub"}]})
        task = SendRequestsTask("send_requests", {"count": "1", "key_pool": True})
        await task.run(ctx)

    assert client.chat.completions.create.call_args.kwargs["model"] == "model-a-id"


async def test_model_from_shared_state_absent_is_noop() -> None:
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        client = _mock_client()
        mock_cls.return_value = client

        task = SendRequestsTask(
            "send_requests",
            {"count": "1", "url": "http://m.test", "model": "static-model", "model_from_shared_state": "deployed_models"},
        )
        await task.run(_make_ctx())

    assert client.chat.completions.create.call_args.kwargs["model"] == "static-model"


async def test_result_key_writes_to_custom_shared_state_key() -> None:
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        task = SendRequestsTask(
            "send_requests",
            {"count": "2", "url": "http://m.test", "token": "sk-t", "result_key": "inference_results_user_a"},
        )
        ctx = _make_ctx()
        await task.run(ctx)

    assert "inference_results" not in ctx.shared_state
    assert ctx.shared_state["inference_results_user_a"]["total_requests"] == 2


async def test_two_invocations_with_distinct_result_keys_coexist() -> None:
    """Mirrors the real scenario shape: two sequential send_requests-family
    task runs, each keyed to a different user, must not clobber each other's
    results — the fixed "inference_results" key would (ADR-019/ADR-023)."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        ctx = _make_ctx(
            {
                "api_keys": [
                    {"id": "id-1", "key": "sk-user-a"},
                    {"id": "id-2", "key": "sk-user-b"},
                ]
            }
        )
        task_a = SendRequestsTask(
            "send_requests",
            {"count": "3", "url": "http://m.test", "key_index": 0, "result_key": "inference_results_user_a"},
        )
        await task_a.run(ctx)

        task_b = SendRequestsTask(
            "send_requests_as_second_user",
            {"count": "5", "url": "http://m.test", "key_index": 1, "result_key": "inference_results_user_b"},
        )
        await task_b.run(ctx)

    assert ctx.shared_state["inference_results_user_a"]["total_requests"] == 3
    assert ctx.shared_state["inference_results_user_b"]["total_requests"] == 5


async def test_url_resolution_explicit_params_priority() -> None:
    """Explicit params override shared_state for url/token."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        task = SendRequestsTask(
            "send_requests",
            {"count": "1", "url": "http://explicit.test", "token": "sk-explicit"},
        )
        ctx = _make_ctx({"url": "http://shared.test", "token": "sk-shared"})
        await task.run(ctx)

    _assert_client_built_with(mock_cls, api_key="sk-explicit", base_url="http://explicit.test")


async def test_url_resolution_shared_state_fallback() -> None:
    """shared_state used when explicit params are absent."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        task = SendRequestsTask("send_requests", {"count": "1"})
        ctx = _make_ctx({"url": "http://shared.test", "token": "sk-shared"})
        await task.run(ctx)

    _assert_client_built_with(mock_cls, api_key="sk-shared", base_url="http://shared.test")


async def test_url_resolution_sa_token_fallback(httpx_mock) -> None:
    """SA token used when no explicit token and no shared_state token."""
    httpx_mock.add_response(
        url="http://maas.test/v1/models",
        json={"data": [{"id": "granite", "url": "http://model.test"}]},
    )

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        task = SendRequestsTask("send_requests", {"count": "1"})
        ctx = _make_ctx()  # no url or token in shared_state
        await task.run(ctx)

    _assert_client_built_with(mock_cls, 
        api_key="test-token", base_url="http://model.test/v1"
    )


async def test_url_resolution_matches_by_owned_by(httpx_mock) -> None:
    """Confirmed live: a scenario's own target_model_namespace/target_model_name
    (e.g. "llm/facebook-opt-125m-simulated") matches neither a model's `id`
    nor its `displayName` — only `owned_by` ("<namespace>/<name>") is a
    reliable match for what a scenario actually knows. Regression test for a
    live incident: an unrelated second model sorting first in the discovery
    response silently hijacked a scenario that had a target configured but
    never matched anything, falling through to "first available"."""
    httpx_mock.add_response(
        url="http://maas.test/v1/models",
        json={
            "data": [
                {
                    "id": "some-other-model-external",
                    "owned_by": "llm/some-other-model-external",
                    "url": "http://wrong-model.test",
                },
                {
                    "id": "publishers/llm/models/facebook/opt-125m",
                    "owned_by": "llm/facebook-opt-125m-simulated",
                    "modelDetails": {"displayName": "Facebook OPT 125M (Simulated)"},
                    "url": "http://right-model.test",
                },
            ]
        },
    )

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        task = SendRequestsTask(
            "send_requests", {"count": "1", "model": "llm/facebook-opt-125m-simulated"}
        )
        ctx = _make_ctx()
        await task.run(ctx)

    _assert_client_built_with(mock_cls, 
        api_key="test-token", base_url="http://right-model.test/v1"
    )


def test_distribute_evenly() -> None:
    assert _distribute(6, 3) == [2, 2, 2]
    assert _distribute(5, 5) == [1, 1, 1, 1, 1]


def test_distribute_with_remainder() -> None:
    """Remainder assigned to first key."""
    assert _distribute(7, 3) == [3, 2, 2]
    assert _distribute(10, 3) == [4, 3, 3]


def test_percentiles_empty() -> None:
    result = _percentiles([])
    assert result["p50_latency_ms"] == 0.0
    assert result["p95_latency_ms"] == 0.0
    assert result["p99_latency_ms"] == 0.0


def test_percentiles_computed() -> None:
    lats = list(range(1, 101))  # 1 to 100, sorted; n=100
    result = _percentiles(lats)
    # floor-index: int(100*p/100) -> p50=s[50]=51, p95=s[95]=96, p99=s[99]=100
    assert result["p50_latency_ms"] == 51
    assert result["p95_latency_ms"] == 96
    assert result["p99_latency_ms"] == 100


async def test_stop_after_429s_skips_remaining_requests() -> None:
    state = await _run_sequence(
        [_usage_response(30), _api_status_error(429), _api_status_error(429), _usage_response(30)],
        stop_after_429s="2",
    )
    ir = state["inference_results"]
    assert ir["total_requests"] == 3  # the 4th was never sent
    assert "stopped early after 2 throttled requests" in state["task_summary"]


async def test_key_pool_requests_interleave_round_robin() -> None:
    sent_with: list[str] = []
    clients = {}

    def _client_for(**kwargs):
        key = kwargs["api_key"]

        async def _create(**_):
            sent_with.append(key)
            return MagicMock()

        c = MagicMock()
        c.chat.completions.create = _create
        clients[key] = c
        return c

    with patch("harness.tasks.inference.AsyncOpenAI", side_effect=_client_for):
        task = SendRequestsTask(
            "send_requests",
            {"count": "5", "concurrency": "1", "url": "http://m.test", "key_pool": True},
        )
        ctx = _make_ctx({"api_keys": [{"id": "a", "key": "sk-a"}, {"id": "b", "key": "sk-b"}]})
        await task.run(ctx)

    assert sent_with == ["sk-a", "sk-b", "sk-a", "sk-b", "sk-a"]


async def test_until_throttled_keeps_sending_until_limit_then_stops() -> None:
    outcomes = [_usage_response(30)] * 4 + [_api_status_error(429)] * 3 + [_usage_response(30)] * 50
    state = await _run_sequence(outcomes, until_throttled="true", max_requests="57")
    ir = state["inference_results"]
    assert ir["total_requests"] == 7  # 4 OK, then 3 throttled ends the burst
    assert ir["tokens_before_first_429"] == 120
    assert state["_traffic"]["inference_results"]["planned"] is None
    assert "sent until throttled" in state["task_summary"]


async def test_until_throttled_progress_tracks_tokens_toward_the_limit() -> None:
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = AsyncMock(side_effect=[_usage_response(30), _usage_response(30), _api_status_error(429)] * 3)
        mock_cls.return_value = m
        ctx = _make_ctx({"subscription_limits": {"token_limit": 50}})
        task = SendRequestsTask(
            "send_requests",
            {"url": "http://m.test", "token": "sk-t", "concurrency": "1", "until_throttled": True,
             "max_requests": "9", "limit_from_shared_state": "subscription_limits.token_limit"},
        )
        await task.run(ctx)
    # Frozen at the moment the burst ended — capped at the limit.
    assert ctx.shared_state["task_progress"] == {"current": 50, "total": 50, "unit": "tokens"}


class _WindowedLimiter:
    """A fake model behind a fixed-window token limit: answers after
    `latency_s`, 429s once `limit` tokens were used in the current window.
    `server_slots` caps how many requests it serves at once (a slow model)."""

    def __init__(self, limit: int, window_s: float, latency_s: float, tokens: int = 20, server_slots: int = 10_000):
        self.limit, self.window_s, self.latency_s, self.tokens = limit, window_s, latency_s, tokens
        self.used = 0
        self.window_start: float | None = None
        self.slots = asyncio.Semaphore(server_slots)

    async def create(self, **_):
        now = time.monotonic()
        if self.window_start is None or now - self.window_start >= self.window_s:
            self.window_start, self.used = now, 0
        if self.used >= self.limit:
            raise _api_status_error(429)
        async with self.slots:
            await asyncio.sleep(self.latency_s)
        self.used += self.tokens
        return _usage_response(self.tokens)


async def _run_limiter(
    limiter: _WindowedLimiter, monkeypatch: pytest.MonkeyPatch, interval_s: float = 0.05, **params
) -> dict:
    monkeypatch.setattr("harness.tasks.inference._RAMP_INTERVAL_S", interval_s)
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = limiter.create
        mock_cls.return_value = m
        ctx = _make_ctx()
        await SendRequestsTask(
            "send_requests",
            {"url": "http://m.test", "token": "sk-t", "concurrency": "1", "until_throttled": "true", **params},
        ).run(ctx)
    return ctx.shared_state


async def test_until_throttled_ramps_concurrency_to_reach_the_limit_within_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At concurrency 1 this model serves ~200 tokens/s — too slow to use up
    400 tokens within a 1 s window, so the limit would never trigger. The
    ramp has to open more concurrency until it does."""
    limiter = _WindowedLimiter(limit=400, window_s=1.0, latency_s=0.1)
    state = await _run_limiter(limiter, monkeypatch, limit="400", window="1s", max_duration_s="5")
    ir = state["inference_results"]
    assert ir["limit_reached"] == 1
    assert ir["concurrency_at_first_429"] > 1
    assert ir["tokens_before_first_429"] >= 400
    # Overshoot bound scales with how many requests were in flight.
    assert ir["allowed_overshoot"] == ir["concurrency_at_first_429"] * 20
    assert ir["tokens_before_first_429"] <= 400 + ir["allowed_overshoot"]
    assert ir["successes_after_first_429"] == 0  # in-flight completions aren't leakage


async def test_until_throttled_explains_a_model_too_slow_to_hit_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One server slot: more concurrency just queues — throughput can't grow.
    limiter = _WindowedLimiter(limit=100_000, window_s=1.0, latency_s=0.02, server_slots=1)
    state = await _run_limiter(
        limiter, monkeypatch, interval_s=0.2, limit="100000", window="1s", max_duration_s="2.4", max_concurrency="16"
    )
    ir = state["inference_results"]
    assert ir["limit_reached"] == 0
    assert ir["not_throttled_bound"] == "response"
    [finding] = state["_findings"]
    assert finding["title"] == "Couldn't reach the limit"
    assert "can't answer fast enough" in finding["text"]
    assert state["_verdict_text"].startswith("Inconclusive")


def test_diagnosis_names_the_bottleneck() -> None:
    from harness.tasks.inference import _diagnose_unthrottled

    def h(c: int, tps: float, p50: float = 50.0) -> dict:
        return {"t": 0, "concurrency": c, "tokens_per_s": tps, "p50_ms": p50}

    still_scaling = [h(1, 100), h(2, 200), h(4, 400)]
    plateaued = [h(1, 100), h(2, 180), h(4, 190, 400)]
    assert _diagnose_unthrottled(still_scaling, 1000, 4, "time", 60000, "1m")[0] == "send"
    assert _diagnose_unthrottled(plateaued, 1000, 128, "time", 60000, "1m")[0] == "response"
    assert _diagnose_unthrottled(still_scaling, 1000, 128, "time", 60000, "1m")[0] == "time"
    assert _diagnose_unthrottled(still_scaling, 1000, 128, "requests", 60000, "1m")[0] == "requests"
    bound, text = _diagnose_unthrottled(still_scaling, 1000, 4, "time", 60000, "1m")
    assert "needs ~1,000 tokens/s" in text and "Max concurrency" in text


async def test_chart_flag_and_insecure_tls_reach_the_client() -> None:
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls, \
         patch("harness.tasks.inference.DefaultAsyncHttpxClient") as http_cls:
        mock_cls.return_value = _mock_client()
        ctx = _make_ctx({"deployed_models": [{"name": "sim-1", "namespace": "llm",
                                              "direct_url": "https://sim-1-direct-llm.apps.example.test/v1"}]})
        await SendRequestsTask(
            "send_requests",
            {"count": "1", "url_from_shared_state": "deployed_models", "insecure_tls": "true", "chart": "true", "token": "x"},
        ).run(ctx)
    assert http_cls.call_args.kwargs["verify"] is False
    _assert_client_built_with(mock_cls, api_key="x", base_url="https://sim-1-direct-llm.apps.example.test/v1")
    assert mock_cls.return_value.chat.completions.create.call_args.kwargs["model"] == "sim-1"
    assert ctx.shared_state["_traffic"]["inference_results"]["chart"] is True


async def test_error_samples_name_the_most_common_failure() -> None:
    state = await _run_sequence([_api_status_error(404)] * 3 + [_api_status_error(503), _api_status_error(429)])
    ir = state["inference_results"]
    assert ir["not_found_count"] == 3
    assert ir["other_error_count"] == 0
    assert ir["error_samples"][0]["count"] == 3
    assert ir["error_samples"][0]["message"].startswith("HTTP 404")
    # Throttling isn't an error reason — it has its own counters.
    assert all("429" not in e["message"] for e in ir["error_samples"])
    assert "most common error: 3× HTTP 404" in state["task_summary"]
    assert [p[2] for p in state["_traffic"]["inference_results"]["timeline"]][:3] == ["not_found"] * 3


async def test_connection_errors_report_the_hidden_cause() -> None:
    from openai import APIConnectionError

    request = httpx.Request("POST", "https://x/v1/chat/completions")
    try:
        try:
            raise httpx.ConnectError("Name or service not known")
        except httpx.ConnectError as cause:
            raise APIConnectionError(request=request) from cause  # type: ignore[arg-type]
    except APIConnectionError as exc:
        conn_error = exc

    state = await _run_sequence([conn_error])
    [sample] = state["inference_results"]["error_samples"]
    assert sample["message"] == "ConnectError: Name or service not known"


async def test_skip_unless_skips_when_precondition_missing() -> None:
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        ctx = _make_ctx({"direct_probe": {"reachable": 0}})
        result = await SendRequestsTask(
            "send_requests", {"count": "5", "url": "http://m.test", "token": "x", "skip_unless": "direct_probe.reachable"}
        ).run(ctx)
    assert result.status == "PASS"
    mock_cls.assert_not_called()
    assert ctx.shared_state["task_summary"].startswith("Skipped")


async def test_step_load_narration_names_the_step_not_the_request_cap() -> None:
    limiter = _WindowedLimiter(limit=10**9, window_s=60.0, latency_s=0.05)
    summaries: list[str] = []

    async def _emit() -> None:
        summaries.append(ctx.shared_state.get("task_summary", ""))

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = limiter.create
        mock_cls.return_value = m
        ctx = _make_ctx()
        ctx.emit_assertion_state = _emit
        await SendRequestsTask(
            "send_requests",
            {"url": "http://m.test", "token": "sk-t", "stages": "1,2", "stage_duration_s": "0.3"},
        ).run(ctx)
    live = [s for s in summaries if s.startswith("Step")]
    assert live and all("1000000000" not in s for s in summaries)
    assert any(s.startswith("Step 2/2: 2 in flight") for s in live)


async def test_step_load_reports_each_concurrency_step() -> None:
    """A slow model (2 slots, 50 ms each ≈ 40 req/s ceiling): throughput
    rises from 1 to 2 in flight, then flattens while latency climbs at 4."""
    limiter = _WindowedLimiter(limit=10**9, window_s=60.0, latency_s=0.05, server_slots=2)
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = limiter.create
        mock_cls.return_value = m
        ctx = _make_ctx()
        await SendRequestsTask(
            "send_requests",
            {"url": "http://m.test", "token": "sk-t", "stages": "1,2,4", "stage_duration_s": "0.5"},
        ).run(ctx)

    ir = ctx.shared_state["inference_results"]
    stages = ir["stages"]
    assert [st["concurrency"] for st in stages] == [1, 2, 4]
    assert stages[1]["requests_per_s"] > stages[0]["requests_per_s"] * 1.5
    assert stages[2]["p50_latency_ms"] > stages[1]["p50_latency_ms"] * 1.5
    assert ir["final_stage_p99_latency_ms"] == stages[-1]["p99_latency_ms"]
    assert ir["final_stage_throttled_pct"] == 0
    assert "3 steps up to 4 in flight" in ctx.shared_state["task_summary"]
    # The internal request cap must never surface as a "planned" count.
    assert ctx.shared_state["_traffic"]["inference_results"]["planned"] is None


async def test_bursts_carry_their_chart_group_label_and_start_time() -> None:
    state = await _run_sequence([_usage_response()], chart_group="recovery", label="Before the wait")
    entry = state["_traffic"]["inference_results"]
    assert (entry["chart_group"], entry["label"]) == ("recovery", "Before the wait")
    assert entry["t0"] > 0


async def test_error_samples_carry_how_long_each_failure_took() -> None:
    state = await _run_sequence([_usage_response(), _api_status_error(500), _api_status_error(500)])
    sample = state["inference_results"]["error_samples"][0]
    assert sample["count"] == 2
    assert {"median_ms", "p10_ms", "p90_ms"} <= sample.keys()


def test_errors_note_flags_a_constant_failure_time_as_a_timeout() -> None:
    from harness.tasks.inference import _errors_note

    result: dict[str, Any] = {
        "fail_count": 77, "rate_limited_count": 0,
        "error_samples": [{"message": "HTTP 500 Internal Server Error", "count": 77,
                           "median_ms": 202.0, "p10_ms": 200.5, "p90_ms": 215.0}],
    }
    note = _errors_note(result)
    assert note is not None and note.startswith("77 requests failed")
    assert "about 202 ms" in note and "timeout" in note
    # Spread-out failure times are reported without the timeout reading.
    result["error_samples"][0].update(p10_ms=50.0, p90_ms=900.0)
    assert "timeout" not in (_errors_note(result) or "")
    # Throttling alone isn't a failure worth a note.
    assert _errors_note({"fail_count": 3, "rate_limited_count": 3, "error_samples": []}) is None
    # Failures the SDK retried away are reported too, even with no visible failure.
    retried = _errors_note({
        "fail_count": 0, "rate_limited_count": 0, "retried_failed_attempts": 1381,
        "attempt_error_samples": [{"message": "HTTP 500 Internal Server Error", "count": 1381,
                                   "median_ms": 202.0, "p10_ms": 200.0, "p90_ms": 220.0}],
    })
    assert retried is not None and retried.startswith("The SDK also retried 1,381 failed attempts")
    assert "timeout" in retried


async def test_step_load_with_failures_adds_a_verdict_note() -> None:
    calls = {"n": 0}

    async def create(**_):
        calls["n"] += 1
        await asyncio.sleep(0.005)  # a real call yields; lets the step timer run
        if calls["n"] % 4 == 0:
            raise _api_status_error(500)
        return _usage_response()

    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        m = MagicMock()
        m.chat.completions.create = create
        mock_cls.return_value = m
        ctx = _make_ctx()
        await SendRequestsTask(
            "send_requests",
            {"url": "http://m.test", "token": "sk-t", "stages": "1", "stage_duration_s": "0.2"},
        ).run(ctx)
    assert "failed, most often" in ctx.shared_state["_verdict_note"]


async def test_show_limit_false_hides_the_chart_limit_line() -> None:
    state = await _run_sequence([_usage_response()], show_limit="false", limit="1000")
    assert state["_traffic"]["inference_results"]["limit"] is None


async def test_failed_attempts_the_sdk_retried_are_counted_and_described() -> None:
    """Retries stay on (the real client experience); the attempts they hid
    are still counted, with what they got back. Drives a real httpx
    transport so the SDK's own retry loop runs."""
    # The OpenAI SDK's client is built on httpx2 (an httpx fork), so the
    # mock transport has to come from there too.
    httpx2 = pytest.importorskip("httpx2")
    calls = {"n": 0}

    def handler(request):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            return httpx2.Response(500, text="Internal Server Error")
        return httpx2.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        })

    from openai import DefaultAsyncHttpxClient

    def client_with_mock_transport(**kwargs):
        return DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler), **kwargs)

    with patch("harness.tasks.inference.DefaultAsyncHttpxClient", client_with_mock_transport), \
         patch("openai._base_client.AsyncAPIClient._calculate_retry_timeout", return_value=0.0):
        ctx = _make_ctx()
        await SendRequestsTask(
            "send_requests", {"url": "http://m.test/v1", "token": "sk-t", "count": "3", "concurrency": "1"}
        ).run(ctx)
    r = ctx.shared_state["inference_results"]
    assert r["success_count"] == 3 and r["fail_count"] == 0
    assert r["http_attempts"] == 6
    assert r["retried_failed_attempts"] == 3
    assert r["attempt_error_samples"][0]["message"] == "HTTP 500 Internal Server Error"
    assert r["attempt_error_samples"][0]["count"] == 3


# --- Request API / Streaming (the launch form's advanced settings) ---------


def _usage(prompt: int, completion: int) -> MagicMock:
    u = MagicMock()
    u.prompt_tokens, u.completion_tokens, u.total_tokens = prompt, completion, prompt + completion
    return u


class _Stream:
    """An async iterator standing in for the SDK's AsyncStream."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks

    def __aiter__(self) -> "_Stream":
        self._it = iter(self._chunks)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


def _chunk(text: str | None = None, usage: MagicMock | None = None, *, completions: bool = False) -> MagicMock:
    c = MagicMock()
    if text is None:
        c.choices = []
    else:
        choice = MagicMock()
        if completions:
            choice.text = text
        else:
            choice.delta.content = text
        c.choices = [choice]
    c.usage = usage
    return c


async def _run(params: dict, client: MagicMock, config: dict | None = None) -> dict:
    with patch("harness.tasks.inference.AsyncOpenAI", return_value=client):
        ctx = _make_ctx()
        ctx.config.update(config or {})
        await SendRequestsTask(
            "send_requests", {"count": "2", "concurrency": "1", "url": "http://m.test", "token": "sk-t", **params}
        ).run(ctx)
    return ctx.shared_state["inference_results"]


async def test_completions_api_sends_prompt_and_counts_usage() -> None:
    m = MagicMock()
    m.completions.create = AsyncMock(return_value=MagicMock(usage=_usage(4, 6)))
    ir = await _run({"api": "completions"}, m)
    assert m.completions.create.await_args.kwargs["prompt"] == "Hello"
    assert ir["api"] == "completions" and ir["stream"] is False
    assert ir["total_tokens_sent"] == 20 and ir["completion_tokens_sent"] == 12


async def test_batched_completions_send_a_list_of_prompts() -> None:
    m = MagicMock()
    m.completions.create = AsyncMock(return_value=MagicMock(usage=_usage(4, 6)))
    await _run({"api": "completions", "batch_size": "3", "prompt": "p"}, m)
    assert m.completions.create.await_args.kwargs["prompt"] == ["p", "p", "p"]


async def test_streamed_chat_counts_usage_from_the_final_chunk_and_times_first_token() -> None:
    m = MagicMock()
    m.chat.completions.create = AsyncMock(
        side_effect=lambda **kw: _Stream([_chunk(""), _chunk("Hi"), _chunk(" there"), _chunk(None, _usage(5, 2))])
    )
    ir = await _run({"api": "chat_completions", "stream": "true"}, m)
    kwargs = m.chat.completions.create.await_args.kwargs
    assert kwargs["stream"] is True and kwargs["stream_options"] == {"include_usage": True}
    assert ir["stream"] is True
    assert ir["total_tokens_sent"] == 14 and ir["success_count"] == 2
    assert "p50_ttft_ms" in ir and ir["usage_missing_count"] == 0


async def test_streamed_completions_without_usage_are_counted_as_missing() -> None:
    m = MagicMock()
    m.completions.create = AsyncMock(side_effect=lambda **kw: _Stream([_chunk("Hi", completions=True)]))
    ir = await _run({"api": "completions", "stream": "true"}, m)
    assert ir["success_count"] == 2
    assert ir["total_tokens_sent"] == 0 and ir["usage_missing_count"] == 2


async def test_streamed_responses_read_usage_from_the_completed_event() -> None:
    delta = MagicMock(type="response.output_text.delta")
    done = MagicMock(type="response.completed")
    done.response.usage = MagicMock(input_tokens=3, output_tokens=4, total_tokens=7)
    m = MagicMock()
    m.responses.create = AsyncMock(side_effect=lambda **kw: _Stream([delta, done]))
    ir = await _run({"api": "responses", "stream": "true"}, m)
    assert ir["total_tokens_sent"] == 14 and ir["prompt_tokens_sent"] == 6
    assert "p50_ttft_ms" in ir


async def test_embeddings_never_stream() -> None:
    m = MagicMock()
    usage = MagicMock(prompt_tokens=5, total_tokens=5, spec=["prompt_tokens", "total_tokens"])
    m.embeddings.create = AsyncMock(return_value=MagicMock(usage=usage))
    ir = await _run({"api": "embeddings", "stream": "true"}, m)
    assert ir["stream"] is False and ir["total_tokens_sent"] == 10
    assert "stream" not in m.embeddings.create.await_args.kwargs


async def test_request_api_falls_back_to_the_launch_settings() -> None:
    """A step that builds its own params (send_requests_to_each_model) still
    follows the scenario's Request API / Streaming settings."""
    m = MagicMock()
    m.completions.create = AsyncMock(side_effect=lambda **kw: _Stream([_chunk(None, _usage(1, 1))]))
    ir = await _run({}, m, config={"request_api": "completions", "stream": "True"})
    assert ir["api"] == "completions" and ir["stream"] is True


async def test_unknown_request_api_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown request api"):
        await _run({"api": "batches"}, MagicMock())


async def test_failed_requests_record_who_answered() -> None:
    request = httpx.Request("POST", "http://m.test/v1/completions")
    response = httpx.Response(404, request=request, json={"object": "error", "message": "nope"})
    exc = APIStatusError("404", response=response, body=None)  # type: ignore[arg-type]
    m = MagicMock()
    m.completions.create = AsyncMock(side_effect=exc)
    ir = await _run({"api": "completions"}, m)
    assert ir["failure_origins"] == {"model": 2}


@pytest.mark.parametrize(
    ("status", "headers", "text", "origin"),
    [
        (403, {"x-ext-auth-reason": "no policy"}, "", "gateway"),
        (429, {}, "", "gateway"),
        (404, {"server": "fasthttp"}, "Not Found", "model"),
        (400, {"server": "uvicorn"}, "bad", "model"),
        (404, {"server": "istio-envoy"}, "", "gateway"),
        (404, {}, '{"object": "error", "message": "x"}', "model"),
        (404, {}, '{"detail": "Not Found"}', "model"),
        (400, {}, '{"error": {"message": "bad", "type": "BadRequestError"}}', "model"),
        (401, {}, '{"error": "denied"}', "gateway"),
        (404, {}, "", "gateway"),
        (503, {}, "no healthy upstream", "unknown"),
        (400, {}, '{"something": "else"}', "unknown"),
    ],
)
def test_failure_origin(status: int, headers: dict, text: str, origin: str) -> None:
    from harness.tasks.inference import failure_origin

    assert failure_origin(status, headers, text)[0] == origin


class _SlowStream(_Stream):
    """Chunks arriving `gap_s` apart, like a model generating token by token."""

    def __init__(self, chunks: list, gap_s: float) -> None:
        super().__init__(chunks)
        self._gap_s = gap_s

    async def __anext__(self) -> Any:
        await asyncio.sleep(self._gap_s)
        return await super().__anext__()


async def test_stream_delivery_streamed_vs_all_at_once() -> None:
    from harness.tasks.inference import delivery_text

    chunks = [_chunk("a"), _chunk("b"), _chunk("c"), _chunk(None, _usage(1, 3))]
    m = MagicMock()
    m.chat.completions.create = AsyncMock(side_effect=lambda **kw: _SlowStream(list(chunks), 0.02))
    paced = await _run({"stream": "true"}, m)
    assert paced["streamed_reply_count"] == 2 and paced["all_at_once_reply_count"] == 0
    assert paced["p50_stream_chunks"] == 3
    assert paced["p50_tpot_ms"] >= 15  # ~20 ms between 3 tokens
    assert 15 <= paced["p50_itl_ms"] <= paced["p95_itl_ms"]
    assert delivery_text(paced).startswith("✓ Streamed — 3 chunks over")

    m.chat.completions.create = AsyncMock(side_effect=lambda **kw: _Stream(list(chunks)))
    burst = await _run({"stream": "true"}, m)
    assert burst["all_at_once_reply_count"] == 2 and burst["streamed_reply_count"] == 0
    assert delivery_text(burst).startswith("⚠ All at once — 3 chunks within")
    assert "p50_ms_per_output_token" in burst


async def test_timeout_and_stop_after_a_hang() -> None:
    """A request that gets no HTTP answer stops the burst when asked to, and
    the per-request timeout reaches the SDK client."""
    m = MagicMock()
    m.chat.completions.create = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
    with patch("harness.tasks.inference.AsyncOpenAI", return_value=m) as mock_cls:
        ctx = _make_ctx()
        await SendRequestsTask(
            "send_requests",
            {"count": "3", "concurrency": "1", "url": "http://m.test", "token": "sk-t",
             "timeout_s": "7", "stop_after_transport_errors": "1"},
        ).run(ctx)
    assert mock_cls.call_args.kwargs["timeout"] == 7.0
    ir = ctx.shared_state["inference_results"]
    assert ir["total_requests"] == 1 and ir["transport_error_count"] == 1
