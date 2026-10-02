import asyncio
import random
import time
import traceback

from kubernetes import client as k8s_client

from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext, record_created
from harness.tasks.registry import REGISTRY

_GROUP = "maas.opendatahub.io"
_VERSION = "v1alpha1"
_PLURAL = "maassubscriptions"

# High enough to win over any pre-existing subscription targeting the same
# model (upstream's recommended scheme tops out around 100 for prod tiers),
# so this scenario's behavior doesn't depend on what else happens to be
# configured on the cluster.
_DEFAULT_PRIORITY = 100

# Kuadrant's TokenRateLimitPolicy counts tokens per window, not requests —
# there is no literal "requests per second" field on MaaSSubscription (see
# ADR-009's Update section). Defaulting the window to 1 second makes
# `token_limit` directly comparable, unit-for-unit, to the harness's own
# `token_throughput_per_sec` measurement (harness/tasks/inference.py) with no
# conversion — pass a different `token_window` only if you also adjust what
# you compare `token_limit` against.
_DEFAULT_TOKEN_WINDOW = "1s"

# ServiceAccount tokens resolve to this group via Kubernetes TokenReview
# (confirmed live: `auth.identity.user.groups` includes `system:authenticated`
# for a ServiceAccount caller), which is also how the cluster's own sample
# subscriptions (simulator-free/simulator-premium) grant access — so this is
# the one group virtually guaranteed to actually match the harness's own
# identity without needing cluster-specific group names as a scenario input.
_DEFAULT_OWNER_GROUPS = ["system:authenticated"]

# Confirmed live: a MaaSSubscription just created/patched via the K8s API is
# accepted immediately, but the MaaS controller reconciles it asynchronously —
# calling POST /maas-api/v1/api-keys with `subscription` pinned to it right
# after creation can lose that race and get back
# `400 {"code":"subscription_not_ready","error":"subscription is unreconciled
# (no status.phase set)"}`. Poll for a non-empty status.phase before moving
# on, rather than assuming the create/patch call being accepted means the
# subscription is actually usable yet.
_DEFAULT_READY_MAX_WAIT_S = 30.0
_READY_POLL_INTERVAL_S = 2.0


async def _wait_for_subscription_ready(
    api: k8s_client.CustomObjectsApi,
    sub_name: str,
    namespace: str,
    max_wait_s: float,
    log_prefix: str,
) -> None:
    start = time.monotonic()
    while True:
        obj = _get_existing_subscription(api, sub_name, namespace)
        phase = (obj or {}).get("status", {}).get("phase")
        if phase:
            print(f"[{log_prefix}] {sub_name} reconciled, status.phase={phase!r}", flush=True)
            return
        elapsed = time.monotonic() - start
        if elapsed >= max_wait_s:
            print(
                f"[{log_prefix}] {sub_name} still unreconciled after {max_wait_s}s "
                "(no status.phase) — proceeding anyway; the next task will surface "
                "the real error if it's still not ready",
                flush=True,
            )
            return
        await asyncio.sleep(_READY_POLL_INTERVAL_S)


_KUADRANT_GROUP = "kuadrant.io"
_KUADRANT_VERSION = "v1alpha1"
_TRLP_PLURAL = "tokenratelimitpolicies"
_DEFAULT_TRLP_READY_MAX_WAIT_S = 60.0
_TRLP_POLL_INTERVAL_S = 3.0


def _trlp_ready(api: k8s_client.CustomObjectsApi, model_name: str, model_namespace: str) -> bool:
    # Same label-selector lookup as harness/tasks/platform_health.py:
    # CheckModelHealthTask — found by label, not an assumed generated name.
    items = api.list_namespaced_custom_object(
        group=_KUADRANT_GROUP,
        version=_KUADRANT_VERSION,
        namespace=model_namespace,
        plural=_TRLP_PLURAL,
        label_selector=f"maas.opendatahub.io/model={model_name}",
    ).get("items", [])
    if not items:
        return False
    conditions = items[0].get("status", {}).get("conditions", [])
    accepted = any(c.get("type") == "Accepted" and c.get("status") == "True" for c in conditions)
    enforced = any(c.get("type") == "Enforced" and c.get("status") == "True" for c in conditions)
    return accepted and enforced


