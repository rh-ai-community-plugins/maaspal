import time

import httpx

from harness.durations import parse_duration_s
from harness.result import TaskResult

# Module imports, not `from ... import Name`: harness/tasks/registry.py imports
# every task module, and auth/inference import the registry back — whichever
# module loads first, the others can be half-initialized at this point.
# Attributes are looked up at call time below, once everything has loaded.
from harness.tasks import auth as _auth
from harness.tasks import inference as _inference
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

# Probing a limit means sending until the first 429 — only sensible when the
# configured budget is small enough to exhaust quickly, and the window long
# enough that a short sequential burst can't straddle a reset.
_DEFAULT_PROBE_MAX_TOKENS = 1000
_DEFAULT_PROBE_MAX_REQUESTS = 40
_MIN_PROBE_WINDOW_S = 10.0
# Same allowance scenarios/verify_subscription_rate_limit.yaml uses: with
# concurrency 1, at most one in-flight request can push usage past the limit.
_DEFAULT_SPILLOVER_TOKENS = 100


def _find_subscription(name: str) -> dict:
    """The subscription as the MaaS Setup tab shows it (api/maas_client.py —
    same image, same ServiceAccount), including each modelRef's configured
    tokenRateLimits."""
    from api.maas_client import list_subscriptions

    result = list_subscriptions()
    if not result.available:
        raise RuntimeError(
            f"Couldn't read MaaSSubscriptions ({result.reason}) — check deploy/rbac-maas-readonly.yaml"
        )
    matches = [s for s in result.items if s["name"] == name]
    if not matches:
        raise RuntimeError(f"Subscription {name!r} not found on this cluster")
    return matches[0]


