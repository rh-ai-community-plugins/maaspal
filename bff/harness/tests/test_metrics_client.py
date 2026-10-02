import httpx
from pytest_httpx import HTTPXMock

from harness.metrics_client import fetch_metrics

_BASE = "http://thanos.test/api/v1/query"


def _q(query: str) -> str:
    """Build the exact URL httpx will request for a given PromQL query string."""
    return str(httpx.URL(_BASE, params={"query": query}))


async def test_fetch_metrics_no_base_url() -> None:
    assert await fetch_metrics("", {"total_requests": "sum(foo)"}, "token") == {}


async def test_fetch_metrics_no_queries() -> None:
    assert await fetch_metrics(_BASE, {}, "token") == {}


async def test_fetch_metrics_single_query_success(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_q("sum(foo)"),
        json={"data": {"result": [{"metric": {}, "value": [1234567890, "42"]}]}},
    )

    result = await fetch_metrics(_BASE, {"total_requests": "sum(foo)"}, "token")
    assert result == {"total_requests": 42.0}


async def test_fetch_metrics_multiple_queries(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_q("sum(requests)"), json={"data": {"result": [{"value": [1, "10"]}]}}
    )
    httpx_mock.add_response(
        url=_q("sum(tokens)"), json={"data": {"result": [{"value": [1, "500"]}]}}
    )

    result = await fetch_metrics(
        _BASE,
        {"total_requests": "sum(requests)", "total_tokens": "sum(tokens)"},
        "token",
    )
    assert result == {"total_requests": 10.0, "total_tokens": 500.0}


async def test_fetch_metrics_scalar_result_type(httpx_mock: HTTPXMock) -> None:
    """A query with no metric selector (e.g. a bare ${harness.x}-substituted literal,
    ADR-015's promql pass-through form) evaluates to resultType "scalar", whose
    result is a bare [timestamp, value] pair rather than a list of series."""
    httpx_mock.add_response(
        url=_q("2.5"),
        json={"data": {"resultType": "scalar", "result": [1234567890, "2.5"]}},
    )

    result = await fetch_metrics(_BASE, {"error_rate_pct": "2.5"}, "token")
    assert result == {"error_rate_pct": 2.5}


async def test_fetch_metrics_empty_result_omitted(httpx_mock: HTTPXMock) -> None:
    """A query with no matching series ('result': []) is omitted, not zero-filled."""
    httpx_mock.add_response(url=_q("sum(nothing)"), json={"data": {"result": []}})

    result = await fetch_metrics(_BASE, {"total_requests": "sum(nothing)"}, "token")
    assert result == {}


async def test_fetch_metrics_http_error_omitted(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=_q("sum(foo)"), status_code=500)

    result = await fetch_metrics(_BASE, {"total_requests": "sum(foo)"}, "token")
    assert result == {}


async def test_fetch_metrics_sends_bearer_token(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=_q("sum(foo)"), json={"data": {"result": [{"value": [1, "1"]}]}}
    )

    await fetch_metrics(_BASE, {"total_requests": "sum(foo)"}, "tok-123")

    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == "Bearer tok-123"
