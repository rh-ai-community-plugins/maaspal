# ADR-018: Access-Control Enforcement Testing via a Second CRD-Writing Task

## Status

Accepted

## Context

The "MaaS Setup" UI (ADR-017) documents and displays a two-layer access model: a `MaaSSubscription` grants a group/user *quota* on a model, and a separate `MaaSAuthPolicy` grants that same group/user *gateway access*. The Access Control tab already flags mismatches (`quota_without_access`, `access_without_quota`), and the Access Simulator predicts whether a candidate identity is `reachable` by checking both. None of this had ever been checked against what the gateway actually does for a real request — it's all read-only CR inspection (see `docs/architecture/empirical-verification-checklist.md`).

Separately, `rate_limit_validation` (the one existing scenario that touches enforcement) only asserted on the harness's own client-side throughput math, never on Limitador's real counters — closed in the same round of work as this ADR by adding a `metrics_queries`/`promql`-form assertion on `limited_calls` (see `scenarios/rate_limit_validation.yaml`), following the exact baseline-delta pattern ADR-014/ADR-015 already established. That part needed no new architecture, just a scenario YAML change plus a small `harness/tasks/inference.py` addition (see below) — it's mentioned here only for completeness, not because it's the interesting decision.

The interesting decision is: how to test the "quota without gateway access fails closed" half of the two-layer model, since nothing in the harness could write a `MaaSAuthPolicy` (or, more precisely, *not* write one while controlling for everything else).

## Decision

**New task**, `harness/tasks/access_policy.py` (`ApplyAuthPolicyTask`, registered as `apply_auth_policy`), that creates/patches/cleans up a `MaaSAuthPolicy` CR — mirroring `harness/tasks/subscription.py` almost exactly (get-then-create-or-patch, `shared_state["original_auth_policy"]`/`_policy_created"` bookkeeping, same cleanup shape). Group/version match `MaaSSubscription` (`maas.opendatahub.io/v1alpha1`, plural `maasauthpolicies`); the write-side schema was inferred from the already-live-confirmed read side (`api/maas_client.py:list_auth_policies`): `spec.subjects.{groups[].name, users[]}`, `spec.modelRefs[].{name, namespace}` — no `tokenRateLimits`, since this CR governs access only, not quota. **Update, confirmed live at deploy time**: the target cluster's own pre-existing `simulator-access` MaaSAuthPolicy (`models-as-a-service` namespace) has exactly this shape (`spec.subjects.groups[].name: system:authenticated`, `spec.modelRefs: [{name: facebook-opt-125m-simulated, namespace: llm}]`) — the write-side schema assumption above is no longer just inferred, it matches a real object on this cluster byte-for-byte in structure.

**RBAC**: extended the existing cluster-scoped `deploy/rbac-maas-subscription-write.yaml` grant to also cover `maasauthpolicies`, rather than adding a new file — the exact same namespace-scoping reasoning from ADR-009's Update section (the CR must live wherever the MaaS controller actually watches, which is install-specific, not the `maaspal` namespace) applies identically to this CRD.

