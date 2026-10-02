import asyncio
import contextlib
import time

import httpx
from openai import APIStatusError, AsyncOpenAI, DefaultAsyncHttpxClient

from harness.durations import parse_duration_s
from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

_DEBOUNCE_SECS = 0.1

# Matches the OpenAI SDK's own default. A scenario that needs every 429 to be
# visible as-is (rate-limit checks against a short real window, where a hidden
# retry can land in a fresh window and quietly succeed) sets `retries: 0`.
_DEFAULT_RETRIES = 2

# `until_throttled` bursts: send as fast as needed to use up the limit within
# one window, ramping concurrency up while the measured token rate is too low.
# The bounds are scenario settings (max_concurrency / max_duration_s /
# max_requests); these are only their defaults.
_UNTIL_THROTTLED_STOP_AFTER_429S = 3
_DEFAULT_MAX_CONCURRENCY = 128
_DEFAULT_MAX_REQUESTS = 20000
# When the window is unknown, or longer than this, the time budget defaults
# to this many seconds (a 24h window can't be waited out by a test).
_DEFAULT_MAX_DURATION_S = 600.0
_RAMP_INTERVAL_S = 2.0
# Ramp while the measured rate is below this multiple of the required rate.
_RAMP_HEADROOM = 1.2
# Doubling concurrency that raises the token rate by less than this factor
# means the model, not the harness, is the bottleneck.
_SCALING_GAIN_THRESHOLD = 1.3


def _diagnose_unthrottled(
    history: list[dict],
    required_tps: float | None,
    max_concurrency: int,
    stop_reason: str,
    limit: float | None,
    window_text: str,
) -> tuple[str, str]:
    """Why a burst never got throttled: (bound, plain-language explanation).

    bound: "response" (the model answered too slowly — more concurrency
    stopped helping), "send" (still scaling when the concurrency ceiling was
    hit), "time" / "requests" (a budget ran out first), or "unknown"."""
    peak = max((h["tokens_per_s"] for h in history), default=0.0)
    last_c = history[-1]["concurrency"] if history else 1
    need = (
        f"Using up {limit:,.0f} tokens within {window_text} needs ~{required_tps:,.0f} tokens/s; "
        if required_tps and limit
        else ""
    )
    got = f"this run peaked at {peak:,.0f} tokens/s at concurrency {last_c}"
    # Average rate at the top concurrency level vs the level below it —
    # averaged, since a single short interval's rate is noisy.
    samples: dict[int, list[float]] = {}
    for h in history:
        samples.setdefault(h["concurrency"], []).append(h["tokens_per_s"])
    by_c = {c: sum(v) / len(v) for c, v in samples.items()}
    levels = sorted(by_c)
    gain = None
    if len(levels) >= 2 and by_c[levels[-2]] > 0:
        gain = by_c[levels[-1]] / by_c[levels[-2]]
    latency = ""
    if history:
        latency = f" (median latency {history[0]['p50_ms']:,.0f} ms → {history[-1]['p50_ms']:,.0f} ms)"

    if stop_reason == "requests":
        return "requests", (
            f"{need}{got}, and stopped at the request cap before the limit was reached. "
            "Raise Max requests under Advanced settings."
        )
    if gain is not None and gain < _SCALING_GAIN_THRESHOLD:
        return "response", (
            f"{need}{got}. Doubling concurrency only raised the rate {gain:.1f}×{latency} — "
            "the model can't answer fast enough to use the limit up, so it can't be verified by "
            "traffic on this model."
        )
    if last_c >= max_concurrency:
        return "send", (
            f"{need}{got} and was still speeding up when it hit the Max concurrency ceiling. "
            "Raise Max concurrency under Advanced settings and run again."
        )
    if stop_reason == "time":
        return "time", (
            f"{need}{got}, and ran out of its time budget while still ramping up. "
            "Raise Max duration under Advanced settings."
        )
    return "unknown", f"{need}{got}, and was never throttled."

# Per-request timeline entries kept in memory per send_requests invocation —
# the runner downsamples further before writing it out for the UI chart.
_TIMELINE_CAP = 5000


def _status_class(status_code: int | None) -> str:
    """Bucket a request outcome for the timeline chart and summary card."""
    if status_code is None:
        return "error"
    if status_code == 429:
        return "throttled"
    if status_code in (401, 403):
        return "denied"
    if status_code == 404:
        return "not_found"
    if status_code >= 500:
        return "server_error"
    return "error"


def _error_message(exc: Exception) -> str:
    """A short, groupable description of why a request failed. The OpenAI
    SDK reports every transport failure as a bare "Connection error." — the
    real reason (DNS, refused, TLS, timeout) is on its __cause__."""
    if isinstance(exc, APIStatusError):
        body = (getattr(exc.response, "text", "") or "").strip().replace("\n", " ")
        reason = getattr(exc.response, "reason_phrase", "") or ""
        return f"HTTP {exc.status_code} {reason}".strip() + (f": {body[:80]}" if body else "")
    cause = exc.__cause__ or exc.__context__
    if cause is not None and str(exc).strip().lower().startswith("connection error"):
        return f"{type(cause).__name__}: {str(cause)[:100]}"
    return f"{type(exc).__name__}: {str(exc)[:100]}"


