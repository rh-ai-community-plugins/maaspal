import time

import httpx
from kubernetes import client as k8s_client

from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

_KUADRANT_GROUP = "kuadrant.io"
_KUADRANT_VERSION = "v1alpha1"
_GATEWAY_GROUP = "gateway.networking.k8s.io"
_GATEWAY_VERSION = "v1"

# Confirmed live on the cluster this repo targets — the one Gateway MaaS's
# own HTTPRoutes actually reference (there is a second, unrelated Gateway,
# data-science-gateway, in the same namespace — don't assume there's only
# one). Override via params if a different install names it differently.
_DEFAULT_GATEWAY_NAME = "maas-default-gateway"
_DEFAULT_GATEWAY_NAMESPACE = "openshift-ingress"


def _condition_true(conditions: list[dict], condition_type: str) -> bool:
    return any(c.get("type") == condition_type and c.get("status") == "True" for c in conditions)


def _list_maas_models() -> list[dict] | None:
    """The MaaS overview page's own merged model catalog (api/maas_client.py —
    shipped in this same image, run as this same ServiceAccount), reused so
    the governance checks below (Ready, subscriptions, auth policy, namespace
    gateway-access label) mean exactly what the UI shows. None if unreadable
    — those checks are then reported as unknown rather than guessed."""
    try:
        from api.maas_client import list_models

        result = list_models()
    except Exception as exc:
        print(f"[check_model_health] model catalog unavailable: {exc}", flush=True)
        return None
    if not result.available:
        print(f"[check_model_health] model catalog unavailable: {result.reason}", flush=True)
        return None
    return result.items


def _mark(value: bool | None) -> str:
    return "?" if value is None else ("✓" if value else "✗")


