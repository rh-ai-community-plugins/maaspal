import asyncio
import time
import traceback

from kubernetes import client as k8s_client

from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

_ISVC_GROUP = "serving.kserve.io"
_ISVC_VERSION = "v1alpha1"
_ISVC_PLURAL = "llminferenceservices"

_MAAS_GROUP = "maas.opendatahub.io"
_MAAS_VERSION = "v1alpha1"
_MODEL_REF_PLURAL = "maasmodelrefs"

_DEFAULT_NAMESPACE = "maaspal"
_DEFAULT_SIM_IMAGE = "ghcr.io/llm-d/llm-d-inference-sim:v0.7.1"
_DEFAULT_MODEL_URI = "hf://sshleifer/tiny-gpt2"
_DEFAULT_GATEWAY_NAME = "maas-default-gateway"
_DEFAULT_GATEWAY_NAMESPACE = "openshift-ingress"
_DEFAULT_READY_MAX_WAIT_S = 120.0
_READY_POLL_INTERVAL_S = 5.0


def _model_ref_body(name: str, namespace: str) -> dict:
    return {
        "apiVersion": f"{_MAAS_GROUP}/{_MAAS_VERSION}",
        "kind": "MaaSModelRef",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"modelRef": {"kind": "LLMInferenceService", "name": name}},
    }


def _llm_isvc_body(
    name: str,
    namespace: str,
    model_name: str,
    model_uri: str,
    image: str,
    gateway_name: str,
    gateway_namespace: str,
) -> dict:
    return {
        "apiVersion": f"{_ISVC_GROUP}/{_ISVC_VERSION}",
        "kind": "LLMInferenceService",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "model": {"name": model_name, "uri": model_uri},
            "replicas": 1,
            "router": {
                "gateway": {
                    "refs": [{"name": gateway_name, "namespace": gateway_namespace}]
                },
                "route": {},
            },
            "template": {
                "containers": [
                    {
                        "name": "main",
                        "image": image,
                        "imagePullPolicy": "Always",
                        "command": ["/app/llm-d-inference-sim"],
                        "args": [
                            "--port", "8000",
                            "--model", model_name,
                            "--mode", "random",
                            "--ssl-certfile", "/var/run/kserve/tls/tls.crt",
                            "--ssl-keyfile", "/var/run/kserve/tls/tls.key",
                        ],
                        "env": [
                            {"name": "POD_NAME", "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": "metadata.name"}}},
                            {"name": "POD_NAMESPACE", "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": "metadata.namespace"}}},
                        ],
                        "ports": [{"containerPort": 8000, "name": "https", "protocol": "TCP"}],
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "256Mi"},
                            "limits": {"cpu": "500m", "memory": "512Mi"},
                        },
                        "livenessProbe": {"httpGet": {"path": "/health", "port": "https", "scheme": "HTTPS"}},
                        "readinessProbe": {"httpGet": {"path": "/ready", "port": "https", "scheme": "HTTPS"}},
                    }
                ]
            },
        },
    }


async def _wait_for_isvc_ready(
    api: k8s_client.CustomObjectsApi,
    name: str,
    namespace: str,
    max_wait_s: float,
    log_prefix: str,
) -> bool:
    """Returns True if Ready, False if timed out."""
    start = time.monotonic()
    while True:
        try:
            obj = api.get_namespaced_custom_object(
                group=_ISVC_GROUP, version=_ISVC_VERSION,
                namespace=namespace, plural=_ISVC_PLURAL, name=name,
            )
            conditions = (obj or {}).get("status", {}).get("conditions", [])
            ready = next((c for c in conditions if c.get("type") == "Ready"), None)
            if ready and ready.get("status") == "True":
                print(f"[{log_prefix}] {name} LLMInferenceService Ready", flush=True)
                return True
        except k8s_client.ApiException as exc:
            if exc.status != 404:
                raise
        elapsed = time.monotonic() - start
        if elapsed >= max_wait_s:
            print(
                f"[{log_prefix}] {name} LLMInferenceService not Ready after {max_wait_s}s — proceeding anyway",
                flush=True,
            )
            return False
        await asyncio.sleep(_READY_POLL_INTERVAL_S)


