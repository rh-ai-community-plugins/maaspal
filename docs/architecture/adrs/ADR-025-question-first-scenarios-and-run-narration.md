# ADR-025: Question-First Scenarios and Run Narration

## Status

Accepted. Supersedes ADR-020's category list (the mechanism is unchanged).

## Context

MaaS:PAL exists to answer one question for an admin: *"Is my MaaS behaving the way I expect, given what I set up?"* By its fourteenth scenario the set had stopped reading that way:

- **Names and descriptions described mechanisms, not questions.** Examples: `rate_limit_validation`, `metrics_fill`, and descriptions full of ADR numbers and CR jargon. The launch form showed every config key as a raw `snake_case` text box, including cluster plumbing (`limitador_namespace`, name prefixes, timeouts). Some keys had to be kept in sync by hand with values from another tab (a model's HTTPRoute name).
- **Overlap.** `rate_limit_validation` and `rate_limit_validation_existing_subscription` asked the same question. The second was the closer fit to the core question, but had the weaker, stale checks. `multi_key_load` (1 request per key by default) duplicated `single_key_load`. `multi_model_subscription_spread` was a strict subset of `multi_model_full_load`, with no traffic and no checks.
- **Run pages showed pass/fail but not what happened.** Rate-limit cards showed derived numbers (`first_rate_limited_at_tokens - token_limit`, i.e. "7"), not "throttled after 57 tokens". Nothing showed whether throttling *stayed* shut, which subscription a key landed on, or what a run had created. PASS/FAIL on exploratory scenarios was inverted ("a FAIL here is the finding").
- **Hidden SDK retries.** The OpenAI SDK retries 429s twice by default. A retry landing in a fresh window can turn a real denial into a success, and it makes Limitador's counts up to 3× the harness's.

## Decision

### Scenario metadata (display only; the harness never reads it)

New optional top-level YAML keys, passed through by `api/routes/scenarios.py`:

| Key | Purpose |
|---|---|
| `title` | The question the scenario answers. This is what the UI shows, not `name`. |
| `summary` | One sentence on the card. `description` moves into the launch form's "How it works". |
| `kind` | `verify` (uses the existing setup; may only create API keys) or `explore` (creates temporary models, subscriptions or identities to probe MaaS). Enforced by `test_scenario_declares_display_metadata`. |
| `mutates`, `needs_rbac`, `est_duration` | Badges: what the run touches, extra RBAC it needs, rough duration. |
| `order` | Position within its category. |
| `requires` | Config keys that must be non-empty before **Launch** is enabled. |
| `inputs` | Per-config-key form metadata: `label`, `help`, `advanced` (collapsed section), `choices` (select), `show_if`, `placeholder`, and autofill via `from_subscription: limit\|window` or `from_model: http_route`. `config:` stays flat. |
| `plan_template` | The "What this run will do" sentence, with `${config.x}`, `${model}` and `${subscription}` substituted live. |
| `previous_names` | Old ids, so run-history rows from before a rename resolve to the current title. |
| `verdict` | `pass`/`fail` sentence templates (`${config.x}` at load time, `${harness.ns.key}` at run end) for the banner at the top of the run page. |

Categories are now organised by question: Quick check, Rate limits, Access control, API keys, Usage metrics, Performance, Diagnostics, and Custom.

### Harness additions

- **Task `when: {config_key: value}`** (`harness/config.py`). A task runs only if the config matches. This gives one scenario a mode switch (existing vs temporary subscription; per-user vs shared expectation) instead of duplicate files.
- **Run narration and outputs** in the progress JSON (`harness/runner.py`):
  - per-task `summary` (tasks set `shared_state["task_summary"]`)
  - `traffic` (per-burst summary plus a downsampled timeline that always keeps status transitions, plus the limit line)
  - `resources` (names only, never key material or tokens)
  - `tables` (tasks publish rows via `shared_state["_tables"]`)
  - `verdict`
- **Assertion display metadata** (`harness/result.py`): `label`, `description`, `unit`, a computed human-readable `target`, and a `between: [lo, hi]` form so cards show the real value. Bounds accept simple arithmetic left over from `${config.x}` substitution, parsed by an AST walker (never `eval`).
- **`send_requests` additions:**
  - a `retries` param (defaults to the SDK's 2; rate-limit scenarios set 0)
  - HTTP attempts counted via an httpx response hook, so retries are visible
  - `stop_after_429s`
  - round-robin key-pool ordering
  - new counters: `tokens_before_first_429`, `requests_before_first_429`, `seconds_to_first_429`, `successes_after_first_429`, `server_error_count`, `other_error_count`, `non_throttle_error_rate_pct`
- **`verify_subscription_models`.** One key pinned to a subscription; per model, reachability plus (for small limits) a limit probe. It reuses `api/maas_client.list_subscriptions()` from the same image and ServiceAccount.
- **`check_model_health`** also reuses `list_models()`. It adds Ready, subscription, auth-policy and namespace-label checks, and checks every internal model when none is given.
- **`pause`** now takes window strings (`"1m"`), a buffer, and a cap.

### The scenario set (old id → new)

| New | From | Change |
|---|---|---|
| `smoke_test` | `platform_health_check` | Retitled. |
| `verify_subscription` | — | New, flagship. |
| `verify_subscription_rate_limit` | `rate_limit_validation` + `rate_limit_validation_existing_subscription` | Merged; `mode: existing \| temporary`. |
| `keys_share_user_budget` | — | New. |
| `rate_limit_window_recovery` | — | New. |
| `subscription_auto_selection` | `rate_limit_priority_precedence` | Retitled. |
| `rate_limit_per_user_or_shared` | `rate_limit_shared_across_users` | `expected_behavior` input added. |
| `denied_without_auth_policy` | `subscription_without_authpolicy` | Retitled. |
| `api_key_lifecycle` | `api_key_lifecycle` | Same id; adds a revoked-key-not-searchable check. |
| `usage_metrics_accuracy` | `metrics_fill` | Retitled. |
| `load_test` | `single_key_load` + `multi_key_load` | Merged; `key_count` input. |
| `gateway_overhead` | `direct_inference` | Direct vs MaaS comparison; the MaaS leg is optional. |
| `multi_model_load` | `multi_model_full_load` + `multi_model_subscription_spread` | Merged; `request_count: 0` builds the environment only. |
| `model_config_health` | `model_health_check` | Retitled. |

## Consequences

**Positive:**
- Every card and launch form leads with the question it answers. Plumbing is collapsed, and model- or subscription-derived values fill themselves in.
- A run explains itself: what each step did, how much traffic got through and where throttling started (chart with the limit line), what was created and whether it was cleaned up, plus a one-sentence verdict.
- PASS/FAIL always means "matches what you expect". Exploratory scenarios take the expectation as an input.

**Negative:**
- Renamed ids break anything outside this repo that launches scenarios by id (`POST /api/runs`). Run history is covered by `previous_names`; external callers are not.
- PENDING assertions still don't fail a run. Scenarios guard their PENDING-able checks with an always-populated companion assertion (e.g. `throttled_at_all` alongside `tokens_before_first_429`) rather than changing that rule globally.
- `CATEGORY_ORDER` (TypeScript) and `_KNOWN_CATEGORIES` (Python) are still hand-synced, as in ADR-020.

**Neutral:**
- Existing custom scenarios keep working unchanged. Every new key is optional and defaulted by the API.