async def _wait_for_token_rate_limit_policies_ready(
    api: k8s_client.CustomObjectsApi,
    models: list[dict],
    max_wait_s: float,
    log_prefix: str,
) -> None:
    """Poll each model's TokenRateLimitPolicy until both its Accepted and
    Enforced conditions are True.

    Distinct from _wait_for_subscription_ready above: multiple subscriptions
    can reference overlapping models and race each other reconciling the
    SAME per-model TokenRateLimitPolicy — confirmed live: "the object has
    been modified; please apply your changes to the latest version and try
    again" conflicts, auto-retried by the controller, while creating several
    subscriptions in a distributed spread (harness/tasks/subscription.py's
    ProvisionSubscriptionsDistributedTask). A subscription's own
    status.phase can be set (even "Degraded") before those races finish
    resolving, so it isn't the signal that actually gates traffic — this
    checks the real thing instead, same principle as _settle_and_evaluate()
    in harness/runner.py uses for metrics.
    """
    unique = {(m["name"], m["namespace"]) for m in models}
    pending = set(unique)
    start = time.monotonic()
    while pending:
        for key in list(pending):
            name, namespace = key
            if _trlp_ready(api, name, namespace):
                pending.discard(key)
        if not pending:
            print(
                f"[{log_prefix}] TokenRateLimitPolicy ready (Accepted+Enforced) for "
                f"all {len(unique)} model(s)",
                flush=True,
            )
            return
        elapsed = time.monotonic() - start
        if elapsed >= max_wait_s:
            still_pending = sorted(name for name, _ in pending)
            print(
                f"[{log_prefix}] TokenRateLimitPolicy still not Accepted+Enforced after "
                f"{max_wait_s}s for {still_pending} — proceeding anyway; the next task "
                "will surface the real error if it's still not ready",
                flush=True,
            )
            return
        await asyncio.sleep(_TRLP_POLL_INTERVAL_S)


def _subscription_body(
    sub_name: str,
    namespace: str,
    priority: int,
    owner_groups: list[str],
    owner_users: list[str],
    model_name: str,
    model_namespace: str,
    token_limit: int,
    token_window: str,
    *,
    model_refs: list[dict] | None = None,
) -> dict:
    # Real MaaSSubscription schema (confirmed live, see
    # docs/architecture/maas-domain-reference.md Catalog item A) — there is
    # no top-level `rpsLimit` field; rate limits are per-model, under
    # `modelRefs[].tokenRateLimits[]`.
    #
    # model_refs: if provided, each entry is {name, namespace, token_limit?,
    # token_window?} and is used directly, overriding the single-model params.
    if model_refs is not None:
        refs_spec = [
            {
                "name": m["name"],
                "namespace": m["namespace"],
                "tokenRateLimits": [
                    {"limit": m.get("token_limit", token_limit), "window": m.get("token_window", token_window)}
                ],
            }
            for m in model_refs
        ]
    else:
        refs_spec = [
            {
                "name": model_name,
                "namespace": model_namespace,
                "tokenRateLimits": [{"limit": token_limit, "window": token_window}],
            }
        ]
    return {
        "apiVersion": f"{_GROUP}/{_VERSION}",
        "kind": "MaaSSubscription",
        "metadata": {"name": sub_name, "namespace": namespace},
        "spec": {
            "priority": priority,
            "owner": {
                "groups": [{"name": g} for g in owner_groups],
                "users": owner_users,
            },
            "modelRefs": refs_spec,
        },
    }


def _get_existing_subscription(api: k8s_client.CustomObjectsApi, sub_name: str, namespace: str):
    try:
        return api.get_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL, name=sub_name
        )
    except k8s_client.ApiException as exc:
        if exc.status == 404:
            return None
        raise


def _create_or_patch_subscription(
    api: k8s_client.CustomObjectsApi, sub_name: str, namespace: str, body: dict, existing
) -> None:
    if existing is None:
        api.create_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL, body=body
        )
    else:
        api.patch_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL,
            name=sub_name, body=body,
        )


def _cleanup_subscription(
    api: k8s_client.CustomObjectsApi, sub_name: str, namespace: str, original, created: bool
) -> None:
    if created:
        api.delete_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL, name=sub_name
        )
    elif original is not None:
        api.replace_namespaced_custom_object(
            group=_GROUP, version=_VERSION, namespace=namespace, plural=_PLURAL,
            name=sub_name, body=original,
        )


