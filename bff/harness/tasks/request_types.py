import contextlib
import time
from dataclasses import dataclass

import httpx

from harness.result import TaskResult

# Module imports, not `from ... import Name` — see harness/tasks/subscription_check.py.
from harness.tasks import inference as _inference
from harness.tasks import model as _model
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

_DIRECT_PORT = 8000
_DIRECT_CHECK_TIMEOUT_S = 5.0
# Per-request timeout. Generous for a ~300-word answer from a real model,
# but well under the ~60 s a MaaS gateway can hold an error reply before
# dropping the connection (confirmed live, see attribute()).
_DEFAULT_REQUEST_TIMEOUT_S = 30.0
# A placeholder bearer for direct calls: the model's own service doesn't
# check MaaS keys, and a real key has no business leaving the MaaS path.
_DIRECT_TOKEN = "maaspal-direct"


@dataclass(frozen=True)
class RequestType:
    id: str
    label: str
    path: str
    # An OpenAI SDK endpoint (inference.API_PATHS) sent through
    # SendRequestsTask, or None for a raw-HTTP probe (see _probe_body).
    api: str | None = None
    stream: bool = False
    batch: bool = False


REQUEST_TYPES = [
    RequestType("chat", "Chat completions", "/v1/chat/completions", "chat_completions"),
    RequestType("chat_stream", "Chat completions (streamed)", "/v1/chat/completions", "chat_completions", True),
    RequestType("completions", "Completions", "/v1/completions", "completions"),
    RequestType("completions_stream", "Completions (streamed)", "/v1/completions", "completions", True),
    RequestType("completions_batch", "Completions (batched prompts)", "/v1/completions", "completions", batch=True),
    RequestType("responses", "Responses", "/v1/responses", "responses"),
    RequestType("responses_stream", "Responses (streamed)", "/v1/responses", "responses", True),
    RequestType("embeddings", "Embeddings", "/v1/embeddings", "embeddings"),
    # vLLM also serves these (docs.vllm.ai, online serving); not OpenAI SDK
    # calls, so sent as plain HTTP. Request bodies not yet confirmed live.
    RequestType("messages", "Anthropic messages", "/v1/messages"),
    RequestType("tokenize", "Tokenize", "/tokenize"),
    RequestType("rerank", "Rerank", "/v1/rerank"),
]
_BY_ID = {t.id: t for t in REQUEST_TYPES}


def selected_types(value: object) -> list[RequestType]:
    """`types`: a comma list of ids, or blank/"all" for every type."""
    text = str(value or "").strip()
    if not text or text.lower() == "all":
        return list(REQUEST_TYPES)
    ids = [s.strip() for s in text.split(",") if s.strip()]
    unknown = [i for i in ids if i not in _BY_ID]
    if unknown:
        raise ValueError(f"Unknown request type(s) {', '.join(unknown)} — one of {', '.join(_BY_ID)}")
    return [_BY_ID[i] for i in ids]


def _probe_body(t: RequestType, model: str, prompt: str) -> dict:
    if t.id == "messages":
        return {"model": model, "max_tokens": 32, "messages": [{"role": "user", "content": prompt}]}
    if t.id == "tokenize":
        return {"model": model, "prompt": prompt}
    return {"model": model, "query": prompt, "documents": [prompt, "Something unrelated."]}