async def _discovered_models(ctx: TaskContext) -> dict[str, tuple[str, str]]:
    """owned_by ("<namespace>/<MaaSModelRef name>") → (base_url, model id)
    from GET /v1/models — the join key back to a subscription's modelRefs."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ctx.maas_api_url}/v1/models",
            headers={"Authorization": f"Bearer {ctx.sa_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
    out = {}
    for m in data.get("data", []):
        base = str(m.get("url", "")).rstrip("/")
        out[str(m.get("owned_by", ""))] = (base if base.endswith("/v1") else f"{base}/v1", m["id"])
    return out


class VerifySubscriptionModelsTask(Task):
    """"Does my subscription work for every model it covers?" — mints one API
    key pinned to an existing subscription, then for each model in its
    modelRefs: sends a short burst to confirm the model is actually reachable
    with that key, and — when the configured token limit is small enough to
    exhaust quickly — keeps sending until the first 429 to confirm the limit
    MaaS enforces is the one the subscription declares. One row per model in
    the run page's "Subscription models" table.

    A key is bound to a subscription, not a model, so one key covers every
    model; the gateway keeps a separate token counter per (subscription,
    model), so each model's limit is probed independently.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        sub_name = str(self.params.get("subscription") or "")
        if not sub_name:
            raise RuntimeError("Pick a subscription to verify")
        requests_per_model = int(self.params.get("requests_per_model", 3))
        probe_max_tokens = int(self.params.get("probe_limit_max_tokens", _DEFAULT_PROBE_MAX_TOKENS))
        probe_max_requests = int(self.params.get("probe_max_requests", _DEFAULT_PROBE_MAX_REQUESTS))
        spillover = int(self.params.get("spillover_tokens", _DEFAULT_SPILLOVER_TOKENS))
        prompt = str(self.params.get("prompt", "Hello, world!"))

        sub = _find_subscription(sub_name)
        model_refs = sub["model_refs"]
        print(
            f"[verify_subscription_models] {sub['namespace']}/{sub_name}: "
            f"{len(model_refs)} model(s), priority={sub.get('priority')}",
            flush=True,
        )

        key_task = _auth.ProvisionApiKeyTask(
            self.name,
            {"key_name": f"maaspal-verify-{ctx.run_id[:8]}", "subscription": sub_name},
        )
        try:
            await key_task.run(ctx)
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Couldn't create an API key on subscription {sub_name!r} "
                f"({exc.response.status_code}: {exc.response.text[:200]}) — is this harness's "
                f"identity one of its owners ({', '.join(sub['owner']['groups'] + sub['owner']['users'])})?"
            ) from exc
        key = ctx.shared_state["api_keys"][-1]["key"]
        discovered = await _discovered_models(ctx)

        counts = {
            "model_count": len(model_refs),
            "reachable_count": 0,
            "probed_count": 0,
            "enforced_count": 0,
        }
        rows: list[list[str]] = []

        for i, ref in enumerate(model_refs):
            ref_id = f"{ref['namespace']}/{ref['name']}"
            limits = ref.get("token_rate_limits") or []
            limit = int(limits[0]["limit"]) if limits else None
            window = str(limits[0].get("window", "")) if limits else ""
            limit_text = f"{limit} / {window}" if limit is not None else "none"

            probe_note = ""
            probe = False
            if limit is None:
                probe_note = "no limit configured"
            elif limit > probe_max_tokens:
                probe_note = f"limit above probe cap ({probe_max_tokens})"
            elif parse_duration_s(window) < _MIN_PROBE_WINDOW_S:
                probe_note = "window too short to probe reliably"
            else:
                probe = True

            if ref_id in discovered:
                url, model_id = discovered[ref_id]
                listed = True
            else:
                # Not in /v1/models — try its dedicated per-model route
                # directly rather than letting discovery fall back to some
                # unrelated model (see inference.SendRequestsTask._discover_model).
                url, model_id = f"{ctx.maas_api_url}/{ref_id}/v1", ref["name"]
                listed = False

            result_key = f"subscription_model_{i + 1}"
            sender = _inference.SendRequestsTask(
                self.name,
                {
                    "url": url,
                    "model": model_id,
                    "token": key,
                    "count": probe_max_requests if probe else requests_per_model,
                    "concurrency": 1,
                    "prompt": prompt,
                    "retries": 0,
                    "result_key": result_key,
                },
            )
            await sender.run(ctx)
            ctx.shared_state["_traffic"][result_key]["label"] = ref_id
            if limit is not None:
                ctx.shared_state["_traffic"][result_key]["limit"] = limit
            r = ctx.shared_state.get(result_key, {})

            reachable = r.get("success_count", 0) > 0
            counts["reachable_count"] += int(reachable)
            throttled_at = r.get("tokens_before_first_429")
            if probe and reachable:
                counts["probed_count"] += 1
                enforced = (
                    throttled_at is not None
                    and limit <= throttled_at <= limit + spillover
                    and r.get("successes_after_first_429", 0) == 0
                )
                counts["enforced_count"] += int(enforced)
                if throttled_at is None:
                    limit_result = f"✗ never throttled ({r.get('total_tokens_sent', 0)} tokens sent)"
                else:
                    limit_result = f"{'✓' if enforced else '✗'} throttled at {throttled_at} tokens"
            else:
                limit_result = f"not probed — {probe_note}" if probe_note else "not probed — unreachable"

            failures = []
            if r.get("unauthorized_count"):
                failures.append(f"{r['unauthorized_count']}× 401/403")
            if r.get("server_error_count"):
                failures.append(f"{r['server_error_count']}× 5xx")
            if r.get("other_error_count"):
                failures.append(f"{r['other_error_count']}× other error")
            reach_text = "✓" if reachable else "✗ " + (", ".join(failures) or "no successful request")
            if not listed:
                reach_text += " (not listed in /v1/models)"

            rows.append([ref_id, limit_text, reach_text, limit_result])
            ctx.shared_state["subscription_check"] = dict(counts)
            ctx.shared_state["task_progress"] = {"current": i + 1, "total": len(model_refs)}
            ctx.shared_state["task_summary"] = (
                f"Subscription {sub_name}: {counts['reachable_count']}/{i + 1} models reachable · "
                f"{counts['enforced_count']}/{counts['probed_count']} probed limits enforced"
            )
            ctx.shared_state.setdefault("_tables", {})["Subscription models"] = {
                "columns": ["Model", "Configured limit", "Reachable", "Limit enforcement"],
                "rows": rows,
            }
            await ctx.emit_assertion_state()

        ctx.shared_state["subscription_check"] = counts
        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        await _auth._revoke_keys(ctx)


REGISTRY["verify_subscription_models"] = VerifySubscriptionModelsTask