class ApplyRateLimitSubscriptionTask(Task):
    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()

        sub_name = str(self.params.get("new_subscription_name", "maaspal-test-subscription"))
        namespace = str(self.params["namespace"])
        # ADR-018's third Update: lets a subscription target a model
        # deploy_simulated_model (harness/tasks/model.py) just created at
        # runtime — task params can't reference shared_state via YAML
        # templating, same reason owner_users_from_shared_state exists below.
        model_from_shared_state = self.params.get("model_from_shared_state")
        if model_from_shared_state:
            models = ctx.shared_state.get(model_from_shared_state, [])
            if not models:
                raise RuntimeError(
                    f"model_from_shared_state={model_from_shared_state!r} but "
                    "shared_state has no entries — run deploy_simulated_model first"
                )
            model_name = str(models[0]["name"])
            model_namespace = str(models[0]["namespace"])
        else:
            model_name = str(self.params["model_name"])
            model_namespace = str(self.params["model_namespace"])
        token_limit = int(self.params.get("token_limit", 10))
        token_window = str(self.params.get("token_window") or _DEFAULT_TOKEN_WINDOW)
        priority = int(self.params.get("priority", _DEFAULT_PRIORITY))
        # Two-arg .get(), not `or` — an explicit owner_groups: [] must stick
        # (e.g. to keep a test subscription's blast radius narrower than
        # system:authenticated, ADR-023); `[] or X` would silently discard it.
        owner_groups = self.params.get("owner_groups", _DEFAULT_OWNER_GROUPS)
        owner_users = self.params.get("owner_users") or []
        # ADR-023: lets a scenario bind owner.users to identities minted at
        # runtime by create_user (harness/tasks/identity.py) — task params
        # can't use ${harness.x} substitution (that's assertion-only, see
        # harness/runner.py), so this reads shared_state directly instead.
        owner_users_from_shared_state = self.params.get("owner_users_from_shared_state")
        if owner_users_from_shared_state:
            identities = ctx.shared_state.get(owner_users_from_shared_state, [])
            owner_users = owner_users + [u["username"] for u in identities]
        ready_max_wait_s = float(self.params.get("ready_max_wait_s", _DEFAULT_READY_MAX_WAIT_S))

        api = k8s_client.CustomObjectsApi()
        existing = _get_existing_subscription(api, sub_name, namespace)
        ctx.shared_state["original_subscription"] = existing
        ctx.shared_state["_sub_created"] = existing is None

        body = _subscription_body(
            sub_name, namespace, priority, owner_groups, owner_users,
            model_name, model_namespace, token_limit, token_window,
        )
        _create_or_patch_subscription(api, sub_name, namespace, body, existing)
        record_created(ctx, self.name, "MaaSSubscription", f"{namespace}/{sub_name}", existed=existing is not None)
        print(
            f"[apply_rate_limit_subscription] {'created' if existing is None else 'patched'} "
            f"{sub_name} token_limit={token_limit}/{token_window} for {model_namespace}/{model_name}",
            flush=True,
        )
        await _wait_for_subscription_ready(
            api, sub_name, namespace, ready_max_wait_s, "apply_rate_limit_subscription"
        )

        ctx.shared_state["new_subscription_name"] = sub_name
        ctx.shared_state["subscription_namespace"] = namespace
        owners = [*(owner_groups or []), *owner_users]
        ctx.shared_state["task_summary"] = (
            f"{'Created' if existing is None else 'Temporarily patched'} subscription "
            f"{namespace}/{sub_name}: {token_limit} tokens per {token_window} on "
            f"{model_namespace}/{model_name} · owners: {', '.join(owners) or '(none)'}"
        )
        await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        sub_name = ctx.shared_state.get("new_subscription_name")
        namespace = ctx.shared_state.get("subscription_namespace")
        if not sub_name or not namespace:
            return

        api = k8s_client.CustomObjectsApi()
        original = ctx.shared_state.get("original_subscription")
        created = ctx.shared_state.get("_sub_created", False)

        try:
            _cleanup_subscription(api, sub_name, namespace, original, created)
            print(
                f"[apply_rate_limit_subscription] {'deleted' if created else 'restored'} {sub_name}",
                flush=True,
            )
        except Exception:
            print(
                f"[apply_rate_limit_subscription] cleanup FAILED\n{traceback.format_exc()}",
                flush=True,
            )