def _summary_line(r: dict, planned: int | None) -> str:
    """One-line human narration of a send_requests burst, shown under its chip."""
    sent = f"{r['total_requests']}/{planned}" if planned else str(r["total_requests"])
    parts = [f"{sent} requests", f"{r['success_count']} OK"]
    if r["rate_limited_count"]:
        parts.append(f"{r['rate_limited_count']} throttled (429)")
    if r["unauthorized_count"]:
        parts.append(f"{r['unauthorized_count']} denied (401/403)")
    if r.get("not_found_count"):
        parts.append(f"{r['not_found_count']} not found (404)")
    if r["server_error_count"]:
        parts.append(f"{r['server_error_count']} server errors")
    if r["other_error_count"]:
        parts.append(f"{r['other_error_count']} other errors")
    parts.append(f"{r['total_tokens_sent']} tokens")
    if "tokens_before_first_429" in r:
        parts.append(f"first 429 after {r['tokens_before_first_429']} tokens")
    samples = r.get("error_samples") or []
    if samples and not r["success_count"]:
        # Nothing worked — say why, right in the narration.
        parts.append(f"most common error: {samples[0]['count']}× {samples[0]['message']}")
    return " · ".join(parts)


def _float_or_none(value: object, shared_state: dict, ref: object) -> float | None:
    """A number from an explicit param, else from shared_state["ns"]["key"]."""
    if value not in (None, ""):
        return float(value)
    if ref:
        ns, _, key = str(ref).partition(".")
        found = (shared_state.get(ns) or {}).get(key)
        return float(found) if found is not None else None
    return None


def _redact(token: str) -> str:
    if not token:
        return "(empty)"
    return token[:8] + "****" if len(token) > 8 else "****"


def _distribute(total: int, n_keys: int) -> list[int]:
    """Return per-key request counts: floor(total/n_keys) each, remainder to first."""
    base = total // n_keys
    remainder = total % n_keys
    return [base + (remainder if i == 0 else 0) for i in range(n_keys)]


def _percentiles(latencies: list[float]) -> dict[str, float]:
    if not latencies:
        return {"p50_latency_ms": 0.0, "p95_latency_ms": 0.0, "p99_latency_ms": 0.0}
    s = sorted(latencies)
    n = len(s)

    def _pct(p: float) -> float:
        return s[min(int(n * p / 100), n - 1)]

    return {
        "p50_latency_ms": _pct(50),
        "p95_latency_ms": _pct(95),
        "p99_latency_ms": _pct(99),
    }


