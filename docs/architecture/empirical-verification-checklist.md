# Empirical MaaS Verification Checklist

MaaS:PAL has two surfaces that make *claims* about live MaaS cluster state: the
"MaaS Setup" UI (ADR-017, pure read-only CR inspection) and the scenario
runner's assertions (which mix genuine cross-checks with harness-side-only
numbers, see ADR-014/ADR-015). Neither surface is automatically correct just
because it reads a CR or computes a number — a CR's `status.conditions` can
say "Enforced" while the gateway does something else, and an assertion that
only checks the harness's own client-side math can go green even if the
cluster silently ignored the thing under test (this happened for real —
`rate_limit_validation` shipped for a while checking only its own throughput
arithmetic, never Limitador's actual counters).

This doc catalogs every such claim worth empirically verifying — checking
what actually happens at the gateway/CR/metrics level, not just what's
displayed or computed. Status values:

- **Verified** — an existing scenario/task cross-checks this against live
  cluster behavior (not just a mocked unit test).
- **Gap** — no scenario/task checks this at all; it's an assumption baked
  into a doc, a UI panel, or an unexamined success case.
- **Partial** — some coverage exists but doesn't close the actual question
  (e.g. a harness-side-only assertion that never touches the real signal).

## Next Up (prioritized)

Five items picked to implement next, in no particular order; design notes below (also cross-referenced from their rows further down). **Platform config's two rows are explicitly out of scope going forward** — they test RHOAI's `DataScienceCluster`/`OdhDashboardConfig`, not MaaS itself, and both carry cluster-wide blast radius on top of being the wrong layer to test here.

### 1. OpenShift Group membership propagation

**Key unlock, confirmed live** (`maas-domain-reference.md` Catalog D): Authorino's `AuthConfig` supports *two* mutually-exclusive authentication paths — API-key pattern match (the `sk-oai-*` keys MaaS:PAL exclusively uses today) **and OpenShift token review**. That second path means a raw Kubernetes bearer token can authenticate to the gateway directly, with identity resolved via standard TokenReview (the same mechanism that already makes the harness's own SA resolve to `system:authenticated` — see `harness/tasks/subscription.py`'s `_DEFAULT_OWNER_GROUPS` comment). This changes the design from "impossible without a real user login" to a concrete, buildable plan:

1. Create a throwaway **test-identity ServiceAccount** in the `maaspal` namespace (e.g. `maaspal-group-test-identity`) and mint its token via the Kubernetes **TokenRequest API** (`create` on the `serviceaccounts/token` subresource) — no OAuth login, no impersonation needed.
2. Create a throwaway OpenShift **`Group`** (`user.openshift.io/v1`, cluster-scoped), initially *without* the test SA's fully-qualified username (`system:serviceaccount:maaspal:maaspal-group-test-identity`) in `.users`.
3. Set up a `MaaSSubscription` + `MaaSAuthPolicy` pair owned by that group, targeting a test model.
4. Send inference using the test SA's **raw token directly** (the OpenShift-token-review auth path, no API key minted at all) — expect denied.
5. Patch the Group to add the test SA's username to `.users`, retry — expect allowed, within some bound. *This bound is itself the thing under test*, so poll-with-max-wait (reusing the existing `_settle_and_evaluate`-style pattern) rather than assuming a fixed delay either way.
6. Remove from `.users`, retry — expect denied again, within some bound.
7. Cleanup: delete the Group, the test SA, and its associated CRs.

**Open question, now more doubtful than "unconfirmed"**: does `Group.users` accept a ServiceAccount's fully-qualified username string (`system:serviceaccount:<ns>:<name>`), or only real OpenShift user identities? A live `TokenReview` against the harness's own SA token (done while investigating an unrelated `subscription_without_authpolicy` failure, see ADR-018's Update) came back with a **fixed** groups list — `[system:serviceaccounts, system:serviceaccounts:maaspal, system:authenticated]` — with no sign of anything Group-CR-derived. This is consistent with how OpenShift's identity model actually works: `Group` membership resolution is part of the OAuth/identity-provider layer for real `User` objects, and ServiceAccount token authentication is a completely separate path that doesn't consult `Group` CRs at all. If that holds, **minting more ServiceAccounts can never test Group-membership propagation**, no matter how the Group's `.users` list is populated — this specific design (the `Group`-based half of §1) may be fundamentally unbuildable under the harness's SA-only auth model, not just risky/unverified. Confirm directly (patch a test Group with a real SA's fully-qualified username, then TokenReview that same SA and check whether the group actually appears) before writing any more code here — if it fails, real Group-membership-propagation testing would need an actual OpenShift `User`/`Identity`, which this harness has no way to create or authenticate as. This doesn't affect §2/§5 below, which use `owner.users` direct-matching, not `Group` membership.

**New RBAC required, and why it needs the capability-gating below**: `create`/`get`/`patch`/`delete` on `groups.user.openshift.io` (cluster-scoped — creating/mutating an OpenShift Group is a meaningfully more sensitive grant than anything else MaaS:PAL has asked for so far, on par with or beyond the `secrets get` grant ADR-017 already flagged as its most sensitive); `create`/`delete` on `serviceaccounts` and `create` on `serviceaccounts/token` in the `maaspal` namespace (minting arbitrary SA tokens, even scoped to SAs MaaS:PAL itself creates, is also a real elevated capability).

**UI/scenario requirement (yours, not optional)**: rather than this one scenario failing opaquely when the SA lacks these grants, build a **generalizable capability-gating mechanism**: an optional `requires_permissions:` list in scenario YAML (`{group, resource, verb}` tuples), checked via a `SelfSubjectAccessReview` per entry (same idea as `oc auth can-i`, using `AuthorizationV1Api`) — surfaced by `GET /api/scenarios` as `available`/`unavailable_reason` per scenario, same shape as the existing `bool | None` "couldn't tell" convention. `ScenarioList.tsx` grays out the card and disables Run with a tooltip explaining the missing RBAC, mirroring `MaasUnavailableNotice.tsx`'s existing style. Generalizes beyond this one scenario — any future elevated-permission scenario gets the same graceful degradation for free.

### 2. `POST /maas-api/v1/api-keys/search` scoped to caller only

The same TokenRequest trick above unlocks this too, and cheaply reuses infrastructure if item 1 is built first: mint **two** distinct test-identity ServiceAccount tokens (identity A, identity B), each bound to its own subscription/key. Identity A creates a key with a unique `name_prefix`. Identity B calls `/search` (using *its own* token, not identity A's or the harness SA's) with identity A's prefix and asserts the result is empty (or, if the finding is that scoping doesn't actually work, asserts it's non-empty — either way the claim gets a real answer instead of "unknown"). Requires `provision_api_key`/`verify_api_key_search` to accept an optional `token` override — the same pattern `send_requests` already established for `url`/`token` (ADR-012), not a new concept.

### 3. Tenant testing

See the new "Tenant testing" section below — brainstormed candidates, since this wasn't a checklist area before now.

### 4. `ExternalModel`/`ExternalProvider` routing

Blocked on two unresolved things, in order: (a) the RHOAI 3.5+ schema for these has never been confirmed against a live instance (`maas-domain-reference.md` Catalog B — "no `ExternalModel` was deployed on the research cluster even pre-upgrade") — first step is a pure read-only spike, `oc get externalmodels.inference.opendatahub.io -A` / `externalproviders...` on the now-upgraded cluster, to confirm or correct the assumed shape before writing anything; (b) testing real routing needs *something* to route to. Real third-party providers (OpenAI/Bedrock/Gemini) mean external network egress and third-party credentials/billing — a materially different dependency than anything else in this checklist. **Cheaper alternative worth designing around**: point a test `ExternalProvider` at an endpoint *MaaS:PAL itself controls or a cheap public echo endpoint* rather than a real provider, and confirm — by inspecting what actually comes back — that traffic really left through the external path rather than silently hitting an internal route. Would need a new CR-write task (`apply_external_model` or similar) and new write RBAC on `inference.opendatahub.io` (currently read-only).

### 5. Shared vs. per-user pooling of a subscription's rate limit — confirmed passing live (ADR-023, ADR-024)

**New claim, not previously in this doc.** The live `TokenRateLimitPolicy` example (Catalog D) shows `counters: [{expression: auth.identity.userid}]` on each subscription's named limit entry — this *suggests* the configured `tokenRateLimits` limit is tracked independently per user, not pooled across everyone the subscription covers, but that's a hint from the CR's shape, not a confirmed behavior. Nothing before this could create more than one distinct caller identity bound to the same subscription to actually check it — `multi_key_load.yaml` creates N *keys*, but all under the harness's own single SA identity, so it couldn't distinguish "per-key" from "per-user".

**Built**: `harness/tasks/identity.py` (`create_user`/`provision_keys_for_users`) mints N throwaway ServiceAccount identities via TokenRequest and provisions each one's own key, authenticated as that identity — the same subset-of-§1 RBAC this section originally called for (ServiceAccount + `serviceaccounts/token`, no `Group`). `apply_rate_limit_subscription` gained `owner_users_from_shared_state`, resolving this doc's own previously-flagged wrinkle (runtime-only usernames can't be spelled out in YAML) exactly as sketched below.

**One deliberate deviation from the original sketch below**: rather than pooling both identities' keys into one `key_pool: true` call (aggregate error rate only), `scenarios/rate_limit_shared_across_users.yaml` fires two **sequential** bursts — one key per invocation, via new `key_index`/`result_key` params on `send_requests` — so each identity's outcome is independently visible, not just a combined aggregate. This also makes a "user A succeeds, user B is immediately denied" result unambiguous, rather than inferring pooling from a rising aggregate error rate. See ADR-023 for the full design and ADR-024 for the precision fixes (`first_rate_limited_at_tokens`-based assertions, `token_limit: 50`/`token_window: "24h"` defaults) that made the comparison between user A and user B actually trustworthy — still depends on the target cluster having a pre-existing `MaaSAuthPolicy` broadly covering `system:authenticated`.

<details>
<summary>Original brainstormed design (superseded by the above, kept for context)</summary>

**Design**: one `MaaSSubscription` with `spec.owner.users` naming several throwaway identities directly (not a `Group`, and not `system:authenticated` — explicitly enumerating exactly the synthetic identities this scenario creates keeps it from affecting any other caller on the cluster at all, a narrower blast radius than every existing rate-limit scenario). Mint N throwaway ServiceAccount identities (same TokenRequest mechanism as §1, but note this specific test **doesn't need the `Group` RBAC** — only the ServiceAccount-creation and `serviceaccounts/token`-minting grants, a subset of §1's ask). Each identity mints its own key using its own token (`provision_api_key` needs the same `token` override this requires for §1/§2 — three next-up items now converging on the same small extension). Size the subscription's `token_limit` and per-identity request volume so the two hypotheses produce genuinely different, distinguishable outcomes: each identity's own traffic comfortably under the limit alone, but the combined total of all identities' traffic over it — if pooled, expect denials once combined usage crosses the limit; if per-user, expect none, since no single identity ever approaches its own allotment.

**A real architectural wrinkle worth flagging now**: `apply_rate_limit_subscription`'s `owner_users` param today expects a YAML-authored list, but here the list of usernames only exists at *run time* (produced by the identity-minting task) — `${harness.x}` template substitution only fills a scalar into a string, it can't build a YAML list from a runtime array. The fix is **not** a templating extension — it's the same `shared_state`-passing convention every other cross-task data flow in this codebase already uses (ADR-004): the subscription task should read the minted identities directly from `shared_state` when told to (e.g. an `owner_users_from_shared_state: "test_identities"` param) rather than needing the usernames spelled out in YAML.

**Reuses existing machinery once the per-identity keys land in the pool**: if every identity's minted key gets appended to the same `shared_state["api_keys"]` list, `send_requests`'s existing `key_pool: true` distribution works completely unchanged to spread requests across all of them — no new inference-side code needed, only the identity-minting and subscription-owner-sourcing pieces above.

</details>

## Tenant testing (brainstormed, not yet scoped)

Not a checklist area before this round — added per the "Tenant testing" request. `MaasTenantConfig`'s real schema (`maas-domain-reference.md` Catalog E, confirmed live): `spec.gatewayRef`, `spec.apiKeys.maxExpirationDays`, `spec.telemetry.{enabled, metrics.captureOrganization/User/Group/ModelUsage}`, `status.phase/conditions`. Candidate checks, roughly ordered by how buildable they look:

- **Tenant health truthfulness**: does `MaasTenantConfig.status.phase == "Active"`/`Ready` actually correlate with `/maas-api/v1/...` being reachable? Cheapest option here — read-only, same spirit as `check_platform_health` (ADR-022), could likely extend that same task rather than write a new one.
- **`gatewayRef` correctness**: does the tenant's declared `spec.gatewayRef` (`{name: maas-default-gateway, namespace: openshift-ingress}`) actually match the Gateway real traffic flows through? Another read-only cross-check, same low-risk shape as the two above.
- **Telemetry capture flags**: does `spec.telemetry.metrics.captureUser`/`captureGroup`/`captureModelUsage` actually gate whether per-user/group/model-labeled metrics show up (vs. aggregate-only)? Interesting and MaaS-specific (not a DSC/dashboard flag), but needs figuring out *where* such labeled metrics would even surface — worth a read-only live spike (`/api/v1/series` with a user/group label filter) before designing a scenario, same discipline as everything else in this doc.
- **`maxExpirationDays` enforcement**: closes the still-open API-key-lifecycle row above, but needs a cluster where this is actually configured (this one's `spec.apiKeys` is empty) — or accept mutating the tenant's own config for the duration of a test, which is a shared, tenant-wide mutation (lower blast radius than toggling `DataScienceCluster`, since it doesn't turn MaaS off, but still affects every caller's key-minting during the test window, not just MaaS:PAL's own synthetic resources) and deserves the same explicit go/no-go as anything else that touches shared state, not silent inclusion in a scenario.
- **Multi-tenant isolation**: does a subscription/model registered under one tenant stay invisible to a caller only eligible under another? Only testable with more than one real tenant present — this cluster has exactly one (`default-tenant`); `AITenant` (tech-preview multi-tenancy) isn't in use here. Flag as untestable on this cluster rather than force it.
- **Tenant deletion/recreation resilience**: excluded, same reasoning as `DataScienceCluster` toggling — deleting/recreating `MaasTenantConfig` risks tearing down `maas-api` for the whole tenant, not a synthetic resource MaaS:PAL owns.

## Rate-limit enforcement

| Claim | Status | Covered by |
|---|---|---|
| A `MaaSSubscription`'s configured `tokenRateLimits` limit actually causes Limitador to deny requests once exceeded | **Verified** | `scenarios/rate_limit_validation.yaml`'s `maas_denied_by_rate_limit` assertion (baseline-delta on Limitador's `limited_calls`) |
| The auto-generated `TokenRateLimitPolicy`'s `Accepted`/`Enforced` UI status (Rate Limiting tab) matches real enforcement, not just a healthy-looking condition | **Verified** | `check_platform_health` task, asserted in `scenarios/platform_health_check.yaml` (ADR-022) — reads `status.conditions` by label selector, not an assumed generated name |
| When two subscriptions target the same model with different `priority`, the higher-priority one's limit actually wins at the gateway | **Verified** — confirmed live that this is actually an auto-selection question, not a per-request race (see ADR-021) | `scenarios/rate_limit_priority_precedence.yaml`: two temp subscriptions, `provision_api_key`'s `expected_subscription_match_count` (no `subscription` pinned) |
| Multiple distinct users bound to the *same* `MaaSSubscription` each get their own independent token-rate-limit allowance (per the live CR's `counters: [{expression: auth.identity.userid}]`), rather than sharing one pooled limit across all of them | **Mechanism confirmed live** — scenario runs end-to-end and produces a trustworthy result either way, see "Next Up" §5 above and ADR-023/ADR-024. Which of "shared" or "per-user" this cluster actually exhibits is the scenario's own output on a given run, not a fixed fact to record here. | `scenarios/rate_limit_shared_across_users.yaml`: `create_user` mints two ServiceAccount identities, `provision_keys_for_users` gets each its own key on one shared subscription, two sequential `send_requests` bursts (`key_index`/`result_key`) compare outcomes via `first_rate_limit_reaches_budget_user_a`/`_user_b` (ADR-024). |

## Access-control enforcement

| Claim | Status | Covered by |
|---|---|---|
| A group with quota (`MaaSSubscription`) but no matching `MaaSAuthPolicy` is denied at the gateway (`gateway-default-deny` fails closed) | **Fix implemented (ADR-018's third Update), not yet verified live** | Live investigation found gateway access is decided by the single cluster-wide `maas-gateway-auth` Kuadrant `AuthPolicy`'s `require-group-membership` OPA rule, keyed on the caller's own identity against a static per-model policy map — never on `MaaSSubscription.spec.owner`. Binding a subscription's `owner.users` to a minted identity (ADR-018's second Update) narrows quota eligibility but doesn't affect gateway access at all, so it couldn't have worked against the shared simulator model (which already has a blanket `system:authenticated` policy). `scenarios/subscription_without_authpolicy.yaml` now deploys its own throwaway model via `deploy_simulated_model` first, so no `MaaSAuthPolicy` anywhere can ever cover it. See ADR-018. |
| A group with a matching `MaaSAuthPolicy` but no `MaaSSubscription` covering the target model is rejected for lack of quota | Gap — **needs a live-cluster spike first** | See "Open question" below; not designed yet because it's unconfirmed whether a MaaS API key can even be minted without a `subscription` binding |
| The Access Simulator's `reachable` prediction (client-side derivation over `/api/maas/{models,subscriptions,auth-policies}`) matches what a real request with that identity actually gets | Gap — **deliberately deferred**, not attempted this round | `subscription_without_authpolicy` indirectly supports the "no policy → not reachable" half; nothing checks the simulator's UI output itself against a live call. Would need the harness to call MaaS:PAL's own API server over the network (a new harness→API-server dependency, a first for this codebase) — worth its own focused round rather than folding into an unrelated batch of work. |
| OpenShift Group membership changes (adding/removing a user) propagate to gateway access promptly, not after a caching delay | Gap — **next up, design in "Next Up" §1 above** | A concrete design now exists (mint a throwaway ServiceAccount via TokenRequest, use its raw token directly via Authorino's OpenShift-token-review auth path — no impersonation or real user login needed) — needs new, meaningfully elevated RBAC (Group + ServiceAccount-token creation) and a capability-gating mechanism so scenarios needing it degrade gracefully without that RBAC, see §1. |

**Open question for the "policy without quota" case**: `harness/tasks/auth.py`'s `provision_api_key` always passes an explicit `subscription` in the request body when one is configured, but it's never been confirmed whether the MaaS API can mint a key with *no* subscription at all (and if so, what identity/group that key presents at the gateway). Before designing a scenario for this, manually try `provision_api_key` with `subscription` omitted against a group that has a policy but no subscription, and observe what actually happens — don't assume, per the lesson in ADR-009 (three compounding bugs came from trusting an undocumented schema instead of checking live).

## API key lifecycle

All rows here are REST-only — no Kubernetes CR read or write, by design (ADR-019), since this is the section of the checklist that's actually achievable that way. `scenarios/api_key_lifecycle.yaml` is the first MaaS:PAL scenario with zero CR dependency at all.

| Claim | Status | Covered by |
|---|---|---|
| `provision_api_key`'s requested `subscription` binding actually took — the response's `subscription` field equals what was sent, not a silent auto-selected fallback | **Verified** | `harness/tasks/auth.py`'s `key_provision_checks.subscription_echo_match_count`, asserted in `scenarios/rate_limit_validation.yaml` and `scenarios/subscription_without_authpolicy.yaml` (the two scenarios that actually pass `subscription`) |
| The response's `name` field matches what was requested, with no silent truncation/sanitization | **Verified** | `key_provision_checks.name_echo_match_count`, asserted in `scenarios/api_key_lifecycle.yaml` |
| `expiresAt` is present at all on every created key | **Verified** (the CR-free half only) | `key_provision_checks.expires_at_present_count`, asserted in `scenarios/api_key_lifecycle.yaml` |
| `expiresAt`'s actual value is consistent with the tenant's configured `MaasTenantConfig.spec.apiKeys.maxExpirationDays` | Gap (unchanged) | Needs reading `MaasTenantConfig`, a CR — deliberately not pulled into `api_key_lifecycle.yaml` to keep that scenario CR-free. Confirmed live: this cluster's `MaasTenantConfig.spec` is **empty** — nothing is even configured to compare against right now, independent of whatever schema question a CR-based check would also need to resolve. |
| Revocation (`DELETE /maas-api/v1/api-keys/{id}`) takes effect immediately for inference, not after a caching delay somewhere in the auth chain | **Verified** | `scenarios/api_key_lifecycle.yaml`: `revoke_api_keys` then `verify_revoked_key_denied` (a registry alias of `send_requests`) asserts `unauthorized_count > 0` on the very next request |
| `POST /maas-api/v1/api-keys/search` finds the keys this run created, filtered correctly by `name_prefix` | **Verified** (inclusion + filtering only) | `verify_api_key_search` task, asserted in `scenarios/api_key_lifecycle.yaml` |
| `POST /maas-api/v1/api-keys/search` is scoped to the caller's own keys, not all keys cluster-wide under an elevated SA token | Gap — **next up, design in "Next Up" §2 above** | No longer genuinely out of reach: the TokenRequest-minted second identity from §1 unlocks this too — two distinct SA tokens, each searching for the other's keys. Needs `provision_api_key`/`verify_api_key_search` to accept an optional `token` override, same pattern `send_requests` already has for `url`/`token` (ADR-012). |

## Model / subscription CR status truthfulness

| Claim | Status | Covered by |
|---|---|---|
| `MaaSModelRef.status.phase` only reaches `Ready` when both a `MaaSSubscription` and a matching `MaaSAuthPolicy` cover it (the governance-pairing rule the Models tab relies on) | Partial — implied by existing UI logic, never flipped live end-to-end | none — `subscription_without_authpolicy` creates the subscription-only half but doesn't assert on `MaaSModelRef.status.phase` itself |
| For an `ExternalModel`, traffic actually routes through the configured `ExternalProvider` endpoint, not an internal route | Gap — **next up, design in "Next Up" §4 above** | Blocked on a read-only schema-confirmation spike first (no `ExternalModel` existed on this cluster as of last check), then a CR-write task pointed at an endpoint MaaS:PAL controls rather than a real third-party provider, to avoid needing external credentials/billing. |

## Networking

| Claim | Status | Covered by |
|---|---|---|
| A `Gateway` showing `Programmed: true` actually accepts traffic | **Verified** (the CR-truthfulness half) — every scenario that successfully sends inference through the MaaS gateway is already implicit evidence, but nothing had asserted the CR's own condition explicitly until now | `check_platform_health`'s `gateway_status.programmed`, asserted in `scenarios/platform_health_check.yaml` (ADR-022). Confirmed live: there are *two* Gateways in `openshift-ingress` (`maas-default-gateway` and an unrelated `data-science-gateway`) — don't assume a cluster has only one. |
| An `HTTPRoute`'s `ownerReferences`-derived model attribution (Networking tab) matches where traffic for that route actually lands | **Verified** (the ownership-metadata half) | `check_platform_health`'s `http_route_status.owner_ref_matches`, asserted in `scenarios/platform_health_check.yaml` (ADR-022) — found by label selector (`app.kubernetes.io/name=<model>`), not an assumed `<model>-kserve-route` name pattern |

## Platform config

**Out of scope going forward** (not just deferred) — both rows below test RHOAI's `DataScienceCluster`/`OdhDashboardConfig`, the platform layer MaaS happens to run on top of, not MaaS itself. Kept in this doc for completeness/history, not as backlog.

| Claim | Status | Covered by |
|---|---|---|
| Toggling `DataScienceCluster`'s MaaS management state (Managed/Removed) actually gates MaaS API availability | Gap — **out of scope**: wrong layer (RHOAI DSC, not MaaS) *and* cluster-wide blast radius | Confirmed live this is `spec.components.kserve.modelsAsService.managementState` (RHOAI 3.5+ path) on this cluster. Toggling it for real would disable MaaS *cluster-wide* for every caller, not just MaaS:PAL's own test resources. |
| `OdhDashboardConfig` feature flags actually gate the behavior they claim to | Gap — **out of scope**, same reasoning | Same cluster-wide blast radius concern; also, most of its flags (`disableModelCatalog`, `genAiStudio`, ...) gate RHOAI dashboard UI behavior MaaS:PAL has no way to observe from the harness side at all. |

## Metrics scoping

| Claim | Status | Covered by |
|---|---|---|
| MaaS-reported request/token counts (baseline-delta) match what the harness actually sent, within tolerance | **Verified** | `scenarios/metrics_fill.yaml` (ADR-014/ADR-015) |
| Isolation from other concurrent callers on the same model route | **Accepted limitation, not a gap** — no metric in the catalog carries a run-id/caller-id label; isolation is time-window-based only. Documented in ADR-014/ADR-015 and CLAUDE.md's "Scoping caveat" section. Would need a caller-scoped Prometheus label (if a deployed Limitador ever exposes one) to close for real. |

## How to use this doc

When picking up the next item: read the row's "Covered by" column and the linked ADR/scenario/task for context, don't start from scratch. If a row has an "Open question," resolve it against a live cluster before writing any code — this repo has a specific, documented history (ADR-009) of scenarios built against an assumed-but-unverified schema shipping silently broken.

The "Next Up" section's five items were prioritized together because #1, #2, and #5 all share the same new mechanism (a TokenRequest-minted throwaway ServiceAccount identity) — building #1 first makes #2 and #5 nearly free. Note #5 only needs a *subset* of #1's RBAC (ServiceAccount + token creation, not `Group`), so it's buildable even before the `Group` half of #1 is — worth sequencing SA-identity-minting as its own reusable piece rather than bundling it inextricably with the `Group` work. Before writing any code for #1, confirm the one load-bearing assumption flagged there (`Group.users` accepting a ServiceAccount's fully-qualified username) live, and build the `requires_permissions:` capability-gating mechanism alongside it rather than after — it's meant to be generalizable to future elevated-permission scenarios too, not a one-off for this scenario.

**Update**: #5 has now been built this way (`harness/tasks/identity.py`, ADR-023), confirming the sequencing bet above — the ServiceAccount+token half of the mechanism didn't need to wait on #1's `Group` work or its `requires_permissions:` gating. #1's own `Group.users` load-bearing assumption is still unconfirmed and #2 still needs `provision_api_key`/`verify_api_key_search` to grow the `token` override — #5's build doesn't resolve either.
