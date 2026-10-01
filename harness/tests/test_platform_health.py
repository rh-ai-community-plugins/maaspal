from unittest.mock import MagicMock, patch

import pytest

from harness.tasks.base import TaskContext
from harness.tasks.platform_health import CheckModelHealthTask

_PARAMS = {
    "model_name": "facebook-opt-125m-simulated",
    "model_namespace": "llm",
}

_HEALTHY_TRLP = {
    "status": {
        "conditions": [
            {"type": "Accepted", "status": "True"},
            {"type": "Enforced", "status": "True"},
        ]
    }
}

_PROGRAMMED_GATEWAY = {
    "status": {
        "conditions": [
            {"type": "Accepted", "status": "True"},
            {"type": "Programmed", "status": "True"},
        ]
    }
}

_MATCHING_ROUTE = {
    "metadata": {
        "ownerReferences": [
            {"kind": "LLMInferenceService", "name": "facebook-opt-125m-simulated"}
        ]
    }
}


@pytest.fixture(autouse=True)
def _no_model_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: the MaaS Setup catalog is unreadable — tests that need it
    patch _list_maas_models themselves."""
    monkeypatch.setattr("harness.tasks.platform_health._list_maas_models", lambda: None)


def _make_ctx() -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="test-001",
        scenario_name="test",
        maas_api_url="http://maas.test",
        sa_token="test-token",
        shared_state={},
        config={},
        assertions={},
        emit_assertion_state=_emit,
    )


def _mock_api(*, trlp_items=None, gateway=None, route_items=None) -> MagicMock:
    api = MagicMock()
    api.list_namespaced_custom_object.side_effect = [
        {"items": trlp_items if trlp_items is not None else [_HEALTHY_TRLP]},
        {"items": route_items if route_items is not None else [_MATCHING_ROUTE]},
    ]
    api.get_namespaced_custom_object.return_value = (
        gateway if gateway is not None else _PROGRAMMED_GATEWAY
    )
    return api


async def test_all_healthy() -> None:
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = _mock_api()
        mock_cls.return_value = api

        task = CheckModelHealthTask("check_model_health",_PARAMS)
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["rate_limit_policy_status"] == {
        "found": 1, "accepted": 1, "enforced": 1,
    }
    assert ctx.shared_state["gateway_status"] == {"programmed": 1}
    assert ctx.shared_state["http_route_status"] == {"found": 1, "owner_ref_matches": 1}


async def test_token_rate_limit_policy_not_found() -> None:
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = _mock_api(trlp_items=[])
        mock_cls.return_value = api

        task = CheckModelHealthTask("check_model_health",_PARAMS)
        ctx = _make_ctx()
        await task.run(ctx)

    assert ctx.shared_state["rate_limit_policy_status"] == {
        "found": 0, "accepted": 0, "enforced": 0,
    }


async def test_token_rate_limit_policy_not_enforced() -> None:
    unenforced = {
        "status": {
            "conditions": [
                {"type": "Accepted", "status": "True"},
                {"type": "Enforced", "status": "False"},
            ]
        }
    }
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = _mock_api(trlp_items=[unenforced])
        mock_cls.return_value = api

        task = CheckModelHealthTask("check_model_health",_PARAMS)
        ctx = _make_ctx()
        await task.run(ctx)

    status = ctx.shared_state["rate_limit_policy_status"]
    assert status["accepted"] == 1
    assert status["enforced"] == 0


async def test_gateway_not_programmed() -> None:
    unprogrammed = {"status": {"conditions": [{"type": "Accepted", "status": "True"}]}}
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = _mock_api(gateway=unprogrammed)
        mock_cls.return_value = api

        task = CheckModelHealthTask("check_model_health",_PARAMS)
        ctx = _make_ctx()
        await task.run(ctx)

    assert ctx.shared_state["gateway_status"] == {"programmed": 0}


async def test_http_route_not_found() -> None:
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = _mock_api(route_items=[])
        mock_cls.return_value = api

        task = CheckModelHealthTask("check_model_health",_PARAMS)
        ctx = _make_ctx()
        await task.run(ctx)

    assert ctx.shared_state["http_route_status"] == {"found": 0, "owner_ref_matches": 0}


async def test_http_route_owner_mismatch() -> None:
    wrong_owner = {
        "metadata": {"ownerReferences": [{"kind": "LLMInferenceService", "name": "some-other-model"}]}
    }
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = _mock_api(route_items=[wrong_owner])
        mock_cls.return_value = api

        task = CheckModelHealthTask("check_model_health",_PARAMS)
        ctx = _make_ctx()
        await task.run(ctx)

    status = ctx.shared_state["http_route_status"]
    assert status["found"] == 1
    assert status["owner_ref_matches"] == 0


async def test_uses_label_selectors_scoped_to_model_namespace() -> None:
    """Resources are found by label, in the model's own namespace — not an
    assumed name pattern (ADR-009's lesson applies to names too)."""
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = _mock_api()
        mock_cls.return_value = api

        task = CheckModelHealthTask("check_model_health",_PARAMS)
        await task.run(_make_ctx())

    trlp_call, route_call = api.list_namespaced_custom_object.call_args_list
    assert trlp_call.kwargs["namespace"] == "llm"
    assert trlp_call.kwargs["label_selector"] == "maas.opendatahub.io/model=facebook-opt-125m-simulated"
    assert route_call.kwargs["namespace"] == "llm"
    assert route_call.kwargs["label_selector"] == "app.kubernetes.io/name=facebook-opt-125m-simulated"


async def test_gateway_defaults_to_confirmed_live_name() -> None:
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = _mock_api()
        mock_cls.return_value = api

        task = CheckModelHealthTask("check_model_health",_PARAMS)
        await task.run(_make_ctx())

    gw_call = api.get_namespaced_custom_object.call_args
    assert gw_call.kwargs["name"] == "maas-default-gateway"
    assert gw_call.kwargs["namespace"] == "openshift-ingress"


async def test_gateway_name_overridable() -> None:
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = _mock_api()
        mock_cls.return_value = api

        task = CheckModelHealthTask(
            "check_model_health",
            {**_PARAMS, "gateway_name": "custom-gateway", "gateway_namespace": "custom-ns"},
        )
        await task.run(_make_ctx())

    gw_call = api.get_namespaced_custom_object.call_args
    assert gw_call.kwargs["name"] == "custom-gateway"
    assert gw_call.kwargs["namespace"] == "custom-ns"


def _catalog_model(name: str, **overrides) -> dict:
    return {
        "name": name,
        "namespace": "llm",
        "hosting": "internal",
        "ready": True,
        "subscriptions": [{"name": "simulator-free"}],
        "has_auth_policy": True,
        "gateway_access_label": True,
        **overrides,
    }


async def test_blank_model_checks_every_internal_model(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = [
        _catalog_model("model-a"),
        _catalog_model("model-b", has_auth_policy=False),
        _catalog_model("ext", hosting="external"),
    ]
    monkeypatch.setattr("harness.tasks.platform_health._list_maas_models", lambda: catalog)

    def _route_for(name: str) -> dict:
        return {"metadata": {"ownerReferences": [{"kind": "LLMInferenceService", "name": name}]}}

    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        api.list_namespaced_custom_object.side_effect = [
            {"items": [_HEALTHY_TRLP]}, {"items": [_route_for("model-a")]},
            {"items": [_HEALTHY_TRLP]}, {"items": [_route_for("model-b")]},
        ]
        api.get_namespaced_custom_object.return_value = _PROGRAMMED_GATEWAY
        mock_cls.return_value = api

        ctx = _make_ctx()
        await CheckModelHealthTask("check_model_health", {"model_name": "", "model_namespace": ""}).run(ctx)

    health = ctx.shared_state["model_health"]
    assert health["models_checked"] == 2  # external model skipped
    assert health["has_auth_policy_count"] == 1
    assert health["healthy_count"] == 1
    table = ctx.shared_state["_tables"]["Model health"]
    assert [row[0] for row in table["rows"]] == ["llm/model-a", "llm/model-b"]
    assert table["rows"][1][3] == "✗"
    assert "1 fully healthy, 1 with issues" in ctx.shared_state["task_summary"]
    # Multi-model runs don't pretend one model's flags speak for all of them.
    assert "rate_limit_policy_status" not in ctx.shared_state


async def test_blank_model_without_catalog_fails_clearly() -> None:
    with (
        patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi"),
        pytest.raises(RuntimeError, match="No model selected"),
    ):
        await CheckModelHealthTask("check_model_health", {}).run(_make_ctx())


async def test_single_model_without_catalog_omits_governance_counts() -> None:
    with patch("harness.tasks.platform_health.k8s_client.CustomObjectsApi") as mock_cls:
        mock_cls.return_value = _mock_api()
        ctx = _make_ctx()
        await CheckModelHealthTask("check_model_health", _PARAMS).run(ctx)
    health = ctx.shared_state["model_health"]
    assert "ready_count" not in health  # unknown, so assertions stay PENDING rather than guess
    assert health["healthy_count"] == 1
