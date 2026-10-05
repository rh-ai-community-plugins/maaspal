from unittest.mock import MagicMock, patch

import pytest

from harness.tasks.base import TaskContext
from harness.tasks.model import ExposeModelRouteTask, ProbeDirectEndpointTask

_MODEL = {"name": "maaspal-overhead-model-abc-1", "namespace": "llm"}


def _ctx(state: dict) -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="r", scenario_name="s", maas_api_url="", sa_token="", shared_state=state,
        config={}, assertions={}, emit_assertion_state=_emit,
    )


async def test_route_points_at_the_workload_service_with_passthrough_tls() -> None:
    ctx = _ctx({"deployed_models": [dict(_MODEL)]})
    with patch("harness.tasks.model.k8s_client.CustomObjectsApi") as api_cls:
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = {
            "status": {"ingress": [{"host": "x-direct-llm.apps.example.test",
                                    "conditions": [{"type": "Admitted", "status": "True"}]}]}
        }
        api_cls.return_value = api
        await ExposeModelRouteTask("expose_model_route", {}).run(ctx)

    body = api.create_namespaced_custom_object.call_args.kwargs["body"]
    assert body["spec"]["to"] == {"kind": "Service", "name": "maaspal-overhead-model-abc-1-kserve-workload-svc"}
    assert body["spec"]["tls"]["termination"] == "passthrough"
    assert ctx.shared_state["deployed_models"][0]["direct_url"] == "https://x-direct-llm.apps.example.test/v1"
    assert ctx.shared_state["_created"][0]["kind"] == "Route"


async def test_route_cleanup_deletes_it_and_tolerates_already_gone() -> None:
    from kubernetes.client import ApiException

    ctx = _ctx({"_exposed_routes": [{"name": "a", "namespace": "llm"}, {"name": "b", "namespace": "llm"}]})
    with patch("harness.tasks.model.k8s_client.CustomObjectsApi") as api_cls:
        api = MagicMock()
        api.delete_namespaced_custom_object.side_effect = [None, ApiException(status=404)]
        api_cls.return_value = api
        await ExposeModelRouteTask("expose_model_route", {}).cleanup(ctx)
    assert api.delete_namespaced_custom_object.call_count == 2


async def test_probe_marks_reachable_when_the_model_answers(httpx_mock) -> None:
    httpx_mock.add_response(url="https://x/v1/chat/completions", json={"choices": []})
    ctx = _ctx({"deployed_models": [{**_MODEL, "direct_url": "https://x/v1"}]})
    await ProbeDirectEndpointTask("probe_direct_endpoint", {}).run(ctx)
    assert ctx.shared_state["direct_probe"] == {"reachable": 1}
    assert "_findings" not in ctx.shared_state


async def test_probe_explains_an_unreachable_model_without_failing_the_run(
    httpx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no_sleep(_s: float) -> None:
        pass

    monkeypatch.setattr("harness.tasks.model.asyncio.sleep", _no_sleep)
    httpx_mock.add_response(url="https://x/v1/chat/completions", status_code=503, text="no healthy upstream", is_reusable=True)
    ctx = _ctx({"deployed_models": [{**_MODEL, "direct_url": "https://x/v1"}]})
    result = await ProbeDirectEndpointTask("probe_direct_endpoint", {"max_wait_s": 6}).run(ctx)
    assert result.status == "PASS"
    assert ctx.shared_state["direct_probe"] == {"reachable": 0}
    assert "HTTP 503: no healthy upstream" in ctx.shared_state["_findings"][0]["text"]
    assert ctx.shared_state["_verdict_text"].startswith("Couldn't compare")


async def test_stop_during_ready_wait_still_cleans_up_the_model() -> None:
    """A Stop cancels deploy_simulated_model while it waits for the model to
    become ready. The LLMInferenceService already exists by then, so it must
    already be in deployed_models (what cleanup reads) and on the run page's
    resource list — confirmed live: recording only at the end left it behind."""
    import asyncio

    from harness.tasks.model import DeploySimulatedModelTask

    async def _never_ready(*_args: object, **_kwargs: object) -> bool:
        await asyncio.sleep(3600)
        return True

    ctx = _ctx({})
    with patch("harness.tasks.model.k8s_client.CustomObjectsApi") as api_cls, patch(
        "harness.tasks.model._wait_for_isvc_ready", new=_never_ready
    ):
        api = MagicMock()
        api.get_namespaced_custom_object.side_effect = _not_found()
        api_cls.return_value = api
        task = DeploySimulatedModelTask(
            "deploy_simulated_model", {"count": "1", "name_prefix": "sim", "namespace": "llm"}
        )
        running = asyncio.create_task(task.run(ctx))
        await asyncio.sleep(0.05)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        [model] = ctx.shared_state["deployed_models"]
        assert model["isvc_created"] and not model["ready"]
        assert [c["name"] for c in ctx.shared_state["_created"]] == ["llm/sim-r-1"]

        await task.cleanup(ctx)
        deleted = api.delete_namespaced_custom_object.call_args.kwargs
        assert (deleted["plural"], deleted["name"]) == ("llminferenceservices", "sim-r-1")


def _not_found() -> Exception:
    from kubernetes import client as k8s_client

    return k8s_client.ApiException(status=404)
