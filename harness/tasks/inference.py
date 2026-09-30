import asyncio
import time

from openai import APIStatusError, AsyncOpenAI

from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

_DEBOUNCE_SECS = 0.1


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
            # ADR-018's third Update: targets a model deploy_simulated_model
            # (harness/tasks/model.py) just created at runtime, the same
            # shared_state-lookup reason key_index exists above.
            models = ctx.shared_state.get(model_from_shared_state, [])
            if models:
                self.params["model"] = f"{models[0]['namespace']}/{models[0]['name']}"

        url, model, token = await self._resolve_url_model_and_token(ctx)
        key_pool_entries = self._resolve_key_pool_entries(ctx)

        if key_pool_entries:
            key_strings = [e["key"] for e in key_pool_entries]
            key_models = [e.get("target_model") for e in key_pool_entries]
            clients = [AsyncOpenAI(api_key=k, base_url=url) for k in key_strings]
            print(
                f"[send_requests] base_url={url} model={model} "
                f"keys=[{', '.join(_redact(k) for k in key_strings)}] "
                f"count={count} concurrency={concurrency}",
                flush=True,
            )
        else:
            key_models = []
            clients = [AsyncOpenAI(api_key=token, base_url=url)]
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
        total_tokens_sent = 0
        prompt_tokens_sent = 0
        completion_tokens_sent = 0
        run_start = time.monotonic()
        last_emit = 0.0

        sem = asyncio.Semaphore(concurrency)

        async def do_request(client_idx: int) -> None:
            nonlocal success, fail, last_emit, rate_limited_count, unauthorized_count
            nonlocal total_tokens_sent, prompt_tokens_sent, completion_tokens_sent
            async with sem:
                t0 = time.monotonic()
                effective_model = (key_models[client_idx] if key_models else None) or model
                try:
                    response = await clients[client_idx].chat.completions.create(
                        model=effective_model,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    success += 1
                    usage = getattr(response, "usage", None)
                    tokens = getattr(usage, "total_tokens", None)
                    if isinstance(tokens, (int, float)):
                        total_tokens_sent += int(tokens)
                    prompt_tokens = getattr(usage, "prompt_tokens", None)
                    if isinstance(prompt_tokens, (int, float)):
                        prompt_tokens_sent += int(prompt_tokens)
                    completion_tokens = getattr(usage, "completion_tokens", None)
                    if isinstance(completion_tokens, (int, float)):
                        completion_tokens_sent += int(completion_tokens)
                except APIStatusError as exc:
                    fail += 1
                    if exc.status_code == 429:
                        rate_limited_count += 1
                    elif exc.status_code in (401, 403):
                        unauthorized_count += 1
                    print(
                        f"[send_requests] request failed: status={exc.status_code} {exc}",
                        flush=True,
                    )
                except Exception as exc:
                    fail += 1
                    print(f"[send_requests] request failed: {exc}", flush=True)

                latencies.append((time.monotonic() - t0) * 1000)
                total = success + fail
                elapsed = time.monotonic() - run_start
                ctx.shared_state[result_key] = {
                    "total_requests": total,
                    "success_count": success,
                    "fail_count": fail,
                    "rate_limited_count": rate_limited_count,
                    "unauthorized_count": unauthorized_count,
                    "error_rate_pct": (fail / total * 100) if total > 0 else 0.0,
                    "throughput_rps": success / elapsed if elapsed > 0 else 0.0,
                    "token_throughput_per_sec": total_tokens_sent / elapsed if elapsed > 0 else 0.0,
                    "total_tokens_sent": total_tokens_sent,
                    "prompt_tokens_sent": prompt_tokens_sent,
                    "completion_tokens_sent": completion_tokens_sent,
                    **_percentiles(latencies),
                }
                ctx.shared_state["task_progress"] = {"current": total, "total": count}
                now = time.monotonic()
                if now - last_emit >= _DEBOUNCE_SECS:
                    last_emit = now
                    await ctx.emit_assertion_state()

        if key_pool_entries:
            assignments = _distribute(count, len(key_pool_entries))
            request_tasks = []
            for key_idx, n_reqs in enumerate(assignments):
                for _ in range(n_reqs):
                    request_tasks.append(do_request(key_idx))
        else:
            request_tasks = [do_request(0) for _ in range(count)]

        await asyncio.gather(*request_tasks)
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
        return list(ctx.shared_state.get("api_keys", []))

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
