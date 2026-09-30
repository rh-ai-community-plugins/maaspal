# MaaS Domain Model Reference

Catalog of the MaaS *governance/setup* objects — subscriptions, models, access control, tenant config, and the Kuadrant/Limitador enforcement layer beneath them — as distinct from `maas-metrics-reference.md`'s catalog of what those objects *emit*. Backs the "MaaS Setup" overview in the UI (see ADR-017) and is meant as the reference to check when designing a new scenario against a given cluster.

Findings from live-cluster research (`oc get`/`-o yaml` as `kube:admin` against `cluster-rkmhx.rkmhx.sandbox1230.opentlc.com`, the cluster `deploy/configmap-global.yaml` targets) plus the following upstream sources:
- Red Hat docs (RHOAI 3.5): [Govern LLM access with Models-as-a-Service](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/govern_llm_access_with_models-as-a-service/deploy-and-manage-models-as-a-service)
- Upstream docs: [opendatahub-io.github.io/models-as-a-service](https://opendatahub-io.github.io/models-as-a-service/latest/)
- Upstream repo (CRDs/controller source of truth): [github.com/opendatahub-io/models-as-a-service](https://github.com/opendatahub-io/models-as-a-service)
- Community install/ops guide: [rh-aiservices-bu.github.io/rhoai-maas-guide](https://rh-aiservices-bu.github.io/rhoai-maas-guide/modules/main/index.html) — not an official Red Hat doc, but the source of the version-split `ExternalModel` schema, the credential-secret labeling requirement, and the `MaaSModelRef` governance-pairing rule below; cross-checked against this cluster where possible.

**Fixed, not just found:** `harness/tasks/subscription.py` used to write `MaaSSubscription.spec.rpsLimit`, a field that doesn't exist on the real CRD (`spec.modelRefs[].tokenRateLimits[]`/`spec.owner`/`spec.priority` is the actual shape). Turned out to be three compounding bugs, not one — see ADR-009's "Update" section for the full account (wrong schema, wrong namespace so the controller never reconciled the CR at all, and no guarantee the harness's own provisioned keys resolved to the subscription under test).

**Research cluster was on the RHOAI 3.4-era MaaS implementation, now upgrading to 3.5.** Originally confirmed via three independent signals: `ExternalModel`/`MaaSModelRef`/`MaaSAuthPolicy`/`MaaSSubscription` all lived under `maas.opendatahub.io/v1alpha1` (the 3.4-era API group), and `DataScienceCluster` used the deprecated `spec.components.kserve.modelsAsService.managementState` field. `api/maas_client.py` now targets 3.5's `ExternalModel`/`ExternalProvider` split under `inference.opendatahub.io/v1alpha1` (written from the community guide's documented schema, not yet re-verified live against the upgraded cluster) and `list_platform()` checks both DataScienceCluster field paths (3.4 and 3.5+) rather than assuming one, since which one a given cluster uses can change under you, as it just did here.

**Implementation status in the UI** (each section below is marked; see ADR-017 for the backend/frontend design): **A (Subscriptions)**, **B (Models)**, **C (Access Control, plus its own "Authorization Policies" tab listing every `MaaSAuthPolicy` CR directly)**, **D (Rate-Limiting Enforcement)**, **E (Tenant/Platform Configuration)**, **F (Gateway/Networking)**, and **I (Access Resolution, as the "Access Simulator" tab)** are live in the "MaaS Setup" tab. **G and H are catalog-only** — read here to understand the domain, but not yet surfaced as their own UI tabs.

## A. Subscriptions — `MaaSSubscription` (`maas.opendatahub.io/v1alpha1`, namespaced) — ✅ in UI

Grants a set of groups/users **quota** (token rate limits) for a set of models. Does **not** by itself grant gateway access — see Access Control below.

| Field | Meaning |
|---|---|
| `metadata.name`/`.namespace` | Identity — namespace is the MaaS install's subscription namespace (`models-as-a-service` on the research cluster, not fixed across installs) |
| `metadata.annotations["openshift.io/display-name"]` / `["openshift.io/description"]` | Human-readable label shown in dashboards |
| `spec.priority` | Resolution order — when a user's groups map to multiple subscriptions for the same model, highest priority wins. Upstream's recommended scheme: prod=100, staging=50, dev=0 (default), sandbox=-10, using gaps of ~10 rather than 0/1000/10000 |
| `spec.owner.groups[].name` / `spec.owner.users[]` | Who this subscription is granted to |
| `spec.modelRefs[]` | Per model: `name`, `namespace`, `tokenRateLimits[]` (`limit`, `window`, e.g. `100`/`"1m"`) |
| `status.phase` | e.g. `Active` |
| `status.conditions[]` | `Ready`; **`SpecPriorityDuplicate`** — `True` means this subscription's priority collides with another one targeting the same model, a real misconfiguration to surface prominently, not just log |
| `status.modelRefStatuses[]` / `status.tokenRateLimitStatuses[]` | Per-model reconciliation health and the generated `TokenRateLimitPolicy` name |

Live example (research cluster, two subscriptions on the same model, deliberately different priorities):
```yaml
spec:
  modelRefs:
  - name: facebook-opt-125m-simulated
    namespace: llm
    tokenRateLimits:
    - {limit: 100, window: 1m}
  owner:
    groups: [{name: system:authenticated}]
    users: []
  priority: 10          # "simulator-free"; "simulator-premium" has priority 20, limit 100000
```

Cross-check: `GET /v1/models` (MaaS REST) returns a `subscriptions[]` array per model (`name`, `displayName`, `description`) — a thinner reverse-index of the same relationship, useful to confirm the REST view matches the CRD view.

**A `MaaSSubscription` never creates the `MaaSModelRef` it references** — that half was correctly verified: `spec.modelRefs[]` is purely a reference by name/namespace, and a `MaaSModelRef` is created separately, by publishing a model (see the governance-pairing rule above).

**Correction: the RHOAI Dashboard *does* auto-create a matching `MaaSAuthPolicy` when a subscription is created through it** — an earlier version of this doc claimed otherwise, verified only against MaaS:PAL's own harness code, not the Dashboard itself. Confirmed live (`cluster-rkmhx.rkmhx.sandbox1230.opentlc.com`): creating a subscription named `test` (owner group `rhods-admins`, model `llm/facebook-opt-125m-simulated`) through the Dashboard produced a second `MaaSAuthPolicy`, `test-policy`, with identical `spec.subjects.groups`/`spec.modelRefs` and an auto-generated `metadata.annotations["openshift.io/description"] = 'Auth policy created for subscription "test"'`. Neither object carries an `ownerReferences` or `labels` entry pointing at the other — the pairing is a **one-time convenience performed by the Dashboard's own subscription-creation flow**, not a Kubernetes-native owner/controller relationship enforced by the CRD or its controller. Editing one afterwards does not sync the other.

This means direct K8s API creation of a `MaaSSubscription` — including MaaS:PAL's own `apply_rate_limit_subscription` task (`harness/tasks/subscription.py`) — does **not** get a matching `MaaSAuthPolicy` for free; only the Dashboard's UI flow does that. That's exactly why `harness/tasks/access_policy.py` (`ApplyAuthPolicyTask`, ADR-018) exists as its own task, and why no scenario invokes both together (`subscription_without_authpolicy` deliberately creates a subscription *without* an auth policy, to test that failure mode). Because a `MaaSAuthPolicy` is consequently a first-class, independently-created object — not merely an artifact of some subscription — it's now surfaced as its own **Authorization Policies** tab (between Models and Access Control) rather than only being cross-referenced from within Subscriptions/Access Control, the same CR-fidelity principle every other tab in this feature already follows.

Because neither object creates the other, `list_subscriptions()` resolves and displays, per referenced model: whether the `MaaSModelRef` actually exists (`model_exists`) and is ready (`model_ready`), and whether a `MaaSAuthPolicy` matching this subscription's own owners covers that model (`has_auth_policy`) — so a dangling model reference or a missing auth policy is visible directly on the subscription that would otherwise silently have no effect, without cross-referencing the Models/Access Control/Authorization Policies tabs by hand. `list_auth_policies()` now does the same in reverse (resolving each of its own `model_refs[]` against the live `MaaSModelRef` catalog), for the same reason.

## B. Models — ✅ in UI

Three sources, merged (see `api/maas_client.py:list_models`):

1. **`MaaSModelRef`** (`maas.opendatahub.io/v1alpha1`, lives in the model's own namespace, e.g. `llm`): `metadata.annotations` (display name/description), `spec.modelRef.{kind,name}` (`LLMInferenceService` today; external providers are a separate `ExternalModel` object, not a `modelRef.kind` value), `status.phase`, `status.endpoint`, `status.httpRouteName/Namespace`, `status.httpRouteGatewayName/Namespace`.
2. **`GET /v1/models`** (MaaS REST, works with an SA token or an API key): `id`, `url`, `ready`, `owned_by` (`"<namespace>/<MaaSModelRef name>"` — the join key back to (1)), `modelDetails.{displayName,description}`, `subscriptions[]`.
3. **`LLMInferenceService`** (`serving.kserve.io/v1alpha2`, only when `modelRef.kind == LLMInferenceService`): `spec.replicas`, container `resources.{requests,limits}` (cpu/mem/GPU), `spec.router.gateway.refs`, `status.conditions[]` (`GatewaysReady`, `HTTPRoutesReady`, `MainWorkloadReady`, `RouterReady`, `WorkloadsReady`, `PresetsCombined`), `status.url`/`status.addresses[]` (internal + external).

`ExternalModel` maps a client-facing model name to an external provider (OpenAI/Bedrock/Gemini-style), with request-translation-vs-passthrough mode. **Schema is RHOAI-version-dependent** (per the community guide):
- **3.4** (`maas.opendatahub.io/v1alpha1`, single CRD): `spec.{provider, targetModel, endpoint, credentialRef}`. **No longer read by `api/maas_client.py`** — the cluster this repo targets is upgrading to 3.5, so support for the 3.4 shape was deliberately dropped rather than maintained as a fallback; a 3.4 cluster's external models won't appear in the Models tab.
- **3.5+** (`inference.opendatahub.io/v1alpha1`, split in two — what `api/maas_client.py` now queries): `ExternalProvider` (`spec.{provider, endpoint, auth.secretRef}`) referenced by `ExternalModel` (`spec.{modelName, externalProviderRefs[].{ref, targetModel, apiFormat, path}}`). `ref` carries no namespace of its own in the guide's examples, so provider resolution assumes same-namespace as the `ExternalModel`. **Written from the documented schema, not yet verified against a live instance** — no `ExternalModel` was deployed on the research cluster even pre-upgrade.

Governance pairing rule (community guide, worth restating precisely): *"a `MaaSModelRef` transitions to Ready only when both a `MaaSSubscription` AND a `MaaSAuthPolicy` reference it"* — updating one does not auto-sync the other. `list_models()` surfaces the auth-policy half of this directly (`has_auth_policy`, cross-referencing live `MaaSAuthPolicy.spec.modelRefs[]`); the subscription half is already visible via `subscriptions[]`.

**Credential `Secret` for `ExternalProvider` auth — ✅ in UI.** Per the community guide, the Secret holding the external provider's API key must carry its own label for the gateway's wasm-shim to find and inject it: `inference.llm-d.ai/ipp-managed=true` (3.5+; the only version checked — see above). `list_models()` resolves each `ExternalModel`'s provider(s), reads the referenced Secret via `_secret_has_label()`, and surfaces `credential_secret_label_ok: bool | None` per provider — `get` only on the one named Secret the provider references, never `list`/enumerate, and never the Secret's `data`/`stringData`. Not yet verified live (no `ExternalProvider`/Secret pair exists on the research cluster to check against).

Each shaped model also carries a `hosting: "internal" | "external"` field, computed once in `api/maas_client.py` (`"internal"` iff `modelRef.kind == LLMInferenceService`) rather than left for the UI to re-derive from `kind` — so "is this actually served by OpenShift AI, or routed out to a third party?" has one answer, not one per caller.

**Namespace `maas.opendatahub.io/gateway-access` label** — confirmed live and in the community guide: *"Without it, the Gateway will not accept HTTPRoutes from this namespace and the model will not be reachable."* Required on any namespace hosting a model, internal or external. Confirmed present on `llm` and `redhat-ods-applications`, absent (correctly — it doesn't host a model) on `models-as-a-service`. `list_models()` reads this per model's namespace (`gateway_access_label: bool | None`, cached per-namespace within one call; `None` means the namespace couldn't be read, not that the label is confirmed missing) and the Models tab surfaces it as a warning when `false`.

## C. Access Control — ✅ in UI

**Two layers, both required** (per upstream docs, confirmed): a `MaaSSubscription` grants **quota**; a `MaaSAuthPolicy` grants **gateway access**. A subscription with no matching auth policy means a user has quota but can't reach the gateway at all; an auth policy with no matching subscription means a user can reach the gateway but is rate-limited to zero by the cluster's own deny-by-default `TokenRateLimitPolicy` (see D below) — both are real, surfaceable misconfigurations, not just two objects to list side by side.

**`MaaSAuthPolicy`** (`maas.opendatahub.io/v1alpha1`, namespaced): `spec.modelRefs[]`, `spec.subjects.{groups[],users[]}`, `status.authPolicies[]` (per-model `ready`/`reason`), `status.phase`.

**OpenShift `Group`** (`user.openshift.io/v1`, cluster-scoped): `metadata.name`, `users[]` — resolves a subscription's/auth-policy's group references down to actual usernames. MaaS "assigns users to subscriptions based on OpenShift group membership"; a user in multiple groups gets the highest-priority subscription among them (upstream docs). Research cluster has one group (`rhods-admins`, empty membership) — the feature must not assume any groups exist.

## D. Rate-Limiting Enforcement (the layer beneath the CRDs above) — ✅ in UI

What the `MaaSSubscription`/`MaaSAuthPolicy` intent actually compiles down to. Useful when a scenario's rate-limit assertion behaves unexpectedly and the CRD-level view alone doesn't explain why.

**`TokenRateLimitPolicy`** (`kuadrant.io/v1alpha1`) — one auto-generated per model (targeting its `HTTPRoute`) plus one cluster-wide default on the `Gateway` itself:
```yaml
# per-model, one `limits` entry per subscription targeting this model
spec:
  limits:
    models-as-a-service-simulator-free-facebook-opt-125m-simulated-tokens:
      counters: [{expression: auth.identity.userid}]
      rates: [{limit: 100, window: 1m}]
      when: [{predicate: 'auth.identity.selected_subscription_key == "models-as-a-service/simulator-free@llm/facebook-opt-125m-simulated" && !request.path.endsWith("/v1/models")'}]
  targetRef: {group: gateway.networking.k8s.io, kind: HTTPRoute, name: facebook-opt-125m-simulated-kserve-route}
status:
  conditions: [{type: Accepted, status: "True"}, {type: Enforced, status: "True"}]
```
```yaml
# cluster-wide default-deny on the Gateway itself — confirmed live, name "gateway-default-deny"
spec:
  defaults:
    limits:
      deny-all-by-default:
        rates: [{limit: 0, window: 1m}]
        when: [{predicate: '!request.path.startsWith("/maas-api") && !request.path.startsWith("/v1/models")'}]
    strategy: atomic
  targetRef: {group: gateway.networking.k8s.io, kind: Gateway, name: maas-default-gateway}
```
This is *why* "auth policy without a subscription" fails closed rather than open: anything not matched by a subscription-scoped policy falls through to this 0-req/min default.

**`Limitador`** (`limitador.kuadrant.io/v1alpha1`, cluster-singleton, in `kuadrant-system`): `spec.limits[]` (`name`, `max_value`, `seconds`, `namespace`, `conditions`, `variables`) — the literal counter definitions Envoy/Limitador enforce; `status.service.{host,ports}`.

**Authorino `AuthConfig`** (`authorino.kuadrant.io/v1beta3`, in `kuadrant-system`) is the object `MaaSAuthPolicy` compiles down to — the direct analogue of `TokenRateLimitPolicy` on the authorization side rather than the quota side. Confirmed live: `spec.authentication` (API-key pattern match + OpenShift token review, mutually exclusive by `when` predicate), `spec.metadata.apiKeyValidation` (calls `maas-api`'s internal validate endpoint), `spec.response.success.headers` (injects `X-MaaS-Group`/`X-MaaS-Subscription` for downstream use). **Deliberately not surfaced in the UI**: names are content-addressed hashes with no human-readable link back to a `MaaSAuthPolicy`, and every `AuthConfig` in `kuadrant-system` (MaaS-relevant or not) looks equally opaque — listing them would be noise, not signal. See `list_rate_limit_policies()` in ADR-017 for this reasoning in code.

## E. Tenant / Platform Configuration — ✅ in UI

- **`Tenant`** (deprecated) / **`MaasTenantConfig`** (current): `spec.gatewayRef`, `spec.apiKeys.maxExpirationDays`, `spec.telemetry.{enabled, metrics.captureOrganization/User/Group/ModelUsage}`, `status.phase/conditions`. Live example: `default-tenant` in `models-as-a-service`, `gatewayRef: {name: maas-default-gateway, namespace: openshift-ingress}`, phase `Active`.
- **`AITenant`** (tech preview multi-tenancy): dedicated tenant namespace, gateway, OIDC config — not in use on the research cluster.
- **`DataScienceCluster`** (`datasciencecluster.opendatahub.io/v2`, cluster-scoped, name `default-dsc`): confirms MaaS itself is enabled, but **the field path is RHOAI-version-dependent** — 3.5+ uses `spec.components.aigateway.modelsAsAService.managementState`; 3.4 (confirmed live on this cluster) uses the deprecated `spec.components.kserve.modelsAsService.managementState`. `list_platform()` checks both rather than assuming one.
- **`OdhDashboardConfig`** (`opendatahub.io/v1alpha`, name `odh-dashboard-config`, in `redhat-ods-applications`): `spec.dashboardConfig.{modelAsService, vLLMDeploymentOnMaaS, genAiStudio, externalModels, observabilityDashboard}` — adjacent RHOAI dashboard feature flags. All `true` on the research cluster.

## F. Gateway / Networking — ✅ in UI

- **`Gateway`** `maas-default-gateway` (`gateway.networking.k8s.io`, in `openshift-ingress`): LB address, class, `Programmed` condition.
- **`HTTPRoute`** per model: name (`<model>-kserve-route` pattern), namespace, parent gateway. `metadata.ownerReferences` points back to the owning `LLMInferenceService` (confirmed live) — `list_http_routes()` uses this to trace a route to its model directly, rather than guessing from the route's name.

## G. Live Usage

Not part of the setup/config view above, but the natural next thing an admin wants once they can see the setup — see `docs/architecture/maas-metrics-reference.md` for the full metrics catalog (Limitador `authorized_calls`/`authorized_hits`, maas-api control-plane counters, vLLM per-model metrics, Grafana dashboards).

## H. API Keys

`POST /maas-api/v1/api-keys/search` — **unverified**: whether this is scoped to the calling identity's own keys only, or returns every key cluster-wide when called with an elevated SA token, was not confirmed against a live multi-user cluster during this research. Any admin-facing "all API keys" view must verify this live and label its actual scope rather than assume "all keys."

## I. Access Resolution (derived, not raw data) — ✅ in UI ("Access Simulator" tab)

Given a candidate user/group set, which subscription wins by priority, and which models are actually reachable (subscription AND matching auth policy) — synthesized from A–C above rather than read directly from any one object. `AccessSimulatorTab.tsx` computes this entirely client-side from `getMaasModels()`/`getMaasSubscriptions()`/`getMaasAuthPolicies()` (no new backend endpoint needed — `api/maas_client.py:list_auth_policies()` was extracted as a reusable flat list specifically so this and `list_access()` could both use it). The candidate set can mix group names and usernames — each is checked against a subscription's/policy's `owner.groups` OR `owner.users` (either can grant access directly, not just group membership); the highest-`priority` matching subscription wins the quota question, and the model is "reachable" only when a matching `MaaSAuthPolicy` also exists for the same candidate set — the same two-layer requirement Catalog item C describes. The input itself is a typeahead-multi chip select, suggesting every group/user already referenced by some subscription's or auth policy's `owner` as you type (deliberately not every OpenShift `Group` on the cluster — an unreferenced one couldn't change any result), while still accepting free-typed names for testing hypothetical groups/users that don't exist yet. Not yet promoted to a harness-composable task/assertion (the "may become one later" idea from the original design) — still UI-only.
