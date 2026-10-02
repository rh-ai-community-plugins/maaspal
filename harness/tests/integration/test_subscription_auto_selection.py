"""Integration test for the subscription_auto_selection scenario."""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MAAS_API_URL"),
    reason="MAAS_API_URL not set — requires a live RHOAI cluster",
)


async def test_rate_limit_priority_precedence_passes() -> None:
    from harness.runner import ScenarioRunner

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/subscription_auto_selection.yaml", run_id)
    result = await runner.run()

    assert result.status == "PASS", (
        f"subscription_auto_selection returned {result.status}. "
        f"Task results: {result.tasks}. "
        f"Assertions: {result.assertions}"
    )


async def test_rate_limit_priority_precedence_no_leftover_subscriptions() -> None:
    """Both temp MaaSSubscriptions must be gone after the run."""
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    from harness.runner import ScenarioRunner

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()

    api = k8s_client.CustomObjectsApi()
    namespace = os.environ.get("MAAS_SUBSCRIPTION_NAMESPACE", "models-as-a-service")

    run_id = str(uuid.uuid4())
    runner = ScenarioRunner("scenarios/subscription_auto_selection.yaml", run_id)
    await runner.run()

    for sub_name in ("maaspal-priority-low", "maaspal-priority-high"):
        try:
            leftover = api.get_namespaced_custom_object(
                group="maas.opendatahub.io",
                version="v1alpha1",
                namespace=namespace,
                plural="maassubscriptions",
                name=sub_name,
            )
        except k8s_client.ApiException as exc:
            leftover = None if exc.status == 404 else (_ for _ in ()).throw(exc)  # type: ignore[assignment]

        assert leftover is None, f"{sub_name} still exists after cleanup: {leftover}"
