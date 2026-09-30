from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kubernetes.client.exceptions import ApiException

from harness.tasks.base import TaskContext
from harness.tasks.subscription import (
    ApplyPriorityTestSubscriptionsTask,
    ApplyRateLimitSubscriptionTask,
    _wait_for_subscription_ready,
)

_PARAMS = {
    "new_subscription_name": "test-sub",
    "namespace": "models-as-a-service",
    "model_name": "facebook-opt-125m-simulated",
    "model_namespace": "llm",
    "token_limit": "10",
    # 0 makes _wait_for_subscription_ready give up after exactly one check
    # instead of actually polling for up to the real 30s default — these
    # tests aren't exercising that wait loop itself (see the dedicated
    # test_wait_for_subscription_ready_* tests below for that).
    "ready_max_wait_s": "0",
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
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", _PARAMS)
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["original_subscription"] is None
    assert ctx.shared_state["_sub_created"] is True
    api.create_namespaced_custom_object.assert_called_once()
    api.patch_namespaced_custom_object.assert_not_called()

    body = _body_from(api.create_namespaced_custom_object)
    assert body["metadata"]["namespace"] == "models-as-a-service"
    assert "rpsLimit" not in body["spec"]
    assert body["spec"]["priority"] == 100
    assert body["spec"]["owner"]["groups"] == [{"name": "system:authenticated"}]
    model_ref = body["spec"]["modelRefs"][0]
    assert model_ref["name"] == "facebook-opt-125m-simulated"
    assert model_ref["namespace"] == "llm"
    assert model_ref["tokenRateLimits"] == [{"limit": 10, "window": "1s"}]


async def test_custom_priority_owner_and_window() -> None:
    params = {
        **_PARAMS,
        "priority": "5",
        "owner_groups": ["team-a"],
        "owner_users": ["alice"],
        "token_window": "1m",
    }
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", params)
        await task.run(_make_ctx())

    body = _body_from(api.create_namespaced_custom_object)
    assert body["spec"]["priority"] == 5
    assert body["spec"]["owner"]["groups"] == [{"name": "team-a"}]
    assert body["spec"]["owner"]["users"] == ["alice"]
    assert body["spec"]["modelRefs"][0]["tokenRateLimits"] == [{"limit": 10, "window": "1m"}]


async def test_patches_cr_when_existing() -> None:
    existing = {
        "apiVersion": "maas.opendatahub.io/v1alpha1",
        "spec": {"priority": 100, "owner": {"groups": [], "users": []}, "modelRefs": []},
    }

    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.return_value = existing

        task = ApplyRateLimitSubscriptionTask(
            "apply_rate_limit_subscription", {**_PARAMS, "token_limit": "20"}
        )
        result = await task.run(_make_ctx())

    assert result.status == "PASS"
    assert api.patch_namespaced_custom_object.call_count == 1
    api.create_namespaced_custom_object.assert_not_called()


async def test_cleanup_deletes_when_created() -> None:
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api

        ctx = _make_ctx(
            {
                "original_subscription": None,
                "_sub_created": True,
                "new_subscription_name": "test-sub",
                "subscription_namespace": "models-as-a-service",
            }
        )
        task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", {})
        await task.cleanup(ctx)

    api.delete_namespaced_custom_object.assert_called_once()
    api.replace_namespaced_custom_object.assert_not_called()


async def test_cleanup_restores_when_preexisting() -> None:
    original = {"apiVersion": "maas.opendatahub.io/v1alpha1", "spec": {"priority": 100}}

    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api

        ctx = _make_ctx(
            {
                "original_subscription": original,
                "_sub_created": False,
                "new_subscription_name": "test-sub",
                "subscription_namespace": "models-as-a-service",
            }
        )
        task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", {})
        await task.cleanup(ctx)

    api.replace_namespaced_custom_object.assert_called_once()
    api.delete_namespaced_custom_object.assert_not_called()


async def test_cleanup_noop_when_no_state() -> None:
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api

        ctx = _make_ctx()
        task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", {})
        await task.cleanup(ctx)

    api.delete_namespaced_custom_object.assert_not_called()
    api.replace_namespaced_custom_object.assert_not_called()


async def test_non_404_api_exception_propagates() -> None:
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(403)

        task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", _PARAMS)
        with pytest.raises(ApiException):
            await task.run(_make_ctx())


async def test_owner_groups_explicit_empty_list_sticks() -> None:
    """Regression test (ADR-023): `owner_groups: []` must not silently fall
    back to _DEFAULT_OWNER_GROUPS — `self.params.get("owner_groups") or
    _DEFAULT_OWNER_GROUPS` used to discard an explicit empty list because
    `[] or X` evaluates to X."""
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyRateLimitSubscriptionTask(
            "apply_rate_limit_subscription", {**_PARAMS, "owner_groups": []}
        )
        await task.run(_make_ctx())

    body = _body_from(api.create_namespaced_custom_object)
    assert body["spec"]["owner"]["groups"] == []


async def test_owner_groups_absent_still_defaults() -> None:
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", _PARAMS)
        await task.run(_make_ctx())

    body = _body_from(api.create_namespaced_custom_object)
    assert body["spec"]["owner"]["groups"] == [{"name": "system:authenticated"}]


async def test_owner_users_from_shared_state_appends_minted_identities() -> None:
    """ADR-023: lets a subscription bind owner.users to identities minted at
    runtime by create_user — task params can't reference shared_state via
    YAML templating, so this reads it directly."""
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyRateLimitSubscriptionTask(
            "apply_rate_limit_subscription",
            {**_PARAMS, "owner_groups": [], "owner_users_from_shared_state": "users"},
        )
        ctx = _make_ctx(
            {
                "users": [
                    {"name": "u1", "namespace": "maaspal", "username": "system:serviceaccount:maaspal:u1", "token": "t1"},
                    {"name": "u2", "namespace": "maaspal", "username": "system:serviceaccount:maaspal:u2", "token": "t2"},
                ]
            }
        )
        await task.run(ctx)

    body = _body_from(api.create_namespaced_custom_object)
    assert body["spec"]["owner"]["users"] == [
        "system:serviceaccount:maaspal:u1",
        "system:serviceaccount:maaspal:u2",
    ]


async def test_owner_users_from_shared_state_combines_with_explicit_owner_users() -> None:
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyRateLimitSubscriptionTask(
            "apply_rate_limit_subscription",
            {
                **_PARAMS,
                "owner_users": ["static-user"],
                "owner_users_from_shared_state": "users",
            },
        )
        ctx = _make_ctx(
            {"users": [{"name": "u1", "namespace": "maaspal", "username": "minted-user", "token": "t1"}]}
        )
        await task.run(ctx)

    body = _body_from(api.create_namespaced_custom_object)
    assert body["spec"]["owner"]["users"] == ["static-user", "minted-user"]


async def test_owner_users_from_shared_state_absent_is_noop() -> None:
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", _PARAMS)
        await task.run(_make_ctx())

    body = _body_from(api.create_namespaced_custom_object)
    assert body["spec"]["owner"]["users"] == []


async def test_missing_required_param_raises() -> None:
    task = ApplyRateLimitSubscriptionTask(
        "apply_rate_limit_subscription", {"new_subscription_name": "test-sub"}
    )
    with pytest.raises(KeyError):
        await task.run(_make_ctx())


async def test_model_from_shared_state_used_when_provided() -> None:
    """ADR-018's third Update: targets a model deploy_simulated_model just
    created at runtime — task params can't reference shared_state via YAML
    templating, so this reads it directly, mirroring
    owner_users_from_shared_state above."""
    params = {k: v for k, v in _PARAMS.items() if k not in ("model_name", "model_namespace")}
    params["model_from_shared_state"] = "deployed_models"

    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", params)
        ctx = _make_ctx(
            {"deployed_models": [{"name": "maaspal-fail-closed-model-abcd1234-1", "namespace": "llm"}]}
        )
        await task.run(ctx)

    body = _body_from(api.create_namespaced_custom_object)
    model_ref = body["spec"]["modelRefs"][0]
    assert model_ref["name"] == "maaspal-fail-closed-model-abcd1234-1"
    assert model_ref["namespace"] == "llm"


async def test_model_from_shared_state_missing_raises() -> None:
    params = {k: v for k, v in _PARAMS.items() if k not in ("model_name", "model_namespace")}
    params["model_from_shared_state"] = "deployed_models"

    task = ApplyRateLimitSubscriptionTask("apply_rate_limit_subscription", params)
    with pytest.raises(RuntimeError, match="deploy_simulated_model"):
        await task.run(_make_ctx())


_PRIORITY_PARAMS = {
    "namespace": "models-as-a-service",
    "model_name": "facebook-opt-125m-simulated",
    "model_namespace": "llm",
    "ready_max_wait_s": "0",
    "subscriptions": [
        {"name": "maaspal-priority-low", "priority": 50, "token_limit": 10},
        {"name": "maaspal-priority-high", "priority": 200, "token_limit": 1000000},
    ],
}


async def test_priority_subscriptions_creates_both_when_absent() -> None:
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = _api_exc(404)

        task = ApplyPriorityTestSubscriptionsTask(
            "apply_priority_test_subscriptions", _PRIORITY_PARAMS
        )
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    assert api.create_namespaced_custom_object.call_count == 2
    records = ctx.shared_state["priority_test_subscriptions"]
    assert [r["name"] for r in records] == ["maaspal-priority-low", "maaspal-priority-high"]
    assert all(r["created"] for r in records)

    bodies = [c.kwargs["body"] for c in api.create_namespaced_custom_object.call_args_list]
    assert bodies[0]["spec"]["priority"] == 50
    assert bodies[1]["spec"]["priority"] == 200


async def test_priority_subscriptions_independent_per_record_state() -> None:
    """One pre-existing, one new — each record must carry its own
    original/created state so cleanup treats them correctly, unlike the
    fixed shared_state keys ApplyRateLimitSubscriptionTask uses (see the
    class docstring for why a second instance of that task can't be reused
    here)."""
    existing_low = {"spec": {"priority": 50}}

    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        # Each subscription now does an initial existence check *and* one
        # ready-poll check (ready_max_wait_s=0 caps it at exactly one) —
        # call order is per-subscription (initial, ready) not batched.
        api.get_namespaced_custom_object.side_effect = [
            existing_low, existing_low, _api_exc(404), _api_exc(404),
        ]

        task = ApplyPriorityTestSubscriptionsTask(
            "apply_priority_test_subscriptions", _PRIORITY_PARAMS
        )
        ctx = _make_ctx()
        await task.run(ctx)

        records = ctx.shared_state["priority_test_subscriptions"]
        assert records[0]["created"] is False
        assert records[0]["original"] == existing_low
        assert records[1]["created"] is True
        assert records[1]["original"] is None

        api.reset_mock()
        await task.cleanup(ctx)

        api.replace_namespaced_custom_object.assert_called_once()
        assert api.replace_namespaced_custom_object.call_args.kwargs["name"] == "maaspal-priority-low"
        api.delete_namespaced_custom_object.assert_called_once()
        assert api.delete_namespaced_custom_object.call_args.kwargs["name"] == "maaspal-priority-high"


async def test_priority_subscriptions_cleanup_noop_when_no_state() -> None:
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api

        task = ApplyPriorityTestSubscriptionsTask("apply_priority_test_subscriptions", {})
        await task.cleanup(_make_ctx())

    api.delete_namespaced_custom_object.assert_not_called()
    api.replace_namespaced_custom_object.assert_not_called()


async def test_wait_for_subscription_ready_returns_immediately_when_phase_set() -> None:
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.return_value = {"status": {"phase": "Active"}}

        await _wait_for_subscription_ready(api, "test-sub", "ns", max_wait_s=30.0, log_prefix="test")

    api.get_namespaced_custom_object.assert_called_once()


async def test_wait_for_subscription_ready_polls_until_phase_appears() -> None:
    """The real bug this closes: MaaS accepts a just-created MaaSSubscription
    immediately, but the controller reconciles it asynchronously — a
    provision_api_key call right after creation can lose that race and get
    back 400 subscription_not_ready ("no status.phase set"). Confirmed live."""
    with (
        patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls,
        patch("harness.tasks.subscription.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.side_effect = [
            {"status": {}},
            {"status": {}},
            {"status": {"phase": "Active"}},
        ]

        await _wait_for_subscription_ready(api, "test-sub", "ns", max_wait_s=30.0, log_prefix="test")

    assert api.get_namespaced_custom_object.call_count == 3
    assert mock_sleep.call_count == 2


async def test_wait_for_subscription_ready_gives_up_honestly_after_timeout() -> None:
    """Never becoming ready must not hang forever or raise — the next task
    (e.g. provision_api_key) surfaces the real error if it's still not ready."""
    with (
        patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls,
        patch("harness.tasks.subscription.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        api = MagicMock()
        mock_cls.return_value = api
        api.get_namespaced_custom_object.return_value = {"status": {}}

        await _wait_for_subscription_ready(api, "test-sub", "ns", max_wait_s=0.0, log_prefix="test")

    api.get_namespaced_custom_object.assert_called_once()
    mock_sleep.assert_not_called()


async def test_priority_subscriptions_cleanup_continues_after_one_failure() -> None:
    """One subscription's cleanup failing must not prevent the other's."""
    with patch("harness.tasks.subscription.k8s_client.CustomObjectsApi") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.delete_namespaced_custom_object.side_effect = [Exception("boom"), None]

        ctx = _make_ctx(
            {
                "priority_test_subscriptions": [
                    {"name": "a", "namespace": "ns", "original": None, "created": True},
                    {"name": "b", "namespace": "ns", "original": None, "created": True},
                ]
            }
        )
        task = ApplyPriorityTestSubscriptionsTask("apply_priority_test_subscriptions", {})
        await task.cleanup(ctx)

    assert api.delete_namespaced_custom_object.call_count == 2
