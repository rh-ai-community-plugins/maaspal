import httpx
import pytest
from pytest_httpx import HTTPXMock

from harness.tasks.base import TaskContext
from harness.tasks.metrics import CheckMaasMetricsTask

_BASE = "http://thanos.test/api/v1/query"


def _q(query: str) -> str:
    return str(httpx.URL(_BASE, params={"query": query}))


def _make_ctx(
    config: dict | None = None,
    shared_state: dict | None = None,
    metrics_queries: dict | None = None,
) -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="test-001",
        scenario_name="test",
        maas_api_url="http://maas.test",
        sa_token="test-token",
        shared_state=shared_state or {},
        config=config or {},
        assertions={},
        emit_assertion_state=_emit,
        metrics_queries=metrics_queries or {},
    )


async def test_check_maas_metrics_stub_when_unconfigured(capsys: pytest.CaptureFixture) -> None:
    task = CheckMaasMetricsTask("check_maas_metrics", {})
    ctx = _make_ctx()
    result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["metrics"]["total_requests"] == 0
    assert ctx.shared_state["metrics"]["total_tokens"] == 0
    out = capsys.readouterr().out
    assert "stub zeroes" in out


async def test_check_maas_metrics_prints_summary(capsys: pytest.CaptureFixture) -> None:
    task = CheckMaasMetricsTask("check_maas_metrics", {})
    ctx = _make_ctx()
    await task.run(ctx)
    out = capsys.readouterr().out
    assert "total_requests" in out
    assert "total_tokens" in out


async def test_check_maas_metrics_stores_in_shared_state() -> None:
    task = CheckMaasMetricsTask("check_maas_metrics", {})
    ctx = _make_ctx()
    await task.run(ctx)
    assert "metrics" in ctx.shared_state


async def test_check_maas_metrics_fetches_via_configured_queries(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_q("sum(requests)"), json={"data": {"result": [{"value": [1, "42"]}]}}
    )
    httpx_mock.add_response(
        url=_q("sum(tokens)"), json={"data": {"result": [{"value": [1, "100"]}]}}
    )

    task = CheckMaasMetricsTask("check_maas_metrics", {})
    ctx = _make_ctx(
        config={"MAAS_METRICS_URL": _BASE},
        metrics_queries={"total_requests": "sum(requests)", "total_tokens": "sum(tokens)"},
    )
    result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["metrics"]["total_requests"] == 42
    assert ctx.shared_state["metrics"]["total_tokens"] == 100


async def test_check_maas_metrics_task_params_override_config(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_q("sum(override)"), json={"data": {"result": [{"value": [1, "7"]}]}}
    )

    task = CheckMaasMetricsTask(
        "check_maas_metrics",
        {"metrics_url": _BASE, "queries": {"total_requests": "sum(override)"}},
    )
    ctx = _make_ctx(config={"MAAS_METRICS_URL": "http://ignored.test"}, metrics_queries={})
    result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["metrics"]["total_requests"] == 7


async def test_check_maas_metrics_handles_fetch_error(capsys: pytest.CaptureFixture) -> None:
    """Unreachable metrics endpoint: query omitted, task still passes."""
    task = CheckMaasMetricsTask(
        "check_maas_metrics",
        {"metrics_url": "http://unreachable.test/api/v1/query", "queries": {"total_requests": "sum(foo)"}},
    )
    ctx = _make_ctx()
    result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["metrics"] == {}


async def test_check_maas_metrics_cleanup_is_noop() -> None:
    task = CheckMaasMetricsTask("check_maas_metrics", {})
    ctx = _make_ctx()
    await task.cleanup(ctx)
