from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException

from harness.tasks.access_policy import ApplyAuthPolicyTask
from harness.tasks.base import TaskContext

_PARAMS = {
    "policy_name": "test-policy",
    "namespace": "models-as-a-service",
    "model_name": "facebook-opt-125m-simulated",
    "model_namespace": "llm",
    "subject_groups": ["maaspal-fail-closed-test"],
}


def _make_ctx(shared_state: dict | None = None) -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="test-001",
        scenario_name="test",
        maas_api_url="http://maas.test",
        sa_token="test-token",
        shared_state=shared_state or {},
        config={"NAMESPACE": "test-ns"},
        assertions={},
        emit_assertion_state=_emit,
    )


def _api_exc(status: int) -> ApiException:
    return ApiException(status=status)


def _body_from(mock_call) -> dict:
    return mock_call.call_args.kwargs["body"]


async def test_creates_cr_when_not_existing() -> None:
    with patch("harness.tasks.access_policy.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyAuthPolicyTask("apply_auth_policy", _PARAMS)
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["original_auth_policy"] is None
    assert ctx.shared_state["_policy_created"] is True
    api.create_namespaced_custom_object.assert_called_once()
    api.patch_namespaced_custom_object.assert_not_called()

    body = _body_from(api.create_namespaced_custom_object)
    assert body["metadata"]["namespace"] == "models-as-a-service"
    assert body["spec"]["subjects"]["groups"] == [{"name": "maaspal-fail-closed-test"}]
    assert body["spec"]["subjects"]["users"] == []
    model_ref = body["spec"]["modelRefs"][0]
    assert model_ref["name"] == "facebook-opt-125m-simulated"
    assert model_ref["namespace"] == "llm"
    assert "tokenRateLimits" not in body["spec"]


async def test_custom_subject_users() -> None:
    params = {**_PARAMS, "subject_groups": [], "subject_users": ["alice"]}
    with patch("harness.tasks.access_policy.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyAuthPolicyTask("apply_auth_policy", params)
        await task.run(_make_ctx())

    body = _body_from(api.create_namespaced_custom_object)
    assert body["spec"]["subjects"]["groups"] == []
    assert body["spec"]["subjects"]["users"] == ["alice"]


async def test_patches_cr_when_existing() -> None:
    existing = {
        "apiVersion": "maas.opendatahub.io/v1alpha1",
        "spec": {"subjects": {"groups": [], "users": []}, "modelRefs": []},
    }

    with patch("harness.tasks.access_policy.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.return_value = existing

        task = ApplyAuthPolicyTask("apply_auth_policy", _PARAMS)
        result = await task.run(_make_ctx())

    assert result.status == "PASS"
    assert api.patch_namespaced_custom_object.call_count == 1
    api.create_namespaced_custom_object.assert_not_called()


async def test_cleanup_deletes_when_created() -> None:
    with patch("harness.tasks.access_policy.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api

        ctx = _make_ctx(
            {
                "original_auth_policy": None,
                "_policy_created": True,
                "auth_policy_name": "test-policy",
                "auth_policy_namespace": "models-as-a-service",
            }
        )
        task = ApplyAuthPolicyTask("apply_auth_policy", {})
        await task.cleanup(ctx)

    api.delete_namespaced_custom_object.assert_called_once()
    api.replace_namespaced_custom_object.assert_not_called()


async def test_cleanup_restores_when_preexisting() -> None:
    original = {"apiVersion": "maas.opendatahub.io/v1alpha1", "spec": {"subjects": {}}}

    with patch("harness.tasks.access_policy.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api

        ctx = _make_ctx(
            {
                "original_auth_policy": original,
                "_policy_created": False,
                "auth_policy_name": "test-policy",
                "auth_policy_namespace": "models-as-a-service",
            }
        )
        task = ApplyAuthPolicyTask("apply_auth_policy", {})
        await task.cleanup(ctx)

    api.replace_namespaced_custom_object.assert_called_once()
    api.delete_namespaced_custom_object.assert_not_called()


async def test_cleanup_noop_when_no_state() -> None:
    with patch("harness.tasks.access_policy.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api

        ctx = _make_ctx()
        task = ApplyAuthPolicyTask("apply_auth_policy", {})
        await task.cleanup(ctx)

    api.delete_namespaced_custom_object.assert_not_called()
    api.replace_namespaced_custom_object.assert_not_called()


async def test_non_404_api_exception_propagates() -> None:
    with patch("harness.tasks.access_policy.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(403)

        task = ApplyAuthPolicyTask("apply_auth_policy", _PARAMS)
        with pytest.raises(ApiException):
            await task.run(_make_ctx())


async def test_missing_required_param_raises() -> None:
    task = ApplyAuthPolicyTask("apply_auth_policy", {"policy_name": "test-policy"})
    with pytest.raises(KeyError):
        await task.run(_make_ctx())


async def test_model_refs_from_shared_state_covers_all_deployed_models() -> None:
    """A scenario deploying N models (e.g. multi_model_full_load) needs
    gateway access granted for all of them in one policy, not just one."""
    params = {k: v for k, v in _PARAMS.items() if k not in ("model_name", "model_namespace")}
    params["model_refs_from_shared_state"] = "deployed_models"

    with patch("harness.tasks.access_policy.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyAuthPolicyTask("apply_auth_policy", params)
        ctx = _make_ctx(
            {
                "deployed_models": [
                    {"name": "maaspal-sim-abcd-1", "namespace": "maaspal"},
                    {"name": "maaspal-sim-abcd-2", "namespace": "maaspal"},
                ]
            }
        )
        await task.run(ctx)

    body = _body_from(api.create_namespaced_custom_object)
    assert body["spec"]["modelRefs"] == [
        {"name": "maaspal-sim-abcd-1", "namespace": "maaspal"},
        {"name": "maaspal-sim-abcd-2", "namespace": "maaspal"},
    ]
