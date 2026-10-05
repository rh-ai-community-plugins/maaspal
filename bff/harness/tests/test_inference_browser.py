"""send_requests with `from_browser`: the open run page sends, this pod tallies
what it reports (harness/browser_channel.py ↔ api/routes/browser.py)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError

from harness import browser_channel
from harness.tasks.base import TaskContext
from harness.tasks.inference import SendRequestsTask

_KEYS = [{"id": "k1", "key": "sk-oai-one"}, {"id": "k2", "key": "sk-oai-two"}]


@pytest.fixture()
def results_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path / "results"


def _ctx(config: dict | None = None) -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="run-1",
        scenario_name="test",
        maas_api_url="http://maas.test",
        sa_token="sa-token",
        shared_state={"api_keys": [dict(k) for k in _KEYS]},
        config=config or {},
        assertions={},
        emit_assertion_state=_emit,
    )


def _ok(offset: float, tokens: int = 30, arrivals: list[float] | None = None) -> dict:
    return {
        "t0_offset_s": offset, "latency_ms": 40.0, "ok": True, "status": 200,
        "usage": {"prompt_tokens": 10, "completion_tokens": tokens - 10, "total_tokens": tokens},
        "arrivals": arrivals or [], "attempts": [{"status": 200, "ms": 40.0}],
    }


def _failed(offset: float, status: int, body: str = "") -> dict:
    return {
        "t0_offset_s": offset, "latency_ms": 5.0, "status": status, "reason": "",
        "body": body, "attempts": [{"status": status, "ms": 5.0}],
    }


async def _browser(results_dir: Path, records: list[dict], *, reason: str = "finished", detail: str = "") -> dict:
    """Wait for the order, claim it, report `records`, then done."""
    while (found := browser_channel.find_order(results_dir, "run-1")) is None:
        await asyncio.sleep(0.01)
    result_key, order = found
    assert browser_channel.try_claim(results_dir, "run-1", result_key, {"claim_id": "c", "origin": "https://rh-ai.test"})
    browser_channel.append_records(results_dir, "run-1", result_key, records)
    browser_channel.append_records(
        results_dir, "run-1", result_key, [{"done": True, "reason": reason, "detail": detail}]
    )
    return order


async def _run(task: SendRequestsTask, ctx: TaskContext, results_dir: Path, records: list[dict], **kw):
    browser = asyncio.create_task(_browser(results_dir, records, **kw))
    result = await task.run(ctx)
    return result, await browser


async def test_order_names_every_key_round_robin_and_is_removed_after(results_dir) -> None:
    task = SendRequestsTask("send_requests", {
        "count": 3, "concurrency": 2, "prompt": "Hi", "key_pool": True, "from_browser": True,
        "model": "m1", "url": "http://maas.test/v1",
    })
    ctx = _ctx()
    result, order = await _run(task, ctx, results_dir, [_ok(0.0), _ok(0.1), _ok(0.2)])

    assert result.status == "PASS"
    assert [t["key"] for t in order["targets"]] == ["sk-oai-one", "sk-oai-two"]
    assert order["plan"] == [0, 1, 0]
    assert order["path"] == "/chat/completions"
    # The keys never outlive the step on the PVC.
    assert not browser_channel.order_path(results_dir, "run-1", "inference_results").exists()
    assert not browser_channel.claim_path(results_dir, "run-1", "inference_results").exists()
    traffic = ctx.shared_state["_traffic"]["inference_results"]
    assert traffic["origin"] == "browser"


async def test_browser_records_tally_like_requests_sent_from_the_pod(results_dir) -> None:
    """Same outcomes, same numbers — whichever side sent them."""
    def _usage_reply(*_a, **_k) -> MagicMock:
        r = MagicMock()
        r.usage.total_tokens, r.usage.prompt_tokens, r.usage.completion_tokens = 30, 10, 20
        return r

    request = httpx.Request("POST", "http://m.test/v1/chat/completions")
    throttled = APIStatusError(
        "429", response=httpx.Response(429, request=request), body=None  # type: ignore[arg-type]
    )
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=[_usage_reply(), _usage_reply(), throttled])
    params = {"count": 3, "concurrency": 1, "prompt": "Hi", "url": "http://m.test", "token": "sk-t", "retries": 0}
    with patch("harness.tasks.inference.AsyncOpenAI", return_value=client):
        pod_ctx = _ctx()
        await SendRequestsTask("send_requests", dict(params)).run(pod_ctx)

    browser_ctx = _ctx()
    await _run(
        SendRequestsTask("send_requests", {**params, "from_browser": True}),
        browser_ctx, results_dir, [_ok(0.0), _ok(0.05), _failed(0.1, 429)],
    )

    pod, browser = pod_ctx.shared_state["inference_results"], browser_ctx.shared_state["inference_results"]
    for key in (
        "total_requests", "success_count", "fail_count", "rate_limited_count", "total_tokens_sent",
        "prompt_tokens_sent", "completion_tokens_sent", "tokens_before_first_429",
        "requests_before_first_429", "error_rate_pct", "failure_origins",
    ):
        assert browser[key] == pod[key], key


async def test_failures_and_streams_from_the_browser(results_dir) -> None:
    ctx = _ctx()
    records = [
        _ok(0.0, arrivals=[100.0, 150.0, 200.0]),
        _failed(0.1, 401),
        _failed(0.2, 500, '{"object": "error", "message": "boom"}'),
        {"t0_offset_s": 0.3, "latency_ms": 1.0, "error": "TypeError: Failed to fetch"},
    ]
    await _run(SendRequestsTask("send_requests", {
        "count": 4, "url": "http://m.test", "token": "sk-t", "from_browser": True, "stream": True,
    }), ctx, results_dir, records)

    r = ctx.shared_state["inference_results"]
    assert (r["success_count"], r["unauthorized_count"], r["server_error_count"]) == (1, 1, 1)
    assert r["transport_error_count"] == 1
    assert r["p50_ttft_ms"] == 100.0
    assert r["failure_origins"] == {"gateway": 1, "model": 1}
    assert r["http_attempts"] == 3
    assert any(s["message"] == "TypeError: Failed to fetch" for s in r["error_samples"])


async def test_blocked_by_the_browser_fails_the_step_with_a_finding(results_dir) -> None:
    ctx = _ctx()
    blocked = {"t0_offset_s": 0.0, "latency_ms": 3.0, "error": "Blocked by the browser (CORS)"}
    result, _ = await _run(SendRequestsTask("send_requests", {
        "count": 3, "url": "http://m.test", "token": "sk-t", "from_browser": True,
    }), ctx, results_dir, [blocked], reason="blocked", detail="no Access-Control-Allow-Origin")

    assert result.status == "FAIL"
    assert "cross-origin" in (result.error or "")
    assert ctx.shared_state["_findings"][0]["title"] == "Blocked by the browser"
    assert "cross-origin" in ctx.shared_state["_verdict_text"]
    assert ctx.shared_state["inference_results"]["transport_error_count"] == 1


async def test_no_browser_picks_it_up(results_dir) -> None:
    ctx = _ctx()
    result = await SendRequestsTask("send_requests", {
        "count": 2, "url": "http://m.test", "token": "sk-t", "from_browser": True,
        "browser_claim_timeout_s": 0.3,
    }).run(ctx)

    assert result.status == "FAIL"
    assert "keep the run page open" in (result.error or "")
    assert not browser_channel.order_path(results_dir, "run-1", "inference_results").exists()


async def test_browser_goes_quiet(results_dir) -> None:
    async def _claim_then_vanish() -> None:
        while (found := browser_channel.find_order(results_dir, "run-1")) is None:
            await asyncio.sleep(0.01)
        browser_channel.try_claim(results_dir, "run-1", found[0], {"claim_id": "c"})

    vanishing = asyncio.create_task(_claim_then_vanish())
    result = await SendRequestsTask("send_requests", {
        "count": 2, "url": "http://m.test", "token": "sk-t", "from_browser": True,
        "browser_idle_timeout_s": 0.3,
    }).run(_ctx())
    await vanishing

    assert result.status == "FAIL"
    assert "stopped reporting" in (result.error or "")


async def test_stop_mid_step_removes_the_order(results_dir) -> None:
    task = asyncio.create_task(SendRequestsTask("send_requests", {
        "count": 2, "url": "http://m.test", "token": "sk-t", "from_browser": True,
    }).run(_ctx()))
    while browser_channel.find_order(results_dir, "run-1") is None:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert browser_channel.find_order(results_dir, "run-1") is None


async def test_follows_the_launch_form_setting(results_dir) -> None:
    ctx = _ctx({"send_from_browser": True})
    _, order = await _run(SendRequestsTask("send_requests", {
        "count": 1, "url": "http://m.test", "token": "sk-t",
    }), ctx, results_dir, [_ok(0.0)])
    assert order["targets"] == [{"url": "http://m.test", "model": order["targets"][0]["model"], "key": "sk-t"}]


async def test_never_hands_the_service_account_token_to_a_browser(results_dir) -> None:
    with pytest.raises(ValueError, match="ServiceAccount token"):
        await SendRequestsTask("send_requests", {
            "count": 1, "url": "http://m.test", "model": "m", "from_browser": True,
        }).run(_ctx())


@pytest.mark.parametrize("extra", [{"until_throttled": True}, {"stages": "1,2"}, {"insecure_tls": True}])
async def test_unsupported_modes_say_so(results_dir, extra) -> None:
    with pytest.raises(ValueError, match="fixed bursts only"):
        await SendRequestsTask("send_requests", {
            "count": 1, "url": "http://m.test", "token": "sk-t", "from_browser": True, **extra,
        }).run(_ctx())
