# ADR-009: MaaSSubscription Rate Limits via Kubernetes CRD

## Status

Accepted

## Context

The `rate_limit_validation` scenario needs to configure a rate limit on a MaaS subscription, fire a burst of requests, and then assert that observed throughput stayed at or below the configured limit.

The MaaS REST API (`/maas-api/v1/...`) provides endpoints for API key lifecycle operations only. There is no REST endpoint for creating or modifying subscriptions or their rate limits.

Options considered:

1. **REST API**: not available. Ruled out.
2. **`oc` / `kubectl` CLI inside the Job pod**: shelling out to `kubectl apply` works but introduces a dependency on the binary being present in the image and is harder to test.
3. **`kubernetes.client.CustomObjectsApi`**: the Kubernetes Python client (already a dependency) can create and patch arbitrary Custom Resources, including `MaaSSubscription` objects from the `maas.opendatahub.io/v1alpha1` API group.

## Decision

Manage `MaaSSubscription` CRs using **`kubernetes.client.CustomObjectsApi`** in the `apply_rate_limit_subscription` task (`harness/tasks/subscription.py`). The task creates or patches the CR with the desired `rps_limit`, stores the original CR state in `shared_state["original_subscription"]`, and its `cleanup()` restores or deletes the CR.

Group/version/kind:
- `group`: `maas.opendatahub.io`
- `version`: `v1alpha1`
- `plural`: `maassubscriptions`

## Consequences

**Positive:**
- No extra binary dependencies in the image; `kubernetes` client is already required for Job management.
- Full programmatic control: create, patch, get, and delete operations are all available.
- Cleanup can precisely restore the original state rather than simply deleting the CR (in case the subscription pre-existed).
- Consistent with how the rest of the harness interacts with the cluster (Kubernetes Python client throughout).

**Negative:**
- The `MaaSSubscription` CRD schema is not yet fully documented; the task implementation will need to be updated if the schema changes between RHOAI versions.
- The SA must have `get`, `create`, `patch`, `delete` on `maassubscriptions` in the appropriate namespace. This must be added to `rbac.yaml`.

**Neutral:**
- This is the only task that manages a Kubernetes CR directly; all other Kubernetes interactions are limited to Jobs and Pods.
- The MaaS REST API remains the correct path for API key lifecycle; this decision does not affect that layer.

## Update: schema, namespace, and RBAC corrections

The "Negative" consequence above was exactly right to flag: live-cluster research (see `docs/architecture/maas-domain-reference.md` Catalog item A, and ADR-017) found the real `MaaSSubscription` schema has no `spec.rpsLimit` at all. Three separate, compounding bugs were found and fixed together, since fixing only one would have left the scenario silently broken:

1. **Wrong schema.** The real shape is `spec.priority` (int), `spec.owner.{groups[].name, users[]}`, `spec.modelRefs[].tokenRateLimits[].{limit, window}` — rate limits are per-model, in tokens per time window, not a flat `rpsLimit`. `harness/tasks/subscription.py` now builds this shape and requires `model_name`/`model_namespace` params (previously only `subscription_name`/`namespace`, since the old schema had no per-model concept at all).

2. **Wrong namespace, silently.** `deploy/rbac.yaml`'s original grant was a namespaced `Role` bound only to the `maaspal` namespace, and the task's default (`ctx.config.get("NAMESPACE", "default")`, i.e. `NAMESPACE=maaspal` from the global ConfigMap) created the CR *in the maaspal namespace*. A `MaaSSubscription` only gets reconciled by the MaaS controller when it lives in the install's actual tenant namespace (e.g. `models-as-a-service`) — a CR in `maaspal` is accepted by the API server (CRDs are cluster-wide resource *types*; any namespace is syntactically valid) but the controller never looks at it there. This means `rate_limit_validation` had never actually exercised real rate-limit enforcement, regardless of the schema bug — it created an inert object every time. Fixed by moving this grant to a new cluster-scoped `deploy/rbac-maas-subscription-write.yaml` (`ClusterRole`/`ClusterRoleBinding`, same reasoning as `rbac-maas-readonly.yaml`: the tenant namespace is install-specific, not fixed) and making `namespace` a required scenario config value (`subscription_namespace`) instead of silently defaulting to the harness's own namespace.

3. **No guarantee the test's own keys would use this subscription.** Even with (1) and (2) fixed, `provision_api_key`'s created keys resolve to *whichever* subscription the caller's identity is eligible for (highest priority among matches) unless told otherwise — with other subscriptions potentially present on the cluster (e.g. the sample `simulator-free`/`simulator-premium`), there was no guarantee the harness's own test keys would land on the CR this scenario just created and is trying to validate. Fixed by giving `provision_api_key` an optional `subscription` param, passed through to `POST /maas-api/v1/api-keys`'s `subscription` field (confirmed in the community guide's request shape), and wiring `rate_limit_validation.yaml` to pass the same `${config.subscription_name}` to both tasks.

**Consequence of (1): the assertion changed meaning, not just its field name.** Kuadrant's `TokenRateLimitPolicy` (what a `MaaSSubscription` compiles down to, see ADR-017 Catalog item D) counts *tokens* per window, not requests — there is no way to configure a literal requests-per-second limit via this CRD. The old scenario's `throughput_rps <= rate_limit_rps` assertion was checking a dimension the CRD can't actually enforce. Fixed honestly rather than patched around: `harness/tasks/inference.py` gained a `token_throughput_per_sec` metric (mirroring the existing `throughput_rps` computation, same `elapsed` denominator), and the task defaults the CR's `tokenRateLimits[].window` to `"1s"` specifically so the configured `token_limit` is directly comparable, unit-for-unit, to that new metric with no conversion — `rate_limit_validation.yaml`'s assertion is now `token_throughput_per_sec <= token_limit`. Anyone overriding `token_window` to something other than `"1s"` needs to adjust what they compare `token_limit` against themselves; this isn't done automatically.