class SendRequestsTask(Task):
    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()

        count = int(self.params.get("count", 10))
        concurrency = int(self.params.get("concurrency", 5))
        prompt = str(self.params.get("prompt", "Hello"))
        result_key = str(self.params.get("result_key", "inference_results"))

        # `skip_unless: "ns.key"` — only send if that shared_state value is
        # truthy (e.g. "direct_probe.reachable"): a precondition that failed
        # earlier is reported there, not as a wall of failed requests here.
        skip_unless = self.params.get("skip_unless")
        if skip_unless:
            ns, _, key = str(skip_unless).partition(".")
            if not (ctx.shared_state.get(ns) or {}).get(key):
                ctx.shared_state["task_summary"] = f"Skipped — {skip_unless} isn't set (see the earlier step)"
                return TaskResult(task_name=self.name, status="PASS", duration_ms=0.0)
        retries = int(self.params.get("retries", _DEFAULT_RETRIES))
        # Stop sending once this many requests have been throttled (0 = never)
        # — lets a rate-limit scenario use a generous request ceiling (enough
        # to exhaust whatever limit it's pointed at) without hammering the
        # gateway for the rest of it once the answer is already in.
        stop_after_429s = int(self.params.get("stop_after_429s", 0))
        # Rate-limit bursts: keep sending until MaaS throttles, as fast as
        # needed to use the limit up within one window (a fixed window resets
        # otherwise, and the limit is never reached). The limit and window come
        # from `limit`/`window` params or `*_from_shared_state` ("ns.key", e.g.
        # "subscription_limits.token_limit"). Bounds: max_concurrency,
        # max_duration_s (default: the window, capped), max_requests.
        until_throttled = str(self.params.get("until_throttled", "")).lower() in ("true", "1", "yes")
        limit_tokens: float | None = None
        window_s: float | None = None
        window_text = ""
        max_concurrency = concurrency
        max_duration_s = _DEFAULT_MAX_DURATION_S
        if until_throttled:
            count = int(self.params.get("max_requests") or _DEFAULT_MAX_REQUESTS)
            stop_after_429s = stop_after_429s or _UNTIL_THROTTLED_STOP_AFTER_429S
            max_concurrency = max(concurrency, int(self.params.get("max_concurrency") or _DEFAULT_MAX_CONCURRENCY))
            limit_tokens = _float_or_none(
                self.params.get("limit"), ctx.shared_state, self.params.get("limit_from_shared_state")
            )
            window_param = self.params.get("window")
            if window_param not in (None, ""):
                window_s = parse_duration_s(window_param)
                window_text = str(window_param)
            else:
                window_s = _float_or_none(None, ctx.shared_state, self.params.get("window_from_shared_state"))
                window_text = f"{window_s:g}s" if window_s else ""
            explicit_duration = self.params.get("max_duration_s")
            if explicit_duration not in (None, ""):
                max_duration_s = float(explicit_duration)
            elif window_s:
                max_duration_s = min(window_s, _DEFAULT_MAX_DURATION_S)
        # Step load (`stages`: concurrency levels, e.g. "5,10,25,50", each held
        # for `stage_duration_s`): sends continuously, stepping concurrency up,
        # and reports throughput/latency/errors per step — where it starts to
        # struggle, not just one averaged number.
        stages_param = self.params.get("stages")
        stage_levels: list[int] = []
        if stages_param not in (None, ""):
            raw = stages_param if isinstance(stages_param, list) else str(stages_param).split(",")
            stage_levels = [int(str(x).strip()) for x in raw if str(x).strip()]
        stage_duration_s = float(self.params.get("stage_duration_s") or 30)
        if stage_levels:
            count = int(self.params.get("max_requests") or 10**9)
            max_concurrency = max(stage_levels)
            concurrency = stage_levels[0]
        # Whether the run page should draw this burst's traffic chart — only
        # where the shape over time answers the scenario's question.
        chart = str(self.params.get("chart", "")).lower() in ("true", "1", "yes")
        # Self-signed / cluster-CA endpoints (a model's in-cluster service).
        insecure_tls = str(self.params.get("insecure_tls", "")).lower() in ("true", "1", "yes")

        url_from_shared_state = self.params.get("url_from_shared_state")
        if url_from_shared_state:
            # A model deploy_simulated_model just created, called directly on
            # its own Route (expose_model_route) — bypassing the MaaS gateway
            # (scenarios/gateway_overhead.yaml).
            models = ctx.shared_state.get(url_from_shared_state, [])
            if not models or not models[0].get("direct_url"):
                raise RuntimeError(
                    f"url_from_shared_state={url_from_shared_state!r}: no deployed model with a "
                    "direct URL — run deploy_simulated_model and expose_model_route first"
                )
            direct = str(models[0]["direct_url"]).rstrip("/")
            self.params["url"] = direct if direct.endswith("/v1") else f"{direct}/v1"
            self.params["model"] = models[0]["name"]

        # Every HTTP response the SDK receives, retries included — the SDK
        # otherwise hides its own automatic 429/5xx retries entirely, so a
        # "request" here can be up to retries+1 gateway hits. Surfacing both
        # numbers keeps harness-side counts comparable to gateway-side ones
        # (e.g. Limitador's limited_calls).
        http_attempts = 0

        async def _count_attempt(_response: httpx.Response) -> None:
            nonlocal http_attempts
            http_attempts += 1

        def _client(api_key: str, base_url: str) -> AsyncOpenAI:
            return AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                max_retries=retries,
                http_client=DefaultAsyncHttpxClient(
                    event_hooks={"response": [_count_attempt]},
                    verify=not insecure_tls,
                ),
            )

        key_index = self.params.get("key_index")
        if key_index is not None:
            # Targets exactly one key out of several dynamically-created
            # ones (e.g. one per user from create_user/provision_keys_for_
            # users, ADR-023) — something neither key_pool (uses the whole
            # pool) nor a static params.token (YAML-only, can't reference
            # shared_state) can do. Injecting into params["token"] reuses
            # the existing single-key resolution chain unchanged.
            api_keys = ctx.shared_state.get("api_keys", [])
            self.params["token"] = api_keys[int(key_index)]["key"]

        model_from_shared_state = self.params.get("model_from_shared_state")
        if model_from_shared_state:
            # Targets a model deploy_simulated_model (harness/tasks/model.py)
            # just created at runtime. Confirmed live: such a model never
            # appears in GET /v1/models — that requires full governance
            # pairing (a MaaSSubscription AND a MaaSAuthPolicy, which a
            # scenario like subscription_without_authpolicy deliberately
            # never has). But its own dedicated per-model route — auto-
            # created by the LLMInferenceService controller, confirmed live
            # to be reachable as soon as RuntimeReady — works directly at
            # {MAAS_API_URL}/{namespace}/{name}/..., with the bare model
            # name in the request body. Confirmed end-to-end live, including
            # that this path still goes through the same gateway AuthPolicy
            # (a subscription with no matching policy still correctly
            # denies; one with a policy still succeeds).
            models = ctx.shared_state.get(model_from_shared_state, [])
            if models:
                namespace, name = models[0]["namespace"], models[0]["name"]
                self.params["model"] = name
                self.params["url"] = f"{ctx.maas_api_url}/{namespace}/{name}/v1"

        key_pool_entries = self._resolve_key_pool_entries(ctx)
        has_target_models = any(e.get("target_model") for e in key_pool_entries)

        # The per-key loop below overrides both url and model for any entry
        # carrying its own target_model, so the value resolved here is only
        # ever used as a placeholder for entries that don't — this just
        # avoids _resolve_url_model_and_token performing a real (and
        # pointless) GET /v1/models discovery call in that case.
        if has_target_models:
            self.params.setdefault("url", f"{ctx.maas_api_url}/v1")

        subscription_aware_models: dict[int, str] = {}
        if (
            key_pool_entries
            and not has_target_models
            and not self.params.get("model")
            and any(e.get("subscription") for e in key_pool_entries)
        ):
            # No explicit model and no per-key target_model already resolved
            # — an auto-selected subscription (blank `subscription` param)
            # and an independently auto-discovered/DEFAULT_MODEL model are
            # otherwise two unrelated choices with no guarantee they're
            # compatible (confirmed live: exactly what broke
            # multi_model_full_load before target_model was wired through).
            # Cross-reference each key's own bound subscription against
            # /v1/models' per-model subscriptions[] list so a key only ever
            # targets a model it can actually reach.
            subscription_aware_models = await self._resolve_models_by_subscription(
                ctx, key_pool_entries
            )

        url, model, token = await self._resolve_url_model_and_token(ctx)

        if key_pool_entries:
            key_strings = [e["key"] for e in key_pool_entries]
            key_urls: list[str] = []
            key_models: list[str | None] = []
            for i, entry in enumerate(key_pool_entries):
                sub_model_id = subscription_aware_models.get(i)
                target = entry.get("target_model")
                if sub_model_id:
                    # Already registered (found via /v1/models) — reachable
                    # through the generic discovered gateway URL.
                    key_urls.append(url)
                    key_models.append(sub_model_id)
                elif target and "/" in target:
                    # A dynamically-deployed model (provision_keys_
                    # distributed) — same reasoning as model_from_shared_
                    # state above: use its own dedicated per-model route
                    # since it isn't registered in /v1/models yet.
                    t_namespace, t_name = target.split("/", 1)
                    key_urls.append(f"{ctx.maas_api_url}/{t_namespace}/{t_name}/v1")
                    key_models.append(t_name)
                else:
                    key_urls.append(url)
                    key_models.append(target)
            clients = [_client(k, u) for k, u in zip(key_strings, key_urls)]
            print(
                f"[send_requests] base_url={url} model={model} "
                f"keys=[{', '.join(_redact(k) for k in key_strings)}] "
                f"count={count} concurrency={concurrency}",
                flush=True,
            )
        else:
            key_models = []
            clients = [_client(token, url)]
            print(
                f"[send_requests] base_url={url} model={model} "
                f"token={_redact(token)} count={count} concurrency={concurrency}",
                flush=True,
            )

        latencies: list[float] = []
        success = 0
        fail = 0
        rate_limited_count = 0
        unauthorized_count = 0
        server_error_count = 0
        not_found_count = 0
        other_error_count = 0
        error_counts: dict[str, int] = {}
        total_tokens_sent = 0
        prompt_tokens_sent = 0
        completion_tokens_sent = 0
        # Cumulative total_tokens_sent at the moment of the FIRST 429 — lets
        # a scenario assert the rate limit actually triggered around the
        # configured budget (with an expected spillover margin for whichever
        # in-flight request pushed the total over, e.g. one request starting
        # at 49/50 tokens used that still completes, taking the total to 57)
        # instead of just "some requests eventually got denied".
        first_rate_limited_at_tokens: int | None = None
        requests_before_first_429: int | None = None
        seconds_to_first_429: float | None = None
        # Successful requests that completed AFTER the first 429 — with a long
        # window this should stay 0 ("once throttled, stays throttled"); a
        # non-zero value means the limit leaked or the window reset mid-run.
        successes_after_first_429 = 0
        # [t_offset_s, cumulative_tokens, status_class, latency_ms] per
        # completed request — the run page's traffic chart.
        timeline: list[list] = []
        skipped = 0
        # until_throttled bookkeeping: current concurrency level, why the
        # burst ended, and the token rate measured at each ramp step.
        current_concurrency = concurrency
        stage_idx = 0
        stage_acc: list[dict] = [
            {"latencies": [], "ok": 0, "throttled": 0, "errors": 0, "tokens": 0} for _ in stage_levels
        ]
        stop_reason: str | None = None
        ramp_history: list[dict] = []
        first_429_at: float | None = None
        concurrency_at_first_429: int | None = None
        max_tokens_per_request = 0
        required_tps = (limit_tokens / window_s) if (limit_tokens and window_s) else None
        traffic_entry: dict = {
            "chart": chart,
            # Bursts sharing a chart_group are drawn on one timeline (e.g.
            # before and after waiting out a rate-limit window), positioned
            # by their wall-clock start t0.
            "chart_group": self.params.get("chart_group"),
            "label": self.params.get("label"),
            "t0": time.time(),
        }
        if limit_tokens is not None:
            # The limit read off the subscription under test — the chart's
            # reference line (otherwise the scenario's own token_limit config).
            traffic_entry["limit"] = limit_tokens
        ctx.shared_state.setdefault("_traffic", {})[result_key] = {
            **traffic_entry,
            "task": self.name,
            # The safety ceiling isn't a plan the user should see.
            "planned": None if (until_throttled or stage_levels) else count,
            "timeline": timeline,
        }
        run_start = time.monotonic()
        last_emit = 0.0

        # Pooled modes cap in-flight requests by opening worker slots instead.
        sem = asyncio.Semaphore(max_concurrency if (until_throttled or stage_levels) else concurrency)

        async def do_request(client_idx: int) -> None:
            nonlocal success, fail, last_emit, rate_limited_count, unauthorized_count
            nonlocal server_error_count, other_error_count, not_found_count
            nonlocal total_tokens_sent, prompt_tokens_sent, completion_tokens_sent
            nonlocal first_rate_limited_at_tokens, requests_before_first_429
            nonlocal seconds_to_first_429, successes_after_first_429, skipped
            nonlocal first_429_at, concurrency_at_first_429, max_tokens_per_request
            async with sem:
                if (stop_after_429s and rate_limited_count >= stop_after_429s) or stop_reason:
                    skipped += 1
                    return
                t0 = time.monotonic()
                my_stage = stage_idx
                status_class = "ok"
                effective_model = (key_models[client_idx] if key_models else None) or model
                try:
                    response = await clients[client_idx].chat.completions.create(
                        model=effective_model,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    success += 1
                    # Leakage = admitted AFTER MaaS had already throttled. A
                    # request that was in flight when the first 429 came back
                    # was admitted before it, so doesn't count.
                    if first_429_at is not None and t0 > first_429_at:
                        successes_after_first_429 += 1
                    usage = getattr(response, "usage", None)
                    tokens = getattr(usage, "total_tokens", None)
                    if isinstance(tokens, (int, float)):
                        total_tokens_sent += int(tokens)
                        max_tokens_per_request = max(max_tokens_per_request, int(tokens))
                    prompt_tokens = getattr(usage, "prompt_tokens", None)
                    if isinstance(prompt_tokens, (int, float)):
                        prompt_tokens_sent += int(prompt_tokens)
                    completion_tokens = getattr(usage, "completion_tokens", None)
                    if isinstance(completion_tokens, (int, float)):
                        completion_tokens_sent += int(completion_tokens)
                except APIStatusError as exc:
                    fail += 1
                    status_class = _status_class(exc.status_code)
                    if exc.status_code == 429:
                        rate_limited_count += 1
                        if first_rate_limited_at_tokens is None:
                            first_rate_limited_at_tokens = total_tokens_sent
                            requests_before_first_429 = success
                            seconds_to_first_429 = time.monotonic() - run_start
                            first_429_at = time.monotonic()
                            concurrency_at_first_429 = current_concurrency
                    elif exc.status_code in (401, 403):
                        unauthorized_count += 1
                    elif exc.status_code == 404:
                        not_found_count += 1
                    elif exc.status_code >= 500:
                        server_error_count += 1
                    else:
                        other_error_count += 1
                    # Error reasons are for genuine failures; throttling has its
                    # own counters (and is often the expected outcome).
                    if exc.status_code != 429:
                        message = _error_message(exc)
                        error_counts[message] = error_counts.get(message, 0) + 1
                    print(
                        f"[send_requests] request failed: status={exc.status_code} {exc}",
                        flush=True,
                    )
                except Exception as exc:
                    fail += 1
                    other_error_count += 1
                    status_class = "error"
                    message = _error_message(exc)
                    error_counts[message] = error_counts.get(message, 0) + 1
                    print(f"[send_requests] request failed: {message}", flush=True)

                latency_ms = (time.monotonic() - t0) * 1000
                latencies.append(latency_ms)
                if stage_levels:
                    acc = stage_acc[my_stage]
                    acc["latencies"].append(latency_ms)
                    if status_class == "ok":
                        acc["ok"] += 1
                        acc["tokens"] += int(getattr(getattr(response, "usage", None), "total_tokens", 0) or 0)
                    elif status_class == "throttled":
                        acc["throttled"] += 1
                    else:
                        acc["errors"] += 1
                total = success + fail
                elapsed = time.monotonic() - run_start
                if len(timeline) < _TIMELINE_CAP:
                    timeline.append(
                        [round(elapsed, 3), total_tokens_sent, status_class, round(latency_ms, 1)]
                    )
                non_throttle_fail = fail - rate_limited_count
                result_data = {
                    "total_requests": total,
                    "http_attempts": http_attempts,
                    "success_count": success,
                    "fail_count": fail,
                    "rate_limited_count": rate_limited_count,
                    "unauthorized_count": unauthorized_count,
                    "server_error_count": server_error_count,
                    "not_found_count": not_found_count,
                    "other_error_count": other_error_count,
                    # The three most common failure reasons, for the run page.
                    "error_samples": [
                        {"message": m, "count": c}
                        for m, c in sorted(error_counts.items(), key=lambda kv: -kv[1])[:3]
                    ],
                    "error_rate_pct": (fail / total * 100) if total > 0 else 0.0,
                    # Errors excluding 429s — throttling by a subscription's
                    # own limit is often the *expected* outcome, not a fault.
                    "non_throttle_error_rate_pct": (
                        (non_throttle_fail / total * 100) if total > 0 else 0.0
                    ),
                    "successes_after_first_429": successes_after_first_429,
                    "throughput_rps": success / elapsed if elapsed > 0 else 0.0,
                    "token_throughput_per_sec": total_tokens_sent / elapsed if elapsed > 0 else 0.0,
                    "total_tokens_sent": total_tokens_sent,
                    "prompt_tokens_sent": prompt_tokens_sent,
                    "completion_tokens_sent": completion_tokens_sent,
                    **_percentiles(latencies),
                }
                # Only present once a 429 has actually happened — an absent
                # key (not a 0/None placeholder) is what keeps a referencing
                # assertion honestly PENDING instead of misreading "not yet
                # rate limited" as "rate limited at 0 tokens".
                if first_rate_limited_at_tokens is not None:
                    result_data["first_rate_limited_at_tokens"] = first_rate_limited_at_tokens
                    result_data["tokens_before_first_429"] = first_rate_limited_at_tokens
                    result_data["requests_before_first_429"] = requests_before_first_429
                    result_data["seconds_to_first_429"] = round(seconds_to_first_429 or 0.0, 3)
                    # With N requests in flight, up to N requests' tokens can
                    # land past the limit before MaaS starts refusing — the
                    # honest upper bound for "throttled at the limit".
                    result_data["concurrency_at_first_429"] = concurrency_at_first_429
                    result_data["allowed_overshoot"] = (
                        (concurrency_at_first_429 or 1) * max(max_tokens_per_request, 1)
                    )
                if until_throttled:
                    result_data["peak_concurrency"] = current_concurrency
                ctx.shared_state[result_key] = result_data
                if until_throttled and limit_tokens:
                    # Progress toward the limit is what this burst is about.
                    ctx.shared_state["task_progress"] = {
                        "current": min(total_tokens_sent, int(limit_tokens)),
                        "total": int(limit_tokens),
                        "unit": "tokens",
                    }
                elif until_throttled:
                    ctx.shared_state["task_progress"] = {"current": total, "total": None, "unit": "requests"}
                elif stage_levels:
                    ctx.shared_state["task_progress"] = {
                        "current": stage_idx + 1, "total": len(stage_levels), "unit": "steps",
                    }
                else:
                    ctx.shared_state["task_progress"] = {"current": total, "total": count}
                if stage_levels:
                    # The request ceiling in step mode is a safety cap, not a plan.
                    ctx.shared_state["task_summary"] = (
                        f"Step {stage_idx + 1}/{len(stage_levels)}: {current_concurrency} in flight · "
                        + _summary_line(result_data, None)
                    )
                else:
                    ctx.shared_state["task_summary"] = _summary_line(
                        result_data, None if until_throttled else count
                    )
                now = time.monotonic()
                if now - last_emit >= _DEBOUNCE_SECS:
                    last_emit = now
                    await ctx.emit_assertion_state()

        if until_throttled or stage_levels:
            n_clients = len(clients)
            requests_started = 0

            async def worker(slot: int) -> None:
                # Slots above the current concurrency level idle until the
                # ramp opens them; every worker stops once a stop reason is set.
                nonlocal requests_started, stop_reason
                while stop_reason is None:
                    if stop_after_429s and rate_limited_count >= stop_after_429s:
                        stop_reason = "throttled"
                        return
                    if slot >= current_concurrency:
                        await asyncio.sleep(0.05)
                        continue
                    if requests_started >= count:
                        stop_reason = "requests"
                        return
                    key_idx = requests_started % n_clients
                    requests_started += 1
                    await do_request(key_idx)

            async def ramp_monitor() -> None:
                # Every interval: measure the token rate, record it, and double
                # concurrency while it's below what using the limit up within
                # one window needs. Also enforces the time budget.
                nonlocal current_concurrency, stop_reason
                last_t, last_tokens = time.monotonic(), 0
                while stop_reason is None:
                    await asyncio.sleep(_RAMP_INTERVAL_S)
                    now = time.monotonic()
                    tps = (total_tokens_sent - last_tokens) / max(now - last_t, 1e-6)
                    recent = latencies[-max(current_concurrency * 4, 20):]
                    ramp_history.append({
                        "t": round(now - run_start, 1),
                        "concurrency": current_concurrency,
                        "tokens_per_s": round(tps, 1),
                        "p50_ms": round(_percentiles(recent)["p50_latency_ms"], 1),
                    })
                    last_t, last_tokens = now, total_tokens_sent
                    if now - run_start >= max_duration_s:
                        stop_reason = "time"
                        return
                    if (
                        required_tps
                        and first_429_at is None
                        and tps < required_tps * _RAMP_HEADROOM
                        and current_concurrency < max_concurrency
                    ):
                        current_concurrency = min(max_concurrency, current_concurrency * 2)
                        print(
                            f"[send_requests] {tps:,.0f} tokens/s < {required_tps:,.0f} needed — "
                            f"concurrency → {current_concurrency}",
                            flush=True,
                        )

            async def stage_controller() -> None:
                # Step load: hold each concurrency level for stage_duration_s.
                nonlocal current_concurrency, stage_idx, stop_reason
                for i, level in enumerate(stage_levels):
                    stage_idx, current_concurrency = i, level
                    ctx.shared_state["task_summary"] = (
                        f"Step {i + 1}/{len(stage_levels)}: {level} requests in flight "
                        f"for {stage_duration_s:g}s"
                    )
                    await asyncio.sleep(stage_duration_s)
                stop_reason = "stages done"

            monitor = asyncio.create_task(ramp_monitor() if until_throttled else stage_controller())
            try:
                await asyncio.gather(*(worker(i) for i in range(max_concurrency)))
            finally:
                # Also when this task itself is cancelled (Stop) — never leave
                # the ramp/step controller running after the burst is over.
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor
        else:
            if key_pool_entries:
                assignments = _distribute(count, len(key_pool_entries))
                # Round-robin across keys (key 1, key 2, …, key 1, …) rather than
                # each key's whole share back to back — with low concurrency the
                # queue order IS the send order, and interleaving is what makes
                # "do these keys share one budget?" observable at all.
                request_tasks = []
                for round_idx in range(max(assignments, default=0)):
                    for key_idx, n_reqs in enumerate(assignments):
                        if round_idx < n_reqs:
                            request_tasks.append(do_request(key_idx))
            else:
                request_tasks = [do_request(0) for _ in range(count)]
            await asyncio.gather(*request_tasks)

        result = ctx.shared_state.get(result_key)
        if stage_levels and result is not None:
            stages_out = []
            for level, acc in zip(stage_levels, stage_acc, strict=True):
                n = len(acc["latencies"])
                pct = _percentiles(acc["latencies"])
                stages_out.append({
                    "concurrency": level,
                    "requests": n,
                    "requests_per_s": round(acc["ok"] / stage_duration_s, 2),
                    "tokens_per_s": round(acc["tokens"] / stage_duration_s, 1),
                    "p50_latency_ms": round(pct["p50_latency_ms"], 1),
                    "p95_latency_ms": round(pct["p95_latency_ms"], 1),
                    "p99_latency_ms": round(pct["p99_latency_ms"], 1),
                    "error_rate_pct": round(acc["errors"] / n * 100, 2) if n else 0.0,
                    "throttled_pct": round(acc["throttled"] / n * 100, 2) if n else 0.0,
                })
            result["stages"] = stages_out
            # Pass/fail is judged at the heaviest step — the load being tested.
            final = stages_out[-1]
            result["final_stage_p99_latency_ms"] = final["p99_latency_ms"]
            result["final_stage_error_rate_pct"] = final["error_rate_pct"]
            result["final_stage_throttled_pct"] = final["throttled_pct"]
            best = max(stages_out, key=lambda st: st["requests_per_s"])
            ctx.shared_state["task_summary"] = (
                f"{len(stages_out)} steps up to {final['concurrency']} in flight · peak "
                f"{best['requests_per_s']:g} req/s at {best['concurrency']} · p95 at the top step "
                f"{final['p95_latency_ms']:,.0f} ms · errors {final['error_rate_pct']:g}%"
            )
        if count <= 0:
            ctx.shared_state["task_summary"] = "No requests configured (count: 0) — skipped"
        elif until_throttled and result is not None:
            result["ramp"] = ramp_history
            result["peak_concurrency"] = max(
                [h["concurrency"] for h in ramp_history] + [current_concurrency]
            )
            if required_tps:
                result["required_tokens_per_s"] = round(required_tps, 1)
            if first_429_at is None:
                bound, why = _diagnose_unthrottled(
                    ramp_history, required_tps, max_concurrency, stop_reason or "unknown",
                    limit_tokens, window_text or "the window",
                )
                result["limit_reached"] = 0
                result["not_throttled_bound"] = bound
                ctx.shared_state.setdefault("_findings", []).append(
                    {"title": "Couldn't reach the limit", "text": why, "outcome": "inconclusive"}
                )
                ctx.shared_state["_verdict_text"] = f"Inconclusive — couldn't reach the limit. {why}"
                ctx.shared_state["task_summary"] = (
                    _summary_line(result, None) + f" · never throttled ({bound}-bound)"
                )
            else:
                result["limit_reached"] = 1
                ctx.shared_state["task_summary"] = (
                    _summary_line(result, None)
                    + f" · sent until throttled at concurrency {concurrency_at_first_429}"
                )
        elif skipped and not stage_levels:
            ctx.shared_state["task_summary"] = (
                _summary_line(ctx.shared_state[result_key], count)
                + f" · stopped early after {stop_after_429s} throttled requests"
            )
        await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def _resolve_url_model_and_token(self, ctx: TaskContext) -> tuple[str, str, str]:
        url = self.params.get("url") or ctx.shared_state.get("url")
        token = self.params.get("token") or ctx.shared_state.get("token") or ctx.sa_token
        model_param = self.params.get("model", "")
        default_model = str(
            (model_param if model_param and model_param.strip("/") else None)
            or ctx.config.get("target_model")
            or ctx.config.get("DEFAULT_MODEL", "granite-3-8b-instruct")
        )
        if url:
            return str(url), default_model, str(token)
        base_url, resolved_model = await self._discover_model(ctx, default_model)
        return base_url, resolved_model, str(token)

    async def _discover_model(self, ctx: TaskContext, want: str) -> tuple[str, str]:
        """Return (base_url, model_id) by querying /v1/models.

        Matches want against m['id'], m['modelDetails']['displayName'], or
        m['owned_by'] ("<namespace>/<MaaSModelRef name>", confirmed live).
        `owned_by` is the one field that reliably matches a scenario's own
        `target_model_namespace`/`target_model_name` config — `id` and
        `displayName` are cosmetic/internal and don't need to (and, confirmed
        live, may not) resemble the CR name at all. Falls through to first
        available only if none of the three match, and that fallback is
        genuinely risky once more than one model is registered (confirmed
        live: an unrelated ExternalModel sorting first in the list silently
        hijacked a scenario that had a target model configured but never
        wired it through to this match) — always pass `model:` explicitly for
        any scenario that cares which model it hits, don't rely on this.
        The returned model_id is always the canonical m['id'] from the discovery
        response, not the want string — ensures the inference call uses the ID
        the endpoint actually recognises.
        """
        import httpx

        discovery_url = f"{ctx.maas_api_url}/v1/models"
        print(
            f"[send_requests] GET {discovery_url} (token={_redact(ctx.sa_token)})",
            flush=True,
        )
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                discovery_url,
                headers={"Authorization": f"Bearer {ctx.sa_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

        print(f"[send_requests] discovery response: {data}", flush=True)

        def _base(url: str) -> str:
            b = url.rstrip("/")
            return b if b.endswith("/v1") else f"{b}/v1"

        for m in data.get("data", []):
            display = (m.get("modelDetails") or {}).get("displayName", "")
            owned_by = m.get("owned_by", "")
            if m["id"] == want or display == want or owned_by == want:
                base = _base(m["url"])
                print(
                    f"[send_requests] matched model id={m['id']} display={display!r} "
                    f"owned_by={owned_by!r} base_url={base}",
                    flush=True,
                )
                return base, m["id"]

        if data.get("data"):
            first = data["data"][0]
            base = _base(first["url"])
            print(
                f"[send_requests] {want!r} not matched; using first available "
                f"id={first['id']} base_url={base}",
                flush=True,
            )
            return base, first["id"]

        fallback = f"{ctx.maas_api_url}/v1"
        print(
            f"[send_requests] no models in discovery response; falling back to {fallback} model={want}",
            flush=True,
        )
        return fallback, want

    def _resolve_key_pool_entries(self, ctx: TaskContext) -> list[dict]:
        if not self.params.get("key_pool"):
            return []
        keys = list(ctx.shared_state.get("api_keys", []))
        # `key_pool_filter`: "active" = keys not revoked mid-run, "revoked" =
        # only those — e.g. revoke 1 of 3 keys, then check the other 2 still
        # work and the revoked one is denied (scenarios/api_key_lifecycle.yaml).
        pool_filter = self.params.get("key_pool_filter")
        if pool_filter == "active":
            keys = [k for k in keys if not k.get("revoked")]
        elif pool_filter == "revoked":
            keys = [k for k in keys if k.get("revoked")]
        return keys

    async def _resolve_models_by_subscription(
        self, ctx: TaskContext, key_pool_entries: list[dict]
    ) -> dict[int, str]:
        """Map each key-pool index to a model id it's actually eligible for,
        by cross-referencing the key's own bound `subscription` (echoed by
        every provision_api_key/provision_keys_for_users/provision_keys_
        distributed create response) against /v1/models' per-model
        `subscriptions: [{name}]` list. A key whose subscription isn't
        listed under any model is left unresolved — falls back to whatever
        the default `model` param/discovery already resolves to, logged
        rather than silently mismatched.
        """
        import httpx

        discovery_url = f"{ctx.maas_api_url}/v1/models"
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                discovery_url,
                headers={"Authorization": f"Bearer {ctx.sa_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

        sub_to_model: dict[str, str] = {}
        for m in data.get("data", []):
            for sub in m.get("subscriptions") or []:
                sub_to_model.setdefault(sub.get("name"), m["id"])

        resolved: dict[int, str] = {}
        for i, entry in enumerate(key_pool_entries):
            sub_name = entry.get("subscription")
            if not sub_name:
                continue
            model_id = sub_to_model.get(sub_name)
            if model_id:
                resolved[i] = model_id
            else:
                print(
                    f"[send_requests] key subscription={sub_name!r} not listed under "
                    "any model in /v1/models — falling back to the default model",
                    flush=True,
                )
        return resolved

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


REGISTRY["send_requests"] = SendRequestsTask
# Registry alias, not a new class (ADR-019): scenarios/api_key_lifecycle.yaml
# needs a second, differently-labeled "send some requests" step after
# revoking the key pool, to confirm denial is immediate. Giving a second YAML
# task entry the *same* registered name ("send_requests" twice) would collide
# in the UI's task pipeline and progress tracking — harness/runner.py's
# _write_progress keys per-task completed-progress by task name, and
# ui/src/components/TaskProgress.tsx uses task.name as the React list key —
# so this needs its own registry entry, not just a repeated YAML task name.
REGISTRY["verify_revoked_key_denied"] = SendRequestsTask
# Same alias trick, same reason (ADR-019), new use (ADR-023):
# scenarios/rate_limit_shared_across_users.yaml needs a second, differently-
# labeled "send some requests" step (targeting a second user's key via
# key_index) so the two bursts get distinct UI task-pipeline/progress chips
# instead of colliding on the shared "send_requests" task name.
REGISTRY["send_requests_as_second_user"] = SendRequestsTask
# More aliases, same reason: a second "send some requests" step in one
# scenario needs its own chip. scenarios/rate_limit_window_recovery.yaml
# sends again after waiting out the window; scenarios/gateway_overhead.yaml
# sends the same burst via MaaS after sending it direct.
REGISTRY["send_requests_after_window"] = SendRequestsTask
REGISTRY["send_requests_via_maas"] = SendRequestsTask
# scenarios/api_key_lifecycle.yaml: after revoking one key, the others must still work.
REGISTRY["verify_other_keys_still_work"] = SendRequestsTask