def _root(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


async def _probe(
    t: RequestType, base_url: str, model: str, token: str, count: int, prompt: str, insecure: bool,
    timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
) -> dict:
    """N plain-HTTP requests to a non-SDK endpoint, summarised in the same
    shape SendRequestsTask writes, so both kinds attribute the same way.
    Stops after the first request that gets no HTTP answer at all."""
    url = _root(base_url) + t.path
    r: dict = {
        "total_requests": 0, "success_count": 0, "fail_count": 0, "rate_limited_count": 0,
        "unauthorized_count": 0, "not_found_count": 0, "server_error_count": 0,
        "other_error_count": 0, "total_tokens_sent": 0, "prompt_tokens_sent": 0,
        "completion_tokens_sent": 0, "transport_error_count": 0, "failure_origins": {},
        "failure_origin_evidence": {},
    }
    latencies: list[float] = []
    errors: dict[str, int] = {}
    error_ms: dict[str, list[float]] = {}
    # [t_offset_s, cumulative_tokens, status_class, latency_ms] — the same
    # shape SendRequestsTask records, for the run page's traffic card.
    timeline: list[list] = []
    start = time.monotonic()
    headers = {"Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01"}
    async with httpx.AsyncClient(verify=not insecure, timeout=timeout_s) as client:
        for _ in range(count):
            t0 = time.monotonic()
            r["total_requests"] += 1
            try:
                resp = await client.post(url, json=_probe_body(t, model, prompt), headers=headers)
            except httpx.HTTPError as exc:
                r["fail_count"] += 1
                r["other_error_count"] += 1
                r["transport_error_count"] += 1
                took_ms = (time.monotonic() - t0) * 1000
                msg = f"{type(exc).__name__}: {str(exc)[:100]}"
                errors[msg] = errors.get(msg, 0) + 1
                error_ms.setdefault(msg, []).append(took_ms)
                timeline.append([round(time.monotonic() - start, 3), r["total_tokens_sent"], "error", round(took_ms, 1)])
                break
            latency_ms = (time.monotonic() - t0) * 1000
            latencies.append(latency_ms)
            if resp.status_code < 400:
                r["success_count"] += 1
                usage: dict = {}
                with contextlib.suppress(ValueError, AttributeError):
                    usage = (resp.json() or {}).get("usage") or {}
                prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                r["prompt_tokens_sent"] += prompt_tokens
                r["completion_tokens_sent"] += completion_tokens
                r["total_tokens_sent"] += int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
                timeline.append(
                    [round(time.monotonic() - start, 3), r["total_tokens_sent"], "ok", round(latency_ms, 1)]
                )
                continue
            r["fail_count"] += 1
            status = resp.status_code
            if status == 429:
                r["rate_limited_count"] += 1
            elif status in (401, 403):
                r["unauthorized_count"] += 1
            elif status == 404:
                r["not_found_count"] += 1
            elif status >= 500:
                r["server_error_count"] += 1
            else:
                r["other_error_count"] += 1
            body = resp.text.strip().replace("\n", " ")
            msg = f"HTTP {status} {resp.reason_phrase}".strip() + (f": {body[:80]}" if body else "")
            errors[msg] = errors.get(msg, 0) + 1
            origin, evidence = _inference.failure_origin(status, dict(resp.headers), resp.text)
            r["failure_origins"][origin] = r["failure_origins"].get(origin, 0) + 1
            r["failure_origin_evidence"].setdefault(origin, evidence)
            timeline.append([
                round(time.monotonic() - start, 3), r["total_tokens_sent"],
                _inference._status_class(status), round(latency_ms, 1),
            ])
    r["timeline"] = timeline
    r["error_samples"] = [
        {"message": m, "count": c, **_inference._latency_spread(error_ms.get(m) or [])}
        for m, c in sorted(errors.items(), key=lambda kv: -kv[1])[:3]
    ]
    r.update(_inference._percentiles(latencies))
    return r


def _outcome_text(r: dict | None) -> str:
    if not r:
        return "—"
    ok, total = r.get("success_count", 0), r.get("total_requests", 0)
    text = f"{ok}/{total} OK"
    samples = r.get("error_samples") or []
    if ok < total and samples:
        text += f" — {samples[0]['count']}× {samples[0]['message']}"
    return text


def _http_failed(r: dict) -> bool:
    """Every request failed with a real HTTP answer that isn't an auth
    refusal — i.e. the endpoint itself answered "no"."""
    # failure_origins counts every failure that got an HTTP response (not
    # other_error_count, which mixes connection errors with e.g. 400s).
    http_fails = sum((r.get("failure_origins") or {}).values()) - r.get("unauthorized_count", 0)
    return r.get("success_count", 0) == 0 and http_fails > 0


def maas_hung(maas: dict) -> bool:
    """Through MaaS nothing came back at all — every failure a timeout or a
    dropped connection, no HTTP status. Confirmed live (2026-10-05): when the
    model answers with an error (no usage in the body), Kuadrant's wasm-shim
    fails to read /usage/total_tokens and the gateway holds the reply ~60 s,
    then drops the connection — for every subscription except the one whose
    limit sorts last in the model's TokenRateLimitPolicy."""
    return (
        maas.get("success_count", 0) == 0
        and maas.get("transport_error_count", 0) > 0
        and not maas.get("failure_origins")
    )


def _took(r: dict) -> str:
    samples = r.get("error_samples") or []
    median = samples[0].get("median_ms") if samples else None
    return f" after {median / 1000:,.0f} s" if median and median >= 1000 else ""


def attribute(maas: dict, direct: dict | None) -> tuple[str, str, str]:
    """(outcome, failed at, why) for one request type. A direct call to the
    model, when it worked, decides; otherwise the MaaS-side failures' own
    origin markers (inference.failure_origin) give a "likely" answer."""
    if maas_hung(maas) and direct is not None and _http_failed(direct):
        return (
            "unsupported_by_model",
            "model (and MaaS hung)",
            f"The model answers {_outcome_text(direct)}, but through MaaS that answer never came back — "
            f"the connection was dropped{_took(maas)}",
        )
    if maas.get("success_count", 0) > 0:
        why = ""
        if direct is not None and direct.get("success_count", 0) == 0:
            why = f"Works through MaaS; the direct call got {_outcome_text(direct)}"
        return "supported", "—", why
    if direct is not None and direct.get("success_count", 0) > 0:
        return "blocked_by_maas", "MaaS gateway", f"Works when calling the model directly ({_outcome_text(direct)})"
    if direct is not None and _http_failed(direct):
        return "unsupported_by_model", "model", f"Fails when calling the model directly too ({_outcome_text(direct)})"
    origins = maas.get("failure_origins") or {}
    if origins:
        origin = max(origins, key=lambda k: origins[k])
        evidence = (maas.get("failure_origin_evidence") or {}).get(origin, "")
        if origin == "gateway":
            return "blocked_by_maas", "MaaS gateway (likely)", evidence
        if origin == "model":
            return "unsupported_by_model", "model (likely)", evidence
    return "unclear", "unknown", "No HTTP answer to tell who refused it" if not origins else "No marker of who answered"


_OUTCOME_MARK = {
    "supported": "✓ supported",
    "blocked_by_maas": "✗ blocked by MaaS",
    "unsupported_by_model": "✗ not supported by the model",
    "unclear": "✗ failed, cause unclear",
}


class SendRequestsEachTypeTask(Task):
    """Tries each request type (chat, completions, responses, embeddings,
    streamed and batched forms, and a few other vLLM endpoints) N times
    through MaaS and — when the model's in-cluster service is reachable —
    directly, then says which work and, for each that doesn't, whether MaaS
    or the model refused it. A type failing never fails the task: the point
    is the report (a "Request types" table and a finding)."""

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        api_keys = ctx.shared_state.get("api_keys") or []
        if not api_keys:
            raise RuntimeError("No API key to send with — run provision_api_key first")
        key = api_keys[-1]["key"]
        types = selected_types(self.params.get("types"))
        count = int(self.params.get("requests_per_type", 3))
        prompt = str(self.params.get("prompt", "Hello, world!"))
        batch_size = max(2, int(self.params.get("batch_size") or 4))
        timeout_s = float(self.params.get("request_timeout_s") or _DEFAULT_REQUEST_TIMEOUT_S)
        model_name = str(self.params.get("model_name") or "")
        model_namespace = str(self.params.get("model_namespace") or "")
        compare_direct = _inference._truthy(self.params.get("compare_direct", True))

        resolver = _inference.SendRequestsTask(
            self.name, {"model": f"{model_namespace}/{model_name}", "token": key}
        )
        maas_url, model_id, _ = await resolver._resolve_url_model_and_token(ctx)

        direct_url, direct_note, direct_model = None, "", None
        if not compare_direct:
            direct_note = "Direct comparison turned off"
        elif not (model_name and model_namespace):
            direct_note = "No model picked, so the model couldn't be called directly"
        else:
            candidate = (
                f"https://{_model._workload_service(model_name)}.{model_namespace}.svc:{_DIRECT_PORT}/v1"
            )
            direct_url, direct_note, direct_model = await _check_direct(candidate)
        print(f"[{self.name}] via MaaS: {maas_url} model={model_id}; direct: {direct_url or direct_note}", flush=True)

        counts = {
            "selected_count": len(types), "tried_count": 0, "supported_count": 0,
            "blocked_by_maas_count": 0, "unsupported_by_model_count": 0, "unclear_count": 0,
        }
        ctx.shared_state["request_types"] = counts
        rows: list[list[str]] = []
        table = {
            "columns": ["Request type", "Endpoint", "Via MaaS", "Direct", "Outcome", "Failed at", "Why",
                        "Latency (p50)", "First token (p50)", "Per token (p50)", "Delivery", "Tokens"],
            "rows": rows,
        }
        ctx.shared_state.setdefault("_tables", {})["Request types"] = table
        by_outcome: dict[str, list[str]] = {k: [] for k in _OUTCOME_MARK}
        held_back: list[str] = []
        hung: list[str] = []

        for i, t in enumerate(types):
            maas = await self._send(
                ctx, t, maas_url, model_id, key, count, prompt, batch_size, False, "", timeout_s
            )
            direct = None
            if direct_url:
                direct = await self._send(
                    ctx, t, direct_url, direct_model or model_id, _DIRECT_TOKEN, count, prompt, batch_size, True,
                    "_direct", timeout_s,
                )
            outcome, failed_at, why = attribute(maas, direct)
            if maas_hung(maas):
                hung.append(t.label)
            counts["tried_count"] += 1
            counts[f"{outcome}_count"] += 1
            by_outcome[outcome].append(t.label)
            ttft = maas.get("p50_ttft_ms")
            delivery = _delivery(t, maas, direct) if maas.get("success_count") else "—"
            if delivery.startswith("⚠"):
                held_back.append(f"{t.label}: {delivery[2:]}")
            rows.append([
                t.label,
                t.path,
                _outcome_text(maas),
                _outcome_text(direct) if direct_url else direct_note,
                _OUTCOME_MARK[outcome],
                failed_at,
                why,
                f"{maas.get('p50_latency_ms', 0):,.0f} ms" if maas.get("success_count") else "—",
                f"{ttft:,.0f} ms" if ttft is not None else "—",
                _per_token_text(maas),
                delivery,
                f"{maas.get('total_tokens_sent', 0):,}",
            ])
            ctx.shared_state["task_progress"] = {"current": i + 1, "total": len(types), "unit": "types"}
            ctx.shared_state["task_summary"] = (
                f"{counts['supported_count']} of {i + 1} request type(s) work through MaaS"
            )
            await ctx.emit_assertion_state()

        sentence = _finding_text(by_outcome, len(types))
        if direct_note and not direct_url:
            sentence += f" ({direct_note}; failures attributed from the responses alone.)"
        if hung:
            sentence += (
                " Through MaaS these got no answer at all — the connection hung until it was dropped: "
                + ", ".join(hung) + "."
            )
        if held_back:
            sentence += " Streamed replies that arrived all at once: " + "; ".join(held_back) + "."
        ctx.shared_state.setdefault("_findings", []).append(
            {"title": "Request types", "text": sentence, "outcome": "info"}
        )
        ctx.shared_state["_verdict_text"] = sentence
        ctx.shared_state["task_summary"] = sentence
        await ctx.emit_assertion_state()
        return TaskResult(task_name=self.name, status="PASS", duration_ms=(time.monotonic() - start) * 1000)

    async def _send(
        self, ctx: TaskContext, t: RequestType, url: str, model: str, token: str,
        count: int, prompt: str, batch_size: int, direct: bool, suffix: str, timeout_s: float,
    ) -> dict:
        result_key = f"request_type_{t.id}{suffix}"
        if t.api is None:
            t0 = time.time()
            r = await _probe(t, url, model, token, count, prompt, insecure=direct, timeout_s=timeout_s)
            timeline = r.pop("timeline")
            ctx.shared_state[result_key] = r
            if not direct:
                # A traffic card like every SDK-sent type gets, so the run
                # page lists all the types it tried, not only the SDK ones.
                ctx.shared_state.setdefault("_traffic", {})[result_key] = {
                    "chart": False, "chart_group": None, "label": t.label, "t0": t0,
                    "task": self.name, "planned": count, "timeline": timeline,
                }
            return r
        await _inference.SendRequestsTask(
            self.name,
            {
                "url": url, "model": model, "token": token, "count": count, "concurrency": 1,
                "prompt": prompt, "retries": 0, "result_key": result_key, "api": t.api,
                "stream": t.stream, "batch_size": batch_size if t.batch else 1,
                "insecure_tls": direct, "label": t.label, "timeout_s": timeout_s,
                # One hang is enough to report; don't wait it out N times.
                "stop_after_transport_errors": 1,
            },
        ).run(ctx)
        traffic = ctx.shared_state.get("_traffic") or {}
        if direct:
            # The table already compares both; one traffic card per type is enough.
            traffic.pop(result_key, None)
        return ctx.shared_state.get(result_key) or {}

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


async def _check_direct(url: str) -> tuple[str | None, str, str | None]:
    """(url, "", served model name) when the model's own service answers,
    else (None, why not, None) — a NetworkPolicy in the model's namespace
    commonly blocks the harness. The served name matters: MaaS lists a model
    under its own id (e.g. "publishers/llm/models/facebook/opt-125m") and
    rewrites it on the way in, while the model server only knows its own
    ("facebook/opt-125m") — confirmed live."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=_DIRECT_CHECK_TIMEOUT_S) as client:  # noqa: S501
            resp = await client.get(f"{url}/models")
    except httpx.HTTPError as exc:
        return None, f"The model's service wasn't reachable directly ({type(exc).__name__})", None
    if resp.status_code in (401, 403):
        return None, f"The model's service needs its own credentials (HTTP {resp.status_code})", None
    served: str | None = None
    with contextlib.suppress(ValueError, AttributeError, IndexError, KeyError, TypeError):
        served = str(resp.json()["data"][0]["id"])
    return url, "", served


def _per_token_text(r: dict) -> str:
    """Time per output token. Streamed: TPOT — (last text chunk − first) ÷
    (output tokens − 1), the decode speed once text started — plus ITL, the
    real gaps between chunks (p50/p95, so jitter shows). Not streamed there's
    nothing to time per token; latency ÷ tokens is shown as a rough upper
    bound, since it also includes the wait for the first token."""
    if "p50_tpot_ms" in r:
        text = f"TPOT {r['p50_tpot_ms']:,.1f} ms"
        if "p50_itl_ms" in r:
            text += f" · ITL p50 {r['p50_itl_ms']:,.1f} / p95 {r['p95_itl_ms']:,.1f} ms"
        return text
    if "p50_ms_per_output_token" in r:
        return f"≤ {r['p50_ms_per_output_token']:,.1f} ms (latency ÷ tokens; stream to measure)"
    return "—"


def _delivery(t: RequestType, maas: dict, direct: dict | None) -> str:
    """inference.delivery_text for the MaaS leg, plus — when the text came
    back in one piece — who held it back, judged by the direct call."""
    if not t.stream:
        return "One response (not streamed)"
    text = _inference.delivery_text(maas)
    if not text.startswith("⚠") or not direct or not direct.get("success_count"):
        return text
    if direct.get("streamed_reply_count", 0) > direct.get("all_at_once_reply_count", 0):
        return text + " — the model streams it, so MaaS held it back"
    return text + " — the model sends it all at once too (MaaS isn't the cause)"


def _finding_text(by_outcome: dict[str, list[str]], total: int) -> str:
    parts = [f"{len(by_outcome['supported'])} of {total} request types work through MaaS."]
    if by_outcome["blocked_by_maas"]:
        parts.append("Blocked by MaaS: " + ", ".join(by_outcome["blocked_by_maas"]) + ".")
    if by_outcome["unsupported_by_model"]:
        parts.append("Not supported by the model: " + ", ".join(by_outcome["unsupported_by_model"]) + ".")
    if by_outcome["unclear"]:
        parts.append("Failed, cause unclear: " + ", ".join(by_outcome["unclear"]) + ".")
    return " ".join(parts)


REGISTRY["send_requests_each_type"] = SendRequestsEachTypeTask
