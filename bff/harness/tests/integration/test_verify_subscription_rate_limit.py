"""Integration test for the verify_subscription_rate_limit scenario."""
import os
import uuid

import pytest


@pytest.fixture(autouse=True)
def _temporary_subscription_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    # The CR-restoration check below only means something when the run
    # creates its own temporary subscription (mode: temporary).
    monkeypatch.setenv("MAASPAL_CONFIG_OVERRIDES", '{"mode": "temporary"}')


pytestmark = pytest.mark.skipif(
    not os.environ.get("MAAS_API_URL"),
    reason="MAAS_API_URL not set — requires a live RHOAI cluster",
)


async def test_rate_limit_validation_passes() -> None:
    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/verify_subscription_rate_limit.yaml", run_id)
    result = await runner.run()

    assert result.status == "PASS", (
        f"verify_subscription_rate_limit returned {result.status}. "
        f"Task results: {result.tasks}. "
        f"Assertions: {result.assertions}"
    )


async def test_rate_limit_subscription_restored() -> None:
    """MaaSSubscription CR must be restored or deleted after the run."""
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    from harness.runner import ScenarioRunner

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()

    api = k8s_client.CustomObjectsApi()
    # Must match scenarios/verify_subscription_rate_limit.yaml's config defaults
    # (subscription_namespace/subscription_name), not the harness's own
    # NAMESPACE — a MaaSSubscription only gets reconciled when it lives in
    # the MaaS tenant namespace, never the maaspal namespace. See ADR-009's
    # Update section.
    namespace = os.environ.get("MAAS_SUBSCRIPTION_NAMESPACE", "models-as-a-service")
    sub_name = "maaspal-rate-limit-test"

    # Capture state before
    try:
        before = api.get_namespaced_custom_object(
            group="maas.opendatahub.io",
            version="v1alpha1",
            namespace=namespace,
            plural="maassubscriptions",
            name=sub_name,
        )
    except k8s_client.ApiException as exc:
        before = None if exc.status == 404 else (_ for _ in ()).throw(exc)  # type: ignore[assignment]

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/verify_subscription_rate_limit.yaml", run_id)
    await runner.run()

    # Capture state after
    try:
        after = api.get_namespaced_custom_object(
            group="maas.opendatahub.io",
            version="v1alpha1",
            namespace=namespace,
            plural="maassubscriptions",
            name=sub_name,
        )
    except k8s_client.ApiException as exc:
        after = None if exc.status == 404 else (_ for _ in ()).throw(exc)  # type: ignore[assignment]

    assert before == after or (before is None and after is None), (
        f"MaaSSubscription state changed after run. Before: {before}, After: {after}"
    )