**Not yet verified against a real cluster** — this fix was written against the documented/researched schema (confirmed live for read purposes throughout ADR-017's work) but the corrected write path itself has only been exercised by mocked unit tests (`harness/tests/test_subscription.py`), not a live `rate_limit_validation` run. Verify end-to-end before relying on this scenario's pass/fail result.

## Update: harness process never actually configured the Kubernetes client

The caveat directly above was exactly right, and turned out to be hiding a fourth, more fundamental bug than the three above — one that would have broken *every* task using `kubernetes.client.CustomObjectsApi()` in the harness, not just this one. `check_platform_health` (ADR-022) was the first CR-based task ever actually run as a live Job (everything before it was verified either via mocked unit tests or by `oc` directly, never by triggering a real run), and it failed immediately with `urllib3.exceptions.LocationValueError: No host specified`.

Root cause: `api/k8s.py` and `api/maas_client.py` (the **API server** process) both call `kubernetes.config.load_incluster_config()` (with a local-kubeconfig fallback) before using the client — but nothing equivalent ever existed for the **harness Job** process. `harness/main.py` never configured the Kubernetes client at all; `k8s_client.CustomObjectsApi()` was being instantiated with no idea what cluster to talk to. This affected `apply_rate_limit_subscription`/`apply_priority_test_subscriptions` (`subscription.py`), `apply_auth_policy` (`access_policy.py`, ADR-018), and `check_platform_health` (`platform_health.py`, ADR-022) identically — none of them could have worked live, and none had been run live until now.

Fixed by adding `harness/main.py:_load_kube_config()` (the exact same incluster-then-kubeconfig pattern as `api/k8s.py:_kube()`), called once at process startup before `ScenarioRunner` runs — configures the kubernetes client's process-global default connection, so every task's `CustomObjectsApi()` call picks it up with no per-task change needed. Covered by `harness/tests/test_main.py`.

This is the second time in this ADR's history that "not yet verified against a real cluster" turned out to be masking a real bug once actually tried — a pattern worth remembering rather than treating the caveat as boilerplate.

**Confirmed fixed, live**: after this fix shipped, `platform_health_check` (read-only) and `rate_limit_priority_precedence` (creates/patches/deletes two `MaaSSubscription`s) were both run against `cluster-rkmhx.rkmhx.sandbox1230.opentlc.com` and passed — the `LocationValueError` is gone, and the underlying task logic each ADR describes worked as designed on the first real attempt. `rate_limit_validation` and `access_denied_no_policy` haven't been individually re-run yet but share the exact same root cause and fix, so they're expected to be unblocked too.

## Update: subscription create/patch doesn't wait for reconciliation

A fifth bug, found when `rate_limit_validation`/`access_denied_no_policy` were actually run live for the first time (after the kube-config fix above unblocked them): `provision_api_key` failed with `400 {"code":"subscription_not_ready","error":"subscription is unreconciled (no status.phase set)"}`. `ApplyRateLimitSubscriptionTask.run()` creates/patches the `MaaSSubscription` CR and returns immediately — the K8s API server accepting the write only means the object exists, not that the MaaS controller has reconciled it yet. `provision_api_key`, running as the very next task, was calling `POST /maas-api/v1/api-keys` with `subscription` pinned to a CR that hadn't been reconciled, and the MaaS API correctly rejected it. `rate_limit_priority_precedence` happened not to hit this in its one live run — creating two subscriptions in a loop apparently left enough incidental time for the first to reconcile — but that was luck, not a guarantee, and the exact same race applies to `ApplyPriorityTestSubscriptionsTask` too.

Fixed with `_wait_for_subscription_ready()` (`harness/tasks/subscription.py`): after create/patch, poll `status.phase` (every 2s, `ready_max_wait_s` param, default 30s) until it's set, logging and proceeding anyway on timeout rather than failing outright — the next task's own call still surfaces a real error if it's genuinely still not ready. Shared by both `ApplyRateLimitSubscriptionTask` and `ApplyPriorityTestSubscriptionsTask`. Covered by `harness/tests/test_subscription.py`'s `test_wait_for_subscription_ready_*` tests; confirmed live (see ADR-024).

## Update: assertion moved on again — `token_throughput_per_sec <= token_limit` superseded

The "Consequence of (1)" paragraph above (`token_throughput_per_sec <= token_limit`) was itself superseded once `rate_limit_validation` was actually run live and iterated on: that assertion still measured the wrong thing (aggregate throughput staying low, when the scenario's entire point is to *trigger* the limit), and a short `token_window` meant sequential demand often couldn't exceed the budget at all regardless of any assertion. See ADR-024 for the fix (`first_rate_limited_at_tokens`-based assertions, a deliberately long `token_window`) and for a second, previously-undiscovered bug in `_wait_for_subscription_ready()`'s cousin problem — subscriptions that *share a model* race the controller's `TokenRateLimitPolicy` reconciliation independently of the subscription's own `status.phase`.
