# ADR-024: Dynamic Model Routing and Rate-Limit Measurement Precision

## Status

Accepted

## Context

Two unrelated-looking scenario failures turned out to share one root cause, and a separate pair of rate-limit scenarios turned out to need the same fix for a different reason.

**`multi_model_full_load`** (deploys N models via `deploy_simulated_model`, spreads M subscriptions across them, fires inference through a key pool) failed every request with `404`. **`subscription_without_authpolicy`** (ADR-018) had already been redesigned once to deploy its own throwaway model via the same task, but still let requests through when they should have been denied, or — after binding the subscription to a minted identity (ADR-018's second Update) — still couldn't demonstrate denial at all. Both scenarios depend on a model that `deploy_simulated_model` just created *this run*.

Live investigation (`oc logs` on the `maas-controller` pod, plus direct `curl` reproduction of the actual gateway behavior) found: a model created this run is **not listed in `GET /v1/models`** no matter how long you wait after its `MaaSModelRef` reports `RuntimeReady` — full governance pairing (a `MaaSSubscription` *and* a `MaaSAuthPolicy`) is required before it appears in discovery. `send_requests`'s existing model-discovery path (`_discover_model`, matching `model:` against `/v1/models`' `id`/`displayName`/`owned_by`) therefore can never resolve such a model, and silently falls back to "first available" — a *different*, already-registered model that the request's actual key isn't entitled to, hence `404`/wrong-model behavior rather than a meaningful pass or fail.

Separately, `rate_limit_validation` and `rate_limit_shared_across_users` kept producing non-deterministic results across several rounds of tuning (`concurrency`, `token_limit` combinations) before the real constraint was understood: at low concurrency, sequential round-trip latency through the gateway's auth chain caps achievable demand at roughly 30-40 tokens/sec, so a tight per-second rate-limit window can simply never be exceeded regardless of configuration; and separately, the `openai` SDK's own default retry behavior (`max_retries=2`, retrying 429s with backoff) can silently retry into a *fresh* window and succeed, hiding a real denial from the harness entirely even though Limitador's own counter shows it happened.

## Decision

### 1. Route to a freshly-deployed model via its own dedicated path, not generic discovery

Confirmed live end-to-end via direct `curl` (mint a throwaway model + subscription + key by hand, no `/v1/models` involvement): a `deploy_simulated_model`-created model's own per-model HTTPRoute — auto-created by the LLMInferenceService controller, reachable the moment `RuntimeReady` is true — works immediately at:
```
{MAAS_API_URL}/{model_namespace}/{model_name}/v1/chat/completions
```
with the model's **bare** name (not `"<namespace>/<name>"`) in the request body's `model:` field. This path still goes through the same gateway `AuthPolicy` as the generic route (a subscription with no matching policy still correctly denies; one with a policy still succeeds) — it just never depends on `/v1/models` discovery at all.

