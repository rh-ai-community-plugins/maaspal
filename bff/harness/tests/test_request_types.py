from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIStatusError

from harness.tasks.base import TaskContext
from harness.tasks.request_types import (
    REQUEST_TYPES,
    SendRequestsEachTypeTask,
    attribute,
    selected_types,
)

_MAAS = "http://maas.test/ns/m/v1"


def _ctx() -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="r1", scenario_name="request_types", maas_api_url="http://maas.test", sa_token="sa",
        shared_state={"api_keys": [{"key": "sk-oai-1"}]}, config={}, assertions={}, emit_assertion_state=_emit,
    )


def _result(ok: int, total: int = 3, **extra: object) -> dict:
    return {"success_count": ok, "total_requests": total, "fail_count": total - ok, **extra}


def test_selected_types_defaults_to_all_and_rejects_unknown() -> None:
    assert selected_types("") == REQUEST_TYPES
    assert [t.id for t in selected_types("chat, embeddings")] == ["chat", "embeddings"]
    with pytest.raises(ValueError, match="batches"):
        selected_types("chat,batches")


def test_a_400_from_the_model_both_ways_is_the_model() -> None:
    """A 400 lands in other_error_count, like connection errors do — it's
    still an HTTP answer from the model (live: batched prompts on the simulator)."""
    bad = _result(0, other_error_count=3, failure_origins={"model": 3}, failure_origin_evidence={"model": "x"})
    assert attribute(bad, bad)[:2] == ("unsupported_by_model", "model")


def test_attribution_matrix() -> None:
    gateway_404 = _result(0, failure_origins={"gateway": 3}, failure_origin_evidence={"gateway": "e"},
                          other_error_count=0)
    assert attribute(_result(3), _result(3))[0] == "supported"
    # Works through MaaS; a failing direct call is noted, not blamed.
    outcome, _, why = attribute(_result(3), _result(0, other_error_count=3))
    assert outcome == "supported" and "direct call" in why
    # Fails via MaaS, works direct → MaaS.
    assert attribute(gateway_404, _result(3))[:2] == ("blocked_by_maas", "MaaS gateway")
    # Fails both ways with a real HTTP answer → the model.
    assert attribute(gateway_404, _result(0, not_found_count=3, failure_origins={"model": 3}))[:2] == (
        "unsupported_by_model", "model",
    )
    # Direct unusable (connection errors / 401) → the MaaS responses decide, marked "likely".
    assert attribute(gateway_404, _result(0, other_error_count=3))[1] == "MaaS gateway (likely)"
    assert attribute(gateway_404, _result(0, unauthorized_count=3))[1] == "MaaS gateway (likely)"
    model_err = _result(0, failure_origins={"model": 3}, failure_origin_evidence={"model": "vLLM"})
    assert attribute(model_err, None)[1] == "model (likely)"
    assert attribute(_result(0, other_error_count=3), None)[0] == "unclear"


def _status_error(status: int, text: str = "") -> APIStatusError:
    request = httpx.Request("POST", "http://x/v1")
    response = httpx.Response(status, request=request, text=text)
    return APIStatusError(str(status), response=response, body=None)  # type: ignore[arg-type]


seen_clients: list[tuple[str, MagicMock]] = []


