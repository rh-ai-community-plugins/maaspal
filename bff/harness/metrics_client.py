"""Shared client for querying MaaS/RHOAI metrics via a Prometheus-compatible instant-query API.

Used by both the background poller (harness/runner.py) and the optional explicit
check_maas_metrics task (harness/tasks/metrics.py) so there's a single place that knows
how to talk to Thanos Querier / Prometheus and parse its response shape.

See docs/architecture/adrs/ADR-014-maas-metrics-cross-validation.md and ADR-015 for
the confirmed real MAAS_METRICS_URL / metrics_queries values.
"""

import traceback
from typing import Any

import httpx


def _extract_scalar(response_json: dict[str, Any]) -> float | None:
    """Extract a single scalar value from a Prometheus instant-query response.

    A query with a metric selector (e.g. sum(authorized_calls{...})) evaluates to an
    instant vector: {"data": {"resultType": "vector", "result": [{"value": [ts, "v"]}]}}.
    A pure-arithmetic expression with no metric selector at all — a bare number, or a
    promql-form assertion whose whole text is a substituted ${harness.x}/${baseline.x}
    literal (see ADR-015) — evaluates to a scalar instead:
    {"data": {"resultType": "scalar", "result": [ts, "v"]}}, a bare 2-element array
    rather than a list of series. Handle both, since scenario authors are free to write
    either kind of PromQL.
    """
    data = response_json.get("data") or {}
    result = data.get("result")
    if not result:
        return None
    if data.get("resultType") == "scalar":
        value = result
    elif isinstance(result, list):
        value = result[0].get("value")
    else:
        return None
    if not value or len(value) < 2:
        return None
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return None


async def fetch_metrics(base_url: str, queries: dict[str, str], token: str) -> dict[str, float]:
    """Run each named PromQL query as an instant query and return {name: scalar_value}.

    A query that errors or returns no result is omitted from the result (not zero-filled),
    so callers/assertions correctly see it as PENDING rather than a false zero.
    """
    if not base_url or not queries:
        return {}

    metrics: dict[str, float] = {}
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, query in queries.items():
            try:
                resp = await client.get(
                    base_url,
                    params={"query": query},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                value = _extract_scalar(resp.json())
                if value is not None:
                    metrics[name] = value
            except Exception:
                print(
                    f"[metrics_client] query {name!r} failed\n{traceback.format_exc()}",
                    flush=True,
                )
    return metrics