class CheckModelHealthTask(Task):
    """Read-only check that a model is wired up end to end — the CRs the MaaS
    Setup UI displays, against what they actually say about themselves. Not a
    claim about live traffic. No writes, no cleanup needed (ADR-022).

    Per model: MaaSModelRef Ready, at least one subscription and a matching
    MaaSAuthPolicy (the two-layer access model, ADR-018), the namespace's
    gateway-access label, the generated TokenRateLimitPolicy Accepted/Enforced,
    and HTTPRoute ownership. Plus the shared Gateway's Programmed condition.

    Leave `model_name` blank to check every internally-hosted model at once.
    Resources are found by label selector, not an assumed generated name —
    ADR-009's lesson (a schema assumption shipped silently broken) applies
    just as well to a resource *name* pattern. ExternalModel routing is a
    separate, still-open checklist item, so external models are skipped.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        model_name = str(self.params.get("model_name") or "")
        model_namespace = str(self.params.get("model_namespace") or "")
        gateway_name = str(self.params.get("gateway_name") or _DEFAULT_GATEWAY_NAME)
        gateway_namespace = str(self.params.get("gateway_namespace") or _DEFAULT_GATEWAY_NAMESPACE)

        catalog = _list_maas_models()
        by_ref = {(m.get("namespace"), m.get("name")): m for m in catalog or []}
        if model_name:
            targets = [(model_namespace, model_name)]
        elif catalog is not None:
            targets = sorted(
                (m["namespace"], m["name"]) for m in catalog if m.get("hosting") == "internal"
            )
        else:
            raise RuntimeError(
                "No model selected and the MaaS model catalog couldn't be read — "
                "pick a model explicitly, or check the Helm chart value rbac.maasReadonly"
            )

        api = k8s_client.CustomObjectsApi()

        gateway = api.get_namespaced_custom_object(
            group=_GATEWAY_GROUP,
            version=_GATEWAY_VERSION,
            namespace=gateway_namespace,
            plural="gateways",
            name=gateway_name,
        )
        gw_conditions = gateway.get("status", {}).get("conditions", [])
        ctx.shared_state["gateway_status"] = {
            "programmed": int(_condition_true(gw_conditions, "Programmed")),
        }
        print(
            f"[check_model_health] Gateway {gateway_namespace}/{gateway_name}: "
            f"{ctx.shared_state['gateway_status']}",
            flush=True,
        )

        counts = {
            "models_checked": 0,
            "rate_limit_enforced_count": 0,
            "route_owner_ok_count": 0,
            "healthy_count": 0,
        }
        if catalog is not None:
            counts.update(
                ready_count=0,
                has_subscription_count=0,
                has_auth_policy_count=0,
                gateway_access_label_count=0,
            )
        rows: list[list[str]] = []

        for i, (ns, name) in enumerate(targets):
            trlp, route = self._check_one(api, name, ns)
            info = by_ref.get((ns, name))
            ready = info.get("ready") if info else None
            has_sub = bool(info.get("subscriptions")) if info else None
            has_policy = info.get("has_auth_policy") if info else None
            ns_label = info.get("gateway_access_label") if info else None
            enforced = bool(trlp["accepted"] and trlp["enforced"])
            owner_ok = bool(route["owner_ref_matches"])

            counts["models_checked"] += 1
            counts["rate_limit_enforced_count"] += int(enforced)
            counts["route_owner_ok_count"] += int(owner_ok)
            if catalog is not None:
                counts["ready_count"] += int(bool(ready))
                counts["has_subscription_count"] += int(bool(has_sub))
                counts["has_auth_policy_count"] += int(bool(has_policy))
                counts["gateway_access_label_count"] += int(bool(ns_label))
            governance = [ready, has_sub, has_policy, ns_label] if catalog is not None else []
            counts["healthy_count"] += int(enforced and owner_ok and all(governance))

            rows.append([
                f"{ns}/{name}",
                _mark(ready),
                ", ".join(s["name"] for s in (info or {}).get("subscriptions") or []) or _mark(has_sub),
                _mark(has_policy),
                _mark(ns_label),
                _mark(enforced),
                _mark(owner_ok),
            ])
            # Single-model flags kept for assertions that reference one model
            # directly (and for the narrower per-CR view they give).
            if len(targets) == 1:
                ctx.shared_state["rate_limit_policy_status"] = trlp
                ctx.shared_state["http_route_status"] = route
            ctx.shared_state["task_progress"] = {"current": i + 1, "total": len(targets)}

        ctx.shared_state["model_health"] = counts
        ctx.shared_state.setdefault("_tables", {})["Model health"] = {
            "columns": [
                "Model", "Ready", "Subscriptions", "Auth policy",
                "Namespace gateway label", "Rate-limit policy enforced", "Route owner",
            ],
            "rows": rows,
        }
        unhealthy = counts["models_checked"] - counts["healthy_count"]
        ctx.shared_state["task_summary"] = (
            f"Checked {counts['models_checked']} model(s): "
            f"{counts['healthy_count']} fully healthy, {unhealthy} with issues · "
            f"Gateway {gateway_name} {'Programmed ✓' if ctx.shared_state['gateway_status']['programmed'] else 'NOT Programmed ✗'}"
            + ("" if catalog is not None else " · governance checks unavailable (catalog unreadable)")
        )

        await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    @staticmethod
    def _check_one(api: k8s_client.CustomObjectsApi, model_name: str, model_namespace: str) -> tuple[dict, dict]:
        trlp_items = api.list_namespaced_custom_object(
            group=_KUADRANT_GROUP,
            version=_KUADRANT_VERSION,
            namespace=model_namespace,
            plural="tokenratelimitpolicies",
            label_selector=f"maas.opendatahub.io/model={model_name}",
        ).get("items", [])
        trlp_found = bool(trlp_items)
        trlp_conditions = trlp_items[0].get("status", {}).get("conditions", []) if trlp_found else []
        trlp = {
            "found": int(trlp_found),
            "accepted": int(_condition_true(trlp_conditions, "Accepted")),
            "enforced": int(_condition_true(trlp_conditions, "Enforced")),
        }

        route_items = api.list_namespaced_custom_object(
            group=_GATEWAY_GROUP,
            version=_GATEWAY_VERSION,
            namespace=model_namespace,
            plural="httproutes",
            label_selector=f"app.kubernetes.io/name={model_name}",
        ).get("items", [])
        route_found = bool(route_items)
        owner_refs = route_items[0].get("metadata", {}).get("ownerReferences", []) if route_found else []
        route = {
            "found": int(route_found),
            "owner_ref_matches": int(
                any(
                    ref.get("kind") == "LLMInferenceService" and ref.get("name") == model_name
                    for ref in owner_refs
                )
            ),
        }
        print(
            f"[check_model_health] {model_namespace}/{model_name}: "
            f"TokenRateLimitPolicy={trlp} HTTPRoute={route}",
            flush=True,
        )
        return trlp, route

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


class CheckPlatformHealthTask(Task):
    """API-level platform health check — calls GET /v1/models with the SA
    token to confirm the MaaS endpoint is reachable and at least one model is
    registered. No Kubernetes client, no CR reads — entirely through the MaaS
    REST API, as an end user would experience it.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()

        url = f"{ctx.maas_api_url}/v1/models"
        print(f"[check_platform_health] GET {url}", flush=True)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {ctx.sa_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

        models = data.get("data", [])
        model_count = len(models)
        ctx.shared_state["platform_health"] = {
            "model_count": model_count,
            "api_reachable": 1,
        }
        print(
            f"[check_platform_health] MaaS API reachable; {model_count} model(s) registered:",
            flush=True,
        )
        for m in models:
            print(
                f"  id={m.get('id')} owned_by={m.get('owned_by', '')}",
                flush=True,
            )
        ctx.shared_state["task_summary"] = (
            f"MaaS API reachable · {model_count} model(s) visible: "
            + (", ".join(str(m.get("id")) for m in models[:5]) or "none")
            + (" …" if model_count > 5 else "")
        )
        await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


REGISTRY["check_model_health"] = CheckModelHealthTask
REGISTRY["check_platform_health"] = CheckPlatformHealthTask