def _client_factory(**kwargs: object) -> MagicMock:
    """Through MaaS, /v1/completions is refused by the gateway (bare 404);
    directly, the model serves it. Embeddings fail both ways (vLLM error)."""
    direct = "svc" in str(kwargs["base_url"])
    seen_clients.append((str(kwargs["base_url"]), m := MagicMock()))
    ok = MagicMock(usage=MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    vllm_404 = _status_error(404, '{"object": "error", "message": "not an embedding model"}')
    m.chat.completions.create = AsyncMock(return_value=ok)
    m.completions.create = AsyncMock(return_value=ok) if direct else AsyncMock(side_effect=_status_error(404))
    m.embeddings.create = AsyncMock(side_effect=vllm_404)
    return m


async def test_each_type_reports_supported_blocked_and_unsupported() -> None:
    ctx = _ctx()
    task = SendRequestsEachTypeTask(
        "send_requests_each_type",
        {"model_name": "m", "model_namespace": "ns", "types": "chat,completions,embeddings",
         "requests_per_type": "2", "compare_direct": "True"},
    )
    with (
        patch("harness.tasks.inference.SendRequestsTask._discover_model",
              AsyncMock(return_value=(_MAAS, "m-id"))),
        patch("harness.tasks.request_types._check_direct",
              AsyncMock(side_effect=lambda url: (url, "", "served-name"))),
        patch("harness.tasks.inference.AsyncOpenAI", side_effect=_client_factory),
    ):
        result = await task.run(ctx)

    assert result.status == "PASS"
    counts = ctx.shared_state["request_types"]
    assert counts == {
        "selected_count": 3, "tried_count": 3, "supported_count": 1, "blocked_by_maas_count": 1,
        "unsupported_by_model_count": 1, "unclear_count": 0,
    }
    rows = {r[0]: r for r in ctx.shared_state["_tables"]["Request types"]["rows"]}
    assert rows["Completions"][5] == "MaaS gateway"
    assert rows["Embeddings"][5] == "model"
    assert "Blocked by MaaS: Completions." in ctx.shared_state["_verdict_text"]
    # One traffic card per type (the MaaS leg), not two.
    assert set(ctx.shared_state["_traffic"]) == {
        "request_type_chat", "request_type_completions", "request_type_embeddings",
    }
    # Direct calls name the model the way the model server knows it, not MaaS's id.
    direct_models = {
        c.chat.completions.create.await_args.kwargs["model"]
        for url, c in seen_clients if "svc" in url and c.chat.completions.create.await_args
    }
    assert direct_models == {"served-name"}


async def test_unreachable_model_falls_back_to_the_responses() -> None:
    ctx = _ctx()
    task = SendRequestsEachTypeTask(
        "send_requests_each_type",
        {"model_name": "m", "model_namespace": "ns", "types": "completions", "requests_per_type": "1"},
    )
    with (
        patch("harness.tasks.inference.SendRequestsTask._discover_model",
              AsyncMock(return_value=(_MAAS, "m-id"))),
        patch("harness.tasks.request_types._check_direct",
              AsyncMock(return_value=(None, "The model's service wasn't reachable directly (ConnectTimeout)", None))),
        patch("harness.tasks.inference.AsyncOpenAI", side_effect=_client_factory),
    ):
        await task.run(ctx)

    row = ctx.shared_state["_tables"]["Request types"]["rows"][0]
    assert row[5] == "MaaS gateway (likely)"
    assert "wasn't reachable" in row[3]
    assert "attributed from the responses alone" in ctx.shared_state["_verdict_text"]


async def test_probe_types_use_plain_http() -> None:
    """messages/tokenize/rerank go out as plain POSTs on the model's root."""
    from harness.tasks import request_types

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/tokenize"):
            return httpx.Response(200, json={"count": 3, "tokens": [1, 2, 3]})
        return httpx.Response(404, text="")

    real_client = httpx.AsyncClient
    with patch.object(request_types.httpx, "AsyncClient",
                      lambda **kw: real_client(transport=httpx.MockTransport(handler))):
        tok = await request_types._probe(request_types._BY_ID["tokenize"], _MAAS, "m", "sk", 2, "hi", False)
        rer = await request_types._probe(request_types._BY_ID["rerank"], _MAAS, "m", "sk", 1, "hi", False)
    assert tok["success_count"] == 2 and len(tok["timeline"]) == 2
    assert rer["not_found_count"] == 1 and rer["failure_origins"] == {"gateway": 1}
    assert seen[0] == "http://maas.test/ns/m/tokenize"
    assert seen[-1] == "http://maas.test/ns/m/v1/rerank"


async def test_probe_types_get_a_traffic_card_too() -> None:
    """The run page lists one traffic card per type tried — plain-HTTP probes
    included, not only the SDK-sent types."""
    ctx = _ctx()
    probe_result = {"success_count": 1, "total_requests": 1, "fail_count": 0, "timeline": [[0.1, 3, "ok", 12.0]]}
    with (
        patch("harness.tasks.inference.SendRequestsTask._discover_model", AsyncMock(return_value=(_MAAS, "m-id"))),
        patch("harness.tasks.request_types._probe", AsyncMock(side_effect=lambda *a, **k: dict(probe_result))),
    ):
        await SendRequestsEachTypeTask(
            "send_requests_each_type", {"types": "tokenize", "requests_per_type": "1", "compare_direct": "false"}
        ).run(ctx)
    card = ctx.shared_state["_traffic"]["request_type_tokenize"]
    assert card["label"] == "Tokenize" and card["timeline"] == [[0.1, 3, "ok", 12.0]]
    assert "timeline" not in ctx.shared_state["request_type_tokenize"]