async def _wait_for_model_ref_runtime_ready(
    api: k8s_client.CustomObjectsApi,
    name: str,
    namespace: str,
    max_wait_s: float,
    log_prefix: str,
) -> bool:
    """Wait for MaaSModelRef RuntimeReady=True (backend healthy, HTTPRoute exists).

    MaaSModelRef.phase stays Pending until a MaaSSubscription is paired with
    the model — waiting for phase=Ready here would deadlock, since subscriptions
    are created in the next task. RuntimeReady=True is the correct pre-subscription
    signal: the simulator pod is up and the HTTPRoute is in place.
    """
    start = time.monotonic()
    while True:
        try:
            obj = api.get_namespaced_custom_object(
                group=_MAAS_GROUP, version=_MAAS_VERSION,
                namespace=namespace, plural=_MODEL_REF_PLURAL, name=name,
            )
            conditions = (obj or {}).get("status", {}).get("conditions", [])
            runtime_ready = next((c for c in conditions if c.get("type") == "RuntimeReady"), None)
            if runtime_ready and runtime_ready.get("status") == "True":
                phase = (obj or {}).get("status", {}).get("phase", "")
                print(f"[{log_prefix}] {name} MaaSModelRef RuntimeReady (phase={phase})", flush=True)
                return True
        except k8s_client.ApiException as exc:
            if exc.status != 404:
                raise
        elapsed = time.monotonic() - start
        if elapsed >= max_wait_s:
            print(
                f"[{log_prefix}] {name} MaaSModelRef not RuntimeReady after {max_wait_s}s — proceeding anyway",
                flush=True,
            )
            return False
        await asyncio.sleep(_READY_POLL_INTERVAL_S)