**New scenario**, `scenarios/access_denied_no_policy.yaml`, tests the "quota without access" half:
1. `apply_rate_limit_subscription` grants a synthetic group (`test_group`, default `maaspal-fail-closed-test`) quota on the target model, with a deliberately generous `token_limit` so rate limiting can't be the reason for any denial observed — isolating the variable under test from `rate_limit_validation`'s concern.
2. `provision_api_key`, pinned to that subscription — the created key inherits the subscription's owner group, which is how its identity is presented at the gateway. **This is the load-bearing assumption of the whole design**: that a MaaS API key's gateway-facing identity comes from the subscription it's bound to at mint time, not from the caller's live OpenShift session. It was not independently re-verified beyond what ADR-009 already established (`provision_api_key`'s `subscription` param does pin the key to a specific subscription — confirmed then), but nothing in that prior work confirmed *what group identity the gateway sees* for a key minted this way. **Verify this first thing when this scenario is run live** — if it's wrong, the scenario will simply fail to reproduce the intended failure mode (e.g. it might instead succeed, or fail for an unrelated reason), which the assertions below are designed to at least surface honestly rather than silently.
3. `send_requests` fires against the target model. New counters in `harness/tasks/inference.py`'s `inference_results` — `rate_limited_count` (429s) and `unauthorized_count` (401/403s), caught via `openai.APIStatusError.status_code` instead of the previous bare `except Exception` — let the assertions distinguish *why* requests failed instead of only whether they did:
   ```yaml
   assertions:
     error_rate_pct: {promql: "...", expect: "> 90"}
     rate_limited_count: {promql: "...", expect: "== 0"}
     unauthorized_count: {promql: "...", expect: "> 0"}
   ```
   These are harness-side-only numbers routed through the `promql` pass-through form (ADR-015 convention, consistent with the other five scenarios) — there's no independent MaaS-side metric that specifically distinguishes an auth denial from any other gateway rejection, so this trio of harness counters is what actually isolates the failure mode.

**Deliberately not designed**: the inverse case (a `MaaSAuthPolicy` exists for a group, but no `MaaSSubscription` covers it — should be rejected for lack of quota even though gateway auth passes). Whether a key can even be minted with no subscription binding, and if so what identity it presents, is unconfirmed — building a scenario on an assumption here would repeat exactly the mistake ADR-009 documents. Left as an open item in `docs/architecture/empirical-verification-checklist.md`, with an explicit recommendation to spike it manually against a live cluster first.

## Consequences

**Positive:**
- Closes the two highest-value gaps identified in a full empirical-verification audit (rate-limit enforcement, access-control fail-closed) using patterns already established in this codebase (CRD-write task shape from ADR-009, baseline-delta/promql-passthrough assertions from ADR-014/ADR-015) — no new architecture, just two more instances of existing patterns.
- `status_code_counts`-style breakdown (`rate_limited_count`/`unauthorized_count`) is additive to `inference_results` and immediately useful beyond this one scenario — any future scenario needing to distinguish denial reasons can use it.
- `deploy/rbac-maas-subscription-write.yaml` sharing one grant for both CRDs avoids RBAC-file sprawl for what is, mechanistically, the same class of write.

**Negative:**
- The load-bearing assumption above (key identity = subscription's owner group) is unverified beyond what ADR-009 already covers for a different question (pinning, not identity-at-the-gateway). If wrong, `access_denied_no_policy` needs rework, not just a config tweak.
- `rate_limited_count`/`unauthorized_count` rely on the OpenAI Python SDK correctly surfacing HTTP status codes via `APIStatusError` for every kind of gateway rejection this cluster can produce; an unusual rejection shape (e.g. a non-JSON error body, or a status code outside 401/403/429) falls into the generic `fail` bucket with no further breakdown — acceptable for now, but a scenario relying on a status code this doesn't distinguish would need the breakdown widened first.
- Like `rate_limit_validation`'s `maas_denied_by_rate_limit` assertion, `access_denied_no_policy` has not yet been run against a live cluster — both are new as of this ADR and unit-tested only (mocked `CustomObjectsApi`/`openai.APIStatusError`). Verify end-to-end before relying on either scenario's pass/fail result, per this repo's established practice of saying so explicitly rather than implying more confidence than the testing supports.

**Neutral:**
- `docs/architecture/empirical-verification-checklist.md` is a new living doc (same spirit as `maas-domain-reference.md`) cataloging every other empirical-verification gap found during this work, so future sessions have a backlog instead of re-deriving the list.

## Update: the load-bearing assumption was wrong — `subscription:` doesn't override caller eligibility

Run live for the first time, `access_denied_no_policy` fails at `provision_api_key` itself: `400 {"code":"invalid_subscription","error":"Unable to resolve a subscription for this API key"}` — it never reaches the actual fail-closed check. Confirmed via a direct `TokenReview` against the harness SA's own token:

```
username: system:serviceaccount:maaspal:maaspal
groups: [system:serviceaccounts, system:serviceaccounts:maaspal, system:authenticated]
```

`maaspal-fail-closed-test` (the scenario's synthetic `owner_groups` value) appears nowhere. This confirms the "load-bearing assumption" flagged above was wrong: `provision_api_key`'s `subscription` param does **not** force-bind a key to an arbitrary named subscription regardless of the caller's real identity — it only *disambiguates among subscriptions the caller is already eligible for*. Naming a subscription whose owner the caller doesn't actually belong to is correctly rejected, not honored. In hindsight this is the only sane behavior for an access-control system — the surprising part is only that this ADR assumed otherwise without checking, exactly the ADR-009 pattern repeating a third time in this codebase's history now (see ADR-009's own three Updates).

**A second, independent problem, uncovered while investigating the first**: even a group the caller *does* belong to doesn't work here. `system:authenticated` already has a real `MaaSAuthPolicy` (`simulator-access`) covering this exact model — so pinning to a `system:authenticated`-owned subscription would make the request *succeed*, not fail closed, defeating the scenario's premise before it even gets to the auth-denial question.

**Path to an actual fix, not yet built**: both problems are solved by the same mechanism already designed for `empirical-verification-checklist.md`'s "Next Up" §2/§5 — mint a throwaway ServiceAccount via the Kubernetes TokenRequest API, and bind the subscription's `spec.owner.users` directly to that SA's fully-qualified username (`system:serviceaccount:maaspal:<name>`), not a group. This is a direct username match, not the (also now-suspect, see the checklist's §1 note) Group-membership-based path — the minted identity is guaranteed to be quota'd (its own dedicated subscription) and guaranteed to have no matching auth policy (nothing else on the cluster could possibly reference a name that only this run generates). Needs `provision_api_key` to accept a `token` override so the key gets minted *as* that identity, not the harness's own SA — the same requirement already identified for checklist items #2 and #5, now with a fourth, more urgent reason to build it: this scenario cannot work at all without it, not just "could be improved by" it.

**Until that's built, this scenario cannot pass as designed** — the checklist's "Access-control enforcement" row for this claim has been reverted from Verified back to Gap.

## Update: fixed via minted-identity binding

The mechanism the previous Update called for now exists — `create_user`/`provision_keys_for_users` (ADR-023), built after this ADR was originally written, mint a throwaway `ServiceAccount` and can bind a subscription's `spec.owner.users` directly to its fully-qualified username. `scenarios/access_denied_no_policy.yaml` has been redesigned to use it: `create_user` → `apply_rate_limit_subscription` (`owner_groups: []`, `owner_users_from_shared_state: "users"`) → `provision_keys_for_users` → `send_requests`, replacing the old `provision_api_key` step pinned to a group. The minted identity is guaranteed eligible for its own dedicated subscription (so `provision_api_key`'s `400 invalid_subscription` failure mode from the first Update can't recur) and guaranteed to have no matching `MaaSAuthPolicy` (nothing else on the cluster references a name this run alone generates), closing both problems the first Update identified. **Turned out to be necessary but not sufficient — see the third Update below.**

**Also renamed**: `scenarios/access_denied_no_policy.yaml` → `scenarios/subscription_without_authpolicy.yaml`, to name what the scenario actually creates and omits rather than the outcome it's testing for (every other reference in this ADR to the old filename predates the rename).

## Update: the real enforcement mechanism is per-caller-identity, not per-subscription — found live

Run live after the second Update's fix, the scenario *still* doesn't deny: `subscription_echo_match_count` passes (the key correctly binds to the new subscription) but `error_rate_pct`/`unauthorized_count` show the request succeeding. Reading the cluster's actual enforcement object (`oc get authpolicy maas-gateway-auth -n openshift-ingress -o yaml` — the single Kuadrant `AuthPolicy` bound to the whole `maas-default-gateway`) explains why: its `require-group-membership` OPA rule checks the caller against a static `model_access` map compiled from **all `MaaSAuthPolicy` CRs cluster-wide**:
```
model_access := {"llm/facebook-opt-125m-simulated": {"users": null, "groups": ["system:authenticated"]}, ...}
allow { model_rules.users[_] == username }
allow { g := groups[_]; model_rules.groups[_] == g }
```
`username`/`groups` here come from `auth.metadata.apiKeyValidation.{username,groups}` — the API key's own bound caller identity, captured from the real K8s identity that minted it. **`MaaSSubscription.spec.owner` is never referenced by this rule at all** — it only feeds a separate `subscription-valid` rule used for quota selection. So gateway access is decided purely per-caller-identity against a static per-model policy list, completely independent of which subscription the caller's key happens to reference. This is a more precise (and now confirmed, not hypothesized) version of the "second, independent problem" the first Update flagged: it isn't that `system:authenticated` merely *happens* to already have access — it's that access is *never* subscription-scoped to begin with, so binding `owner.users` to a minted identity (the second Update's fix) narrows quota eligibility but does nothing to gateway access.

The cluster's only model (`llm/facebook-opt-125m-simulated`) has exactly one `MaaSAuthPolicy` (`simulator-access`), granting blanket `system:authenticated` access — and every OpenShift-issued token, including any `ServiceAccount` the harness mints, carries `system:authenticated`. So no identity the harness can construct will ever be denied on that model, no matter how the subscription is configured.

**Actual fix**: give the scenario its own throwaway model that no `MaaSAuthPolicy` will ever cover, using `deploy_simulated_model` (`harness/tasks/model.py`, `DeploySimulatedModelTask`) — already built for `multi_model_full_load.yaml`/`multi_model_subscription_spread.yaml`, and already the right shape (creates a uniquely run-id-named `LLMInferenceService`+`MaaSModelRef`, cleans both up after). `scenarios/subscription_without_authpolicy.yaml` now runs `deploy_simulated_model` first; `apply_rate_limit_subscription` and `send_requests` gained a `model_from_shared_state` param (reading `shared_state["deployed_models"]`, the same pattern `owner_users_from_shared_state` already established) so they can target the dynamically-named model without a static YAML value. Since nothing else on the cluster ever references a model name this run alone generates, `model_access` will never have an entry for it — `require-group-membership`'s `allow` rules both fail, and the request is denied by construction, not by hoping no policy happens to match. Not yet re-verified against a live cluster — do that before trusting a PASS here over what this Update documents.