`harness/tasks/inference.py:SendRequestsTask` now takes this path whenever it knows a specific dynamically-created model to target, via two existing extension points:
- `model_from_shared_state` (single target, e.g. `subscription_without_authpolicy`): sets both `params["model"]` (bare name) and `params["url"]` to the dedicated path.
- Per-key `target_model` on `shared_state["api_keys"]` entries (set by `provision_keys_distributed`, used by `multi_model_full_load`'s key pool): each key gets its own dedicated-path client, since a key pool spanning multiple dynamically-deployed models needs a different URL per key, not one shared URL.

`harness/tasks/auth.py:ProvisionKeysDistributedTask`'s `target_model` field changed from the bare `MaaSModelRef` name to `"<namespace>/<name>"` so `send_requests` has both pieces needed to build the per-model URL.

A third case — a key pool with no explicit model and no per-key `target_model` (an auto-selected subscription against an auto-discovered model, e.g. `single_key_load`/`multi_key_load` with both left blank) — got a related but separate fix: `_resolve_models_by_subscription` cross-references each key's own bound `subscription` against `/v1/models`' per-model `subscriptions: [{name}]` list, so an auto-selected subscription and an independently-discovered model can't end up mismatched (this is the general form of the same "two unrelated choices with no guaranteed compatibility" problem, for the case where the model *is* already registered).

### 2. A freshly-deployed model has no gateway access at all until a matching `MaaSAuthPolicy` exists

Separate from routing: `multi_model_full_load`'s models, once reachable, were then denied access outright (`403`) — `provision_subscriptions_distributed` grants quota but, like `apply_rate_limit_subscription`, never creates a `MaaSAuthPolicy`. `harness/tasks/access_policy.py:ApplyAuthPolicyTask` gained `model_refs_from_shared_state`, covering every model `deploy_simulated_model` created this run in one policy (`multi_model_full_load.yaml` now runs `apply_auth_policy` right after `deploy_simulated_model`, granting `system:authenticated` — the same owner the subscriptions use).

### 3. Subscriptions sharing a model race the controller's `TokenRateLimitPolicy` reconciliation

With the above two fixes in place, `multi_model_full_load` still intermittently got `403 subscription rate limiting policies are not ready`. `oc logs` on `maas-controller` showed why: `provision_subscriptions_distributed` creates several subscriptions in quick succession, and subscriptions that share an underlying model race each other updating that model's single `TokenRateLimitPolicy` (`"the object has been modified; please apply your changes to the latest version and try again"` — a standard, auto-retried Kubernetes conflict). The existing `_wait_for_subscription_ready()` only waits for the *subscription's* `status.phase` to become non-empty (accepts `"Degraded"`, not just `"Active"`) — a signal that can be set before these races finish resolving.

New helper `_wait_for_token_rate_limit_policies_ready()` in `harness/tasks/subscription.py` polls every unique model a batch of subscriptions references (same label-selector lookup `harness/tasks/platform_health.py:CheckModelHealthTask` already uses) until each `TokenRateLimitPolicy` reports `Accepted=True` and `Enforced=True` — checking the thing that actually gates traffic, the same principle `_settle_and_evaluate()` already applies to metrics-dependent assertions (ADR-014). `ProvisionSubscriptionsDistributedTask` calls this after creating all subscriptions; new `token_rate_limit_ready_max_wait_s` param (default 60s), configured in `multi_model_full_load.yaml`.

### 4. Precise rate-limit-trigger measurement: `first_rate_limited_at_tokens`

Asserting "throughput stays under the limit" and "error rate stays low" on a scenario whose whole point is *triggering* the limit was self-contradicting (confirmed live: a low error rate just meant the limit wasn't firing). `send_requests` now tracks `first_rate_limited_at_tokens` — cumulative `total_tokens_sent` at the moment of the *first* 429, left absent (not `0`/`None`) in `shared_state[result_key]` until a 429 actually happens, so a referencing assertion stays honestly PENDING rather than reading a false zero. `rate_limit_validation`/`rate_limit_shared_across_users` assert this lands at-or-just-above the configured `token_limit` (`first_rate_limit_reaches_budget`) without excessive overshoot (`first_rate_limit_within_spillover`, `<= 100` tokens) — a scenario-specific, non-generic metric name following the same `promql` pass-through convention (ADR-015) as every other harness-side-only number in this codebase.

### 5. `token_window` must be long, not short — for two independent reasons

Both rate-limit scenarios now default `token_window` to `"24h"` (was the implicit `"1s"`/explicit `"10m"`) — deliberately far longer than either scenario's own runtime, so the budget effectively "never resets during the test" (the subscription is deleted at cleanup regardless). This closes two separate problems at once, discovered in this order:
1. **Achievable-throughput problem**: sequential (`concurrency: 1`) round trips generate only ~30-40 tokens/sec of real demand. Against a tight 1-second window, a `token_limit` anywhere near that figure can simply never be exceeded — not a bug, just insufficient demand relative to the window's refill rate.
2. **Retry-masking problem**: confirmed in the `openai` SDK's own source (`_base_client.py`) that `AsyncOpenAI` retries a 429 automatically by default (`max_retries=2`, honoring a `Retry-After` header up to a 2-minute cap, or ~0.5-8s exponential backoff otherwise). Against a short window, that retry can land in the *next* window and quietly succeed — the SDK only returns the final (successful) response to calling code, so `rate_limited_count`/`first_rate_limited_at_tokens` can stay at `0`/PENDING even though Limitador's own `limited_calls` counter shows a real denial underneath.

**Disabling retries (`max_retries=0`) was tried and reverted.** It's a global behavior change (every scenario's `send_requests` calls, not just the two rate-limit ones) for a problem the window-length fix solves anyway: once a window is drastically longer than any plausible retry delay (SDK backoff is at most low single-digit seconds without a server `Retry-After`, capped at 2 minutes with one), every retry for a given logical request keeps hitting the same still-exhausted budget and gets denied again, and the SDK eventually surfaces a genuine `APIStatusError(429)` after exhausting its own retry budget regardless. `rate_limit_shared_across_users`'s pre-existing `"10m"` window is very likely why it never exhibited the retry-masking symptom in the first place, unlike `rate_limit_validation`'s original `"1s"` default.

### 6. Redaction false positive: `token_limit`/`token_window` aren't credentials

`harness/runner.py:_redact_sensitive_config`'s key pattern (`(?:^|_)(token|secret|password)(?:$|_)`) masked these two fields in the Run Settings view, since "token" is a complete underscore-delimited word in them too — the same false-positive shape already documented for `"key"` (`key_name`, `key_pool`, ...), just narrower: unlike `"key"`, `"token"` can't be excluded as a blanket substring without also un-redacting real credentials named the same way (`target_token`, `sa_token`). Fixed with an exact-name exception set (`_SENSITIVE_CONFIG_KEY_EXCEPTIONS = {"token_limit", "token_window"}`) rather than a pattern change, so it can't accidentally un-redact anything else.

## Consequences

**Positive:**
- `multi_model_full_load` and `subscription_without_authpolicy` both confirmed passing live after these fixes — the dedicated-per-model-route mechanism is now the standard way any scenario targets a model `deploy_simulated_model` created this run, reusable by future multi-model scenarios without rediscovering this.
- `rate_limit_validation`/`rate_limit_shared_across_users` now measure the thing their names claim to measure (precisely where enforcement kicks in), not a proxy (aggregate throughput/error-rate) that turned out to contradict the scenario's own point.
- The retry investigation, even though the fix wasn't adopted, is preserved here rather than lost — a future "why does this rate-limit scenario sometimes not trigger" question has the actual mechanism on record instead of needing to be re-derived.

**Negative:**
- `_wait_for_token_rate_limit_policies_ready()`'s `60s` default timeout is unverified against a slower cluster or a larger `subscription_count` than this repo's own `multi_model_full_load` default (6) — a cluster with heavier reconciliation load may need a larger `token_rate_limit_ready_max_wait_s`.
- The per-model dedicated-route mechanism assumes every `deploy_simulated_model`-created model's HTTPRoute follows the `{namespace}/{name}/v1/...` path convention observed on this cluster — not re-derived from the LLMInferenceService controller's source, only confirmed by live reproduction on one cluster.
- A long `token_window` (`"24h"`) means if cleanup ever fails to delete the `MaaSSubscription` (an existing, separately-documented gap — see the Stopping a Run section in `CLAUDE.md`), the leftover subscription's rate limit won't naturally expire for a full day, unlike the previous short-window defaults.