class DeploySimulatedModelTask(Task):
    """Creates LLMInferenceService CRs using the llm-d inference simulator
    image. Each instance gets a unique name and model name so MaaS treats
    them as distinct models, and the name is namespaced by run_id so
    concurrent/back-to-back runs (including different scenarios that share
    the same name_prefix/namespace defaults) never collide on the same CR.
    The controller auto-creates MaaSModelRef, HTTPRoute, and the backing
    Deployment.

    Stores each deployed model's name and namespace in
    shared_state["deployed_models"] for downstream tasks
    (provision_subscriptions_distributed).
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        count = int(self.params.get("count", 1))
        name_prefix = str(self.params.get("name_prefix", "maaspal-sim"))
        namespace = str(self.params.get("namespace", _DEFAULT_NAMESPACE))
        image = str(self.params.get("image", _DEFAULT_SIM_IMAGE))
        model_uri = str(self.params.get("model_uri", _DEFAULT_MODEL_URI))
        gateway_name = str(self.params.get("gateway_name", _DEFAULT_GATEWAY_NAME))
        gateway_namespace = str(self.params.get("gateway_namespace", _DEFAULT_GATEWAY_NAMESPACE))
        ready_max_wait_s = float(self.params.get("ready_max_wait_s", _DEFAULT_READY_MAX_WAIT_S))

        parallel_param = self.params.get("parallel", True)
        if isinstance(parallel_param, str):
            parallel = parallel_param.lower() not in ("false", "0", "no")
        else:
            parallel = bool(parallel_param)

        deployed = ctx.shared_state.setdefault("deployed_models", [])
        ready_count = 0

        async def _deploy_one(i: int) -> dict:
            nonlocal ready_count
            # run_id is baked into the name (matching the ctx.run_id[:8]
            # convention used for API key names in auth.py) so this run's CRs
            # never collide with another run's — otherwise two scenarios that
            # both default to name_prefix="maaspal-sim" would silently patch
            # and then cross-delete each other's LLMInferenceService/MaaSModelRef.
            isvc_name = f"{name_prefix}-{ctx.run_id[:8]}-{i + 1}"
            # Use the service name as the model name so each instance gets a
            # distinct MaaS model alias (e.g. maaspal-sim-a1b2c3d4-1).
            model_name = isvc_name
            api = k8s_client.CustomObjectsApi()

            # Step 1: create/patch the LLMInferenceService and wait for its pods/routes to be up.
            isvc_body = _llm_isvc_body(isvc_name, namespace, model_name, model_uri, image, gateway_name, gateway_namespace)
            try:
                api.get_namespaced_custom_object(
                    group=_ISVC_GROUP, version=_ISVC_VERSION, namespace=namespace, plural=_ISVC_PLURAL, name=isvc_name
                )
                api.patch_namespaced_custom_object(
                    group=_ISVC_GROUP, version=_ISVC_VERSION, namespace=namespace, plural=_ISVC_PLURAL, name=isvc_name, body=isvc_body
                )
                print(f"[deploy_simulated_model] patched LLMInferenceService {namespace}/{isvc_name}", flush=True)
                isvc_existed = True
            except k8s_client.ApiException as exc:
                if exc.status != 404:
                    raise
                api.create_namespaced_custom_object(
                    group=_ISVC_GROUP, version=_ISVC_VERSION, namespace=namespace, plural=_ISVC_PLURAL, body=isvc_body
                )
                print(f"[deploy_simulated_model] created LLMInferenceService {namespace}/{isvc_name}", flush=True)
                isvc_existed = False

            isvc_ready = await _wait_for_isvc_ready(api, isvc_name, namespace, ready_max_wait_s, "deploy_simulated_model")
            if not isvc_ready:
                ready_count += 1
                ctx.shared_state["task_progress"] = {"current": ready_count, "total": count}
                await ctx.emit_assertion_state()
                return {"name": isvc_name, "namespace": namespace, "isvc_created": not isvc_existed, "ref_created": False, "ready": False}

            # Step 2: create/patch the MaaSModelRef — the MaaS controller does not
            # auto-create these; they must be applied manually (confirmed live:
            # all existing MaaSModelRefs on this cluster have last-applied-configuration).
            ref_body = _model_ref_body(isvc_name, namespace)
            try:
                api.get_namespaced_custom_object(
                    group=_MAAS_GROUP, version=_MAAS_VERSION, namespace=namespace, plural=_MODEL_REF_PLURAL, name=isvc_name
                )
                api.patch_namespaced_custom_object(
                    group=_MAAS_GROUP, version=_MAAS_VERSION, namespace=namespace, plural=_MODEL_REF_PLURAL, name=isvc_name, body=ref_body
                )
                print(f"[deploy_simulated_model] patched MaaSModelRef {namespace}/{isvc_name}", flush=True)
                ref_existed = True
            except k8s_client.ApiException as exc:
                if exc.status != 404:
                    raise
                api.create_namespaced_custom_object(
                    group=_MAAS_GROUP, version=_MAAS_VERSION, namespace=namespace, plural=_MODEL_REF_PLURAL, body=ref_body
                )
                print(f"[deploy_simulated_model] created MaaSModelRef {namespace}/{isvc_name}", flush=True)
                ref_existed = False

            ref_ready = await _wait_for_model_ref_runtime_ready(api, isvc_name, namespace, ready_max_wait_s, "deploy_simulated_model")

            ready_count += 1
            ctx.shared_state["task_progress"] = {"current": ready_count, "total": count}
            await ctx.emit_assertion_state()
            return {"name": isvc_name, "namespace": namespace, "isvc_created": not isvc_existed, "ref_created": not ref_existed, "ready": ref_ready}

        if parallel:
            results = await asyncio.gather(*[_deploy_one(i) for i in range(count)])
        else:
            results = []
            for i in range(count):
                results.append(await _deploy_one(i))
        deployed.extend(results)

        not_ready = [r["name"] for r in results if not r["ready"]]
        ctx.shared_state["task_summary"] = (
            f"Deployed {len(results)} throwaway simulated model(s) in {namespace}: "
            f"{len(results) - len(not_ready)} ready"
            + (f", not ready: {', '.join(not_ready)}" if not_ready else "")
        )
        if not_ready:
            print(
                f"[deploy_simulated_model] WARNING: {len(not_ready)} model(s) did not become Ready "
                f"and will be excluded from subscription distribution: {not_ready}",
                flush=True,
            )

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        deployed = ctx.shared_state.get("deployed_models", [])
        if not deployed:
            return

        api = k8s_client.CustomObjectsApi()
        for m in reversed(deployed):
            name = m["name"]
            namespace = m["namespace"]
            if m.get("ref_created"):
                try:
                    api.delete_namespaced_custom_object(
                        group=_MAAS_GROUP, version=_MAAS_VERSION, namespace=namespace, plural=_MODEL_REF_PLURAL, name=name
                    )
                    print(f"[deploy_simulated_model] deleted MaaSModelRef {namespace}/{name}", flush=True)
                except k8s_client.ApiException as exc:
                    if exc.status != 404:
                        print(f"[deploy_simulated_model] cleanup MaaSModelRef {name} FAILED\n{traceback.format_exc()}", flush=True)
            if m.get("isvc_created"):
                try:
                    api.delete_namespaced_custom_object(
                        group=_ISVC_GROUP, version=_ISVC_VERSION, namespace=namespace, plural=_ISVC_PLURAL, name=name
                    )
                    print(f"[deploy_simulated_model] deleted LLMInferenceService {namespace}/{name}", flush=True)
                except k8s_client.ApiException as exc:
                    if exc.status != 404:
                        print(f"[deploy_simulated_model] cleanup LLMInferenceService {name} FAILED\n{traceback.format_exc()}", flush=True)


REGISTRY["deploy_simulated_model"] = DeploySimulatedModelTask