class ApplyPriorityTestSubscriptionsTask(Task):
    """Creates multiple MaaSSubscriptions in one task invocation, all sharing
    the same owner (so they compete for the same caller's auto-selection),
    each with its own priority/token_limit — used to empirically test that
    MaaS's auto-selection actually picks the highest-priority eligible
    subscription for a caller (ADR-021,
    docs/architecture/empirical-verification-checklist.md).

    A second YAML task entry for ApplyRateLimitSubscriptionTask (even under a
    different registered name) would NOT work here: that task's shared_state
    bookkeeping (new_subscription_name/_sub_created/original_subscription) is a
    fixed key, not namespaced per task instance — two instances in one
    scenario would silently clobber each other's cleanup state before
    cleanup() ever runs. This task tracks a *list* instead, one entry per
    subscription, so each gets cleaned up independently and correctly.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        namespace = str(self.params["namespace"])
        model_name = str(self.params["model_name"])
        model_namespace = str(self.params["model_namespace"])
        owner_groups = self.params.get("owner_groups", _DEFAULT_OWNER_GROUPS)
        owner_users = self.params.get("owner_users") or []
        ready_max_wait_s = float(self.params.get("ready_max_wait_s", _DEFAULT_READY_MAX_WAIT_S))
        specs = self.params["subscriptions"]

        api = k8s_client.CustomObjectsApi()
        records = []
        for spec in specs:
            sub_name = str(spec["name"])
            priority = int(spec["priority"])
            token_limit = int(spec.get("token_limit", 10))
            token_window = str(spec.get("token_window") or _DEFAULT_TOKEN_WINDOW)

            existing = _get_existing_subscription(api, sub_name, namespace)
            body = _subscription_body(
                sub_name, namespace, priority, owner_groups, owner_users,
                model_name, model_namespace, token_limit, token_window,
            )
            _create_or_patch_subscription(api, sub_name, namespace, body, existing)
            record_created(ctx, self.name, "MaaSSubscription", f"{namespace}/{sub_name}", existed=existing is not None)
            print(
                f"[apply_priority_test_subscriptions] "
                f"{'created' if existing is None else 'patched'} {sub_name} "
                f"priority={priority} token_limit={token_limit}/{token_window}",
                flush=True,
            )
            await _wait_for_subscription_ready(
                api, sub_name, namespace, ready_max_wait_s, "apply_priority_test_subscriptions"
            )
            records.append(
                {
                    "name": sub_name,
                    "namespace": namespace,
                    "original": existing,
                    "created": existing is None,
                }
            )

        ctx.shared_state["priority_test_subscriptions"] = records
        ctx.shared_state["task_summary"] = "Created " + "; ".join(
            f"{s['name']} (priority {s['priority']}, {s.get('token_limit', 10)} tokens)"
            for s in specs
        ) + f" on {model_namespace}/{model_name}"
        await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        records = ctx.shared_state.get("priority_test_subscriptions", [])
        if not records:
            return

        api = k8s_client.CustomObjectsApi()
        for rec in records:
            try:
                _cleanup_subscription(api, rec["name"], rec["namespace"], rec["original"], rec["created"])
                print(
                    f"[apply_priority_test_subscriptions] "
                    f"{'deleted' if rec['created'] else 'restored'} {rec['name']}",
                    flush=True,
                )
            except Exception:
                print(
                    f"[apply_priority_test_subscriptions] cleanup FAILED for {rec['name']}\n"
                    f"{traceback.format_exc()}",
                    flush=True,
                )


def _select_models_weighted(models: list[dict], k: int, usage_counts: dict[str, int]) -> list[dict]:
    """Pick k models from the pool without replacement, weighted toward those
    with lower usage counts so subscriptions spread evenly across models."""
    remaining = [(m, 1.0 / (usage_counts.get(m["name"], 0) + 1)) for m in models]
    selected = []
    for _ in range(k):
        total = sum(w for _, w in remaining)
        r = random.uniform(0, total)
        cumsum = 0.0
        for j, (m, w) in enumerate(remaining):
            cumsum += w
            if r <= cumsum or j == len(remaining) - 1:
                selected.append(m)
                remaining.pop(j)
                break
    return selected


class ProvisionSubscriptionsDistributedTask(Task):
    """Creates subscription_count MaaSSubscription CRs distributed randomly
    across the models in shared_state["deployed_models"]. Each subscription
    references 1..max_models_per_subscription models (or up to all models when
    max_models_per_subscription=0). Selection is weighted toward underused
    models so coverage is roughly even.

    Stores records in shared_state["distributed_subscriptions"] so cleanup
    can delete each independently. Downstream tasks (provision_keys_distributed)
    read the same key to pin keys to specific subscriptions.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        all_models: list[dict] = ctx.shared_state.get("deployed_models", [])
        if not all_models:
            raise RuntimeError("provision_subscriptions_distributed requires deployed_models in shared_state — run deploy_simulated_model first")
        # Only distribute across models that actually became Ready — a
        # subscription referencing a model with no Ready MaaSModelRef fails
        # immediately at the controller level.
        deployed_models = [m for m in all_models if m.get("ready", True)]
        if not deployed_models:
            raise RuntimeError("provision_subscriptions_distributed: no deployed models became Ready — cannot create subscriptions")
        if len(deployed_models) < len(all_models):
            skipped = [m["name"] for m in all_models if not m.get("ready", True)]
            print(
                f"[provision_subscriptions_distributed] skipping {len(skipped)} non-Ready model(s): {skipped}",
                flush=True,
            )

        subscription_count = int(self.params.get("subscription_count", 6))
        name_prefix = str(self.params.get("name_prefix", "maaspal-dist-sub"))
        namespace = str(self.params.get("namespace", "models-as-a-service"))
        max_models_per_sub = int(self.params.get("max_models_per_subscription", 0))
        token_limit = int(self.params.get("token_limit", 1000000))
        token_window = str(self.params.get("token_window") or _DEFAULT_TOKEN_WINDOW)
        priority = int(self.params.get("priority", _DEFAULT_PRIORITY))
        owner_groups = self.params.get("owner_groups", _DEFAULT_OWNER_GROUPS)
        owner_users = self.params.get("owner_users") or []
        ready_max_wait_s = float(self.params.get("ready_max_wait_s", _DEFAULT_READY_MAX_WAIT_S))
        token_rate_limit_ready_max_wait_s = float(
            self.params.get("token_rate_limit_ready_max_wait_s", _DEFAULT_TRLP_READY_MAX_WAIT_S)
        )

        cap = min(max_models_per_sub, len(deployed_models)) if max_models_per_sub > 0 else len(deployed_models)
        usage_counts: dict[str, int] = {m["name"]: 0 for m in deployed_models}

        api = k8s_client.CustomObjectsApi()
        records = []
        for i in range(subscription_count):
            sub_name = f"{name_prefix}-{i + 1}"
            k = random.randint(1, cap)
            selected = _select_models_weighted(deployed_models, k, usage_counts)
            for m in selected:
                usage_counts[m["name"]] = usage_counts.get(m["name"], 0) + 1

            model_refs = [{"name": m["name"], "namespace": m["namespace"], "token_limit": token_limit, "token_window": token_window} for m in selected]
            body = _subscription_body(
                sub_name, namespace, priority, owner_groups, owner_users,
                "", "", token_limit, token_window,
                model_refs=model_refs,
            )
            existing = _get_existing_subscription(api, sub_name, namespace)
            _create_or_patch_subscription(api, sub_name, namespace, body, existing)
            record_created(ctx, self.name, "MaaSSubscription", f"{namespace}/{sub_name}", existed=existing is not None)
            model_summary = ", ".join(m["name"] for m in selected)
            print(
                f"[provision_subscriptions_distributed] "
                f"{'created' if existing is None else 'patched'} {sub_name} "
                f"models=[{model_summary}]",
                flush=True,
            )
            await _wait_for_subscription_ready(
                api, sub_name, namespace, ready_max_wait_s, "provision_subscriptions_distributed"
            )
            records.append({
                "name": sub_name,
                "namespace": namespace,
                "original": existing,
                "created": existing is None,
                "model_refs": model_refs,
            })
            ctx.shared_state["task_progress"] = {"current": i + 1, "total": subscription_count}
            ctx.shared_state["task_summary"] = (
                f"Created {i + 1} subscriptions spread across "
                f"{len({(m['namespace'], m['name']) for r in records for m in r['model_refs']})} models"
            )
            await ctx.emit_assertion_state()

        ctx.shared_state["distributed_subscriptions"] = records

        all_model_refs = [m for rec in records for m in rec["model_refs"]]
        await _wait_for_token_rate_limit_policies_ready(
            api, all_model_refs, token_rate_limit_ready_max_wait_s, "provision_subscriptions_distributed"
        )

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        records = ctx.shared_state.get("distributed_subscriptions", [])
        if not records:
            return
        api = k8s_client.CustomObjectsApi()
        for rec in records:
            try:
                _cleanup_subscription(api, rec["name"], rec["namespace"], rec["original"], rec["created"])
                print(
                    f"[provision_subscriptions_distributed] "
                    f"{'deleted' if rec['created'] else 'restored'} {rec['name']}",
                    flush=True,
                )
            except Exception:
                print(
                    f"[provision_subscriptions_distributed] cleanup FAILED for {rec['name']}\n"
                    f"{traceback.format_exc()}",
                    flush=True,
                )


REGISTRY["apply_rate_limit_subscription"] = ApplyRateLimitSubscriptionTask
REGISTRY["apply_priority_test_subscriptions"] = ApplyPriorityTestSubscriptionsTask
REGISTRY["provision_subscriptions_distributed"] = ProvisionSubscriptionsDistributedTask
