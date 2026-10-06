# ADR-027: Browser-Originated Traffic ("Send from user browser")

## Status

Accepted. Phase 1: fixed bursts only.

## Context

Every inference request MaaS:PAL sent left the harness Job pod, inside the cluster. That tests MaaS as an in-cluster caller sees it. Most real clients sit outside: a browser or an app on someone's machine, coming in through cluster ingress, the external `maas.<apps domain>` route and its TLS, and, for a browser, the cross-origin rules a web page has to follow. Admins want to check that path from where they actually are.

Requirements:

- A **Send from user browser** checkbox (advanced setting, like Streaming) on every scenario with a send-requests step. On by default for small scenarios, off for large ones (rate limits, load).
- Results, checks, charts, logs and MaaS metrics must come out exactly as they do for a pod-sent run.
- **Nothing on the MaaS or cluster side is changed to make it work.** If the gateway won't take a correctly formed call from a browser (CORS), that is the test's result.

## Decision

### The Job stays the source of truth; the browser only generates load

For a browser step, `send_requests` (`from_browser`, falling back to the `send_from_browser` config key like `stream`) resolves its targets as usual: URL, model and the run's own API keys, per-model routes included. It then writes an **order** to the PVC (`harness/browser_channel.py`) and waits. The open run page (`src/app/useBrowserSender.ts`) polls `GET /api/runs/{id}/browser-work`, claims the order (`POST …/browser-work/claim`, exactly one tab, via an `O_EXCL` claim file), sends the requests with `fetch` (`src/app/browserSender.ts`: the same request bodies as `_send_one`, the OpenAI SDK's retry rules, streams read to the end with a usage chunk), and posts one **raw record** per request (`POST …/browser-results`) every 500 ms.

The Job tails those records and feeds each one into `record_outcome()`, **the same bookkeeping** as a request it sent itself. The per-request half of `do_request` was split out for exactly this. Everything downstream is untouched: `shared_state[result_key]`, `emit()`, progress.json, the traffic chart, assertions, verdict templates, and the metrics poller (Limitador counts at the gateway, wherever the call came from). Failed browser requests are printed as `[send_requests] (browser) …` log lines, so the existing pod-log capture shows them too.

Alternatives rejected:

- **The browser computes the results.** That would duplicate the aggregation in TypeScript, let a browser hand in any numbers it liked, and split the run's state across two places.
- **Relaying through the BFF.** No CORS problem, but the traffic would come from inside the cluster again, which defeats the point.

### The browser gets the run's temporary key, nothing more

The claim response is the only place a key value leaves the cluster: `Cache-Control: no-store`, behind the same `require_user` gate as every route, and only while the run is `RUNNING`. The status poll carries no keys. The order file is group-readable only (`0640`, because the BFF and Job pods may run as different UIDs) and is deleted when the step ends, including on Stop. The keys are revoked at cleanup as always. The harness refuses to hand its own ServiceAccount token to a browser: a browser step needs an API key (`key_pool`, `key_index` or `token`). Records are raw outcomes, validated by pydantic (at most 500 per post). The Job recomputes every number from them, and only `server`/`x-ext-auth-reason` headers are kept. Response text is never capped: bodies, status text and browser errors have no length limit anywhere on the way.

### What a browser can't do, reported as such

- **CORS.** The page (`rh-ai.<apps>`) calls `maas.<apps>` cross-origin with an `Authorization` header, so the browser sends a preflight first. If no readable answer comes back, the browser can't tell a CORS refusal from an unreachable host. So the first request is sent alone, and if it gets no readable answer the step ends with reason `blocked`. The step then **fails** with a finding, verdict and log line: "Blocked by the browser: no readable answer from `<gateway host>` (`<the browser's error>`) — the gateway did not allow a cross-origin call from `<page origin>`, or is not reachable from the user's machine." No cluster change is made or suggested.
- **Hidden headers.** Response headers the gateway doesn't expose to cross-origin callers can't be read, so `failure_origin()` falls back to status and body.
- **No open page.** If nothing claims the order within `browser_claim_timeout_s` (default 120), or the tab goes quiet for `browser_idle_timeout_s` (default 60), the step fails and says why.

### Messages are reported exactly as received

What a request got back reaches the run log and the run results unchanged: the status line and the **whole** body of a failed reply, or the browser's own error (`TypeError: Failed to fetch`) when no answer could be read. Nothing is shortened, reflowed or reworded, and the run page wraps long or multi-line text instead of cutting it. The same now holds for pod-sent requests: `_error_message()` used to cut bodies to 80 characters and transport causes to 100. Explanations, like the "Blocked by the browser" sentence, go into findings and verdicts next to the raw message, never in its place.

### Where a step's requests came from

In "What happened", every step that sent requests carries a label: **Sent from the pod** or **Sent from the browser**, with a tooltip saying which side a failure can come from. A second label gives the endpoint and streaming (`/v1/chat/completions · streamed`), or `N request types` for a step that tries many. Both come from the burst's traffic record (`origin`, `request`, `stream`), which the harness writes when the step starts, from the step's resolved `api`/`stream`. Runs from before this have no `origin` (all were pod-sent) and fall back to the summary's `api`/`stream`.

### Scope

Phase 1 is fixed bursts: `key_pool`, `key_index`, `model_from_shared_state`/per-key `target_model`, `api`, `stream`, `stop_after_429s`. `until_throttled`, `stages`, `insecure_tls` and `url_from_shared_state` raise a clear "fixed bursts only" error. Those scenarios show the checkbox **off** by default, and `harness/tests/test_scenarios.py` enforces that. `gateway_overhead` and `request_types` don't offer it at all: their comparisons need in-cluster endpoints. Phase 2 would port the `until_throttled` ramp to the browser, with the pod still running `_diagnose_unthrottled` on the reported ramp history.

Defaults: on for `smoke_test`, `verify_subscription`, `api_key_lifecycle`, `denied_without_auth_policy`, `usage_metrics_accuracy`; off for the Rate limits scenarios, `load_test` and `multi_model_load`.

## Consequences

- A browser run needs the run page open in one tab for its send steps. The page warns before unload and shows a banner while it sends.
- Browser-sent numbers are comparable with pod-sent ones (`test_browser_records_tally_like_requests_sent_from_the_pod`), except where the browser can't see something (exposed headers, connection-level detail).
- Not yet run live. The empirical-verification checklist records what the gateway actually does with browser calls once it is.
