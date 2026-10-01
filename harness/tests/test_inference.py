import time
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
        f"status {status_code}", response=response, body={"error": "denied"}
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
    calls_by_key: dict[str, int] = {}

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
    calls_by_key: dict[str, int] = {}

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

    mock_cls.assert_called_with(api_key="sk-key-2", base_url="http://m.test")


async def test_key_index_overrides_static_token_param() -> None:
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        ctx = _make_ctx({"api_keys": [{"id": "id-1", "key": "sk-from-pool"}]})
        task = SendRequestsTask(
            "send_requests",
            {"count": "1", "url": "http://m.test", "token": "sk-static", "key_index": 0},
        )
        await task.run(ctx)

    mock_cls.assert_called_with(api_key="sk-from-pool", base_url="http://m.test")


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
    mock_cls.assert_called_with(
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

    mock_cls.assert_called_with(
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

    mock_cls.assert_called_with(api_key="sk-explicit", base_url="http://explicit.test")


async def test_url_resolution_shared_state_fallback() -> None:
    """shared_state used when explicit params are absent."""
    with patch("harness.tasks.inference.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = _mock_client()

        task = SendRequestsTask("send_requests", {"count": "1"})
        ctx = _make_ctx({"url": "http://shared.test", "token": "sk-shared"})
        await task.run(ctx)

    mock_cls.assert_called_with(api_key="sk-shared", base_url="http://shared.test")


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

    mock_cls.assert_called_with(
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

    mock_cls.assert_called_with(
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
