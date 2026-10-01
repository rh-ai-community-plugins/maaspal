import time
import traceback

from kubernetes import client as k8s_client

from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

_GROUP = "maas.opendatahub.io"
_VERSION = "v1alpha1"
_PLURAL = "maasauthpolicies"


class ApplyAuthPolicyTask(Task):
    """Create or patch a MaaSAuthPolicy CR — the gateway-access half of the
    two-layer access model (a MaaSSubscription only grants quota; a
    MaaSAuthPolicy is what actually lets the gateway authorize a caller, see
    docs/architecture/maas-domain-reference.md Catalog item C). Schema
    confirmed live from the read side (api/maas_client.py:list_auth_policies,
    same group/version as MaaSSubscription): spec.subjects.{groups[].name,
    users[]}, spec.modelRefs[].{name, namespace} — no tokenRateLimits, since
    this CR only governs access, not quota.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()

        policy_name = str(self.params.get("policy_name", "maaspal-test-auth-policy"))
        namespace = str(self.params["namespace"])
        subject_groups = self.params.get("subject_groups") or []
        subject_users = self.params.get("subject_users") or []

        # Covers every model deploy_simulated_model created this run in one
        # policy — a scenario deploying N models (e.g. multi_model_full_load)
        # needs gateway access granted for all of them, not just one; task
        # params can't reference runtime shared_state via YAML templating,
        # same reason model_from_shared_state exists on send_requests/
        # apply_rate_limit_subscription.
        model_refs_from_shared_state = self.params.get("model_refs_from_shared_state")
        if model_refs_from_shared_state:
            models = ctx.shared_state.get(model_refs_from_shared_state, [])
            model_refs = [{"name": m["name"], "namespace": m["namespace"]} for m in models]
        else:
            model_refs = [
                {"name": str(self.params["model_name"]), "namespace": str(self.params["model_namespace"])}
            ]

        api = k8s_client.CustomObjectsApi()

        try:
            existing = api.get_namespaced_custom_object(
                group=_GROUP,
                version=_VERSION,
                namespace=namespace,
                plural=_PLURAL,
                name=policy_name,
            )
            ctx.shared_state["original_auth_policy"] = existing
            ctx.shared_state["_policy_created"] = False
        except k8s_client.ApiException as exc:
            if exc.status == 404:
                ctx.shared_state["original_auth_policy"] = None
                ctx.shared_state["_policy_created"] = True
            else:
                raise

        body = {
            "apiVersion": f"{_GROUP}/{_VERSION}",
            "kind": "MaaSAuthPolicy",
            "metadata": {"name": policy_name, "namespace": namespace},
            "spec": {
                "subjects": {
                    "groups": [{"name": g} for g in subject_groups],
                    "users": subject_users,
                },
                "modelRefs": model_refs,
            },
        }
        models_summary = ", ".join(f"{m['namespace']}/{m['name']}" for m in model_refs)

        if ctx.shared_state["_policy_created"]:
            api.create_namespaced_custom_object(
                group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL, body=body
            )
            print(
                f"[apply_auth_policy] created {policy_name} "
                f"subjects={subject_groups}/{subject_users} for {models_summary}",
                flush=True,
            )
        else:
            api.patch_namespaced_custom_object(
                group=_GROUP,
                version=_VERSION,
                namespace=namespace,
                plural=_PLURAL,
                name=policy_name,
                body=body,
            )
            print(
                f"[apply_auth_policy] patched {policy_name} "
                f"subjects={subject_groups}/{subject_users} for {models_summary}",
                flush=True,
            )

        ctx.shared_state["auth_policy_name"] = policy_name
        ctx.shared_state["auth_policy_namespace"] = namespace
        subjects = [*subject_groups, *subject_users]
        ctx.shared_state["task_summary"] = (
            f"{'Created' if ctx.shared_state.get('_policy_created') else 'Temporarily patched'} "
            f"auth policy {namespace}/{policy_name}: grants {', '.join(subjects) or '(nobody)'} "
            f"gateway access to {models_summary}"
        )
        await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        policy_name = ctx.shared_state.get("auth_policy_name")
        namespace = ctx.shared_state.get("auth_policy_namespace")
        if not policy_name or not namespace:
            return

        api = k8s_client.CustomObjectsApi()
        original = ctx.shared_state.get("original_auth_policy")
        created = ctx.shared_state.get("_policy_created", False)

        try:
            if created:
                api.delete_namespaced_custom_object(
                    group=_GROUP,
                    version=_VERSION,
                    namespace=namespace,
                    plural=_PLURAL,
                    name=policy_name,
                )
                print(f"[apply_auth_policy] deleted {policy_name}", flush=True)
            elif original is not None:
                api.replace_namespaced_custom_object(
                    group=_GROUP,
                    version=_VERSION,
                    namespace=namespace,
                    plural=_PLURAL,
                    name=policy_name,
                    body=original,
                )
                print(f"[apply_auth_policy] restored {policy_name}", flush=True)
        except Exception:
            print(
                f"[apply_auth_policy] cleanup FAILED\n{traceback.format_exc()}",
                flush=True,
            )


REGISTRY["apply_auth_policy"] = ApplyAuthPolicyTask
