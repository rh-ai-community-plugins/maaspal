import time

import httpx

from harness.durations import parse_duration_s
from harness.result import TaskResult

# Module imports, not `from ... import Name`: harness/tasks/registry.py imports
# every task module, and inference imports the registry back — whichever
# module loads first, the others can be half-initialized at this point.
# Attributes are looked up at call time below, once everything has loaded.
from harness.tasks import inference as _inference
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY


def _find_subscription(name: str) -> dict:
    """The subscription as the MaaS overview page shows it (api/maas_client.py —
    same image, same ServiceAccount), including each modelRef's configured
    tokenRateLimits."""
    from api.maas_client import list_subscriptions

    result = list_subscriptions()
    if not result.available:
        raise RuntimeError(
            f"Couldn't read MaaSSubscriptions ({result.reason}) — check the Helm chart value rbac.maasReadonly"
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


def _limit_text(ref: dict) -> str:
    limits = ref.get("token_rate_limits") or []
    if not limits:
        return "no limit configured"
    return ", ".join(f"{lim['limit']} tokens / {lim.get('window', '?')}" for lim in limits)


class ReadSubscriptionLimitsTask(Task):
    """Reads the token rate limit an existing subscription actually declares
    for one model, so a rate-limit check compares against the real
    configuration instead of a number the user had to copy in by hand.
    Writes shared_state["subscription_limits"] = {token_limit, window,
    window_s} — referenced by assertions as ${harness.subscription_limits.x}
    and by send_requests' `limit_from_shared_state`."""

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        sub_name = str(self.params.get("subscription") or "")
        model_name = str(self.params.get("model_name") or "")
        model_namespace = str(self.params.get("model_namespace") or "")
        if not sub_name:
            raise RuntimeError("Pick a subscription to test")

        sub = _find_subscription(sub_name)
        refs = sub["model_refs"]
        ref = next(
            (r for r in refs if r["name"] == model_name and r["namespace"] == model_namespace),
            None,
        )
        if ref is None:
            covered = ", ".join(f"{r['namespace']}/{r['name']}" for r in refs) or "none"
            raise RuntimeError(
                f"Subscription {sub_name!r} doesn't cover {model_namespace}/{model_name} "
                f"(it covers: {covered})"
            )
        limits = ref.get("token_rate_limits") or []
        if not limits:
            raise RuntimeError(
                f"Subscription {sub_name!r} has no token rate limit for {model_namespace}/{model_name} — "
                "nothing to enforce"
            )
        limit = int(limits[0]["limit"])
        window = str(limits[0].get("window", ""))
        ctx.shared_state["subscription_limits"] = {
            "token_limit": limit,
            "window": window,
            "window_s": parse_duration_s(window) if window else None,
        }
        ctx.shared_state["task_summary"] = (
            f"{sub_name} allows {limit} tokens per {window} on {model_namespace}/{model_name}"
            + (f" (first of {len(limits)} limits)" if len(limits) > 1 else "")
        )
        await ctx.emit_assertion_state()
        return TaskResult(task_name=self.name, status="PASS", duration_ms=(time.monotonic() - start) * 1000)

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


class DiscoverSubscriptionModelsTask(Task):
    """Lists the models one existing subscription covers (its modelRefs, with
    their configured limits) and whether each is listed in GET /v1/models.
    Writes shared_state["subscription_models"] for
    send_requests_to_each_model, and a "Subscription models" table."""

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        sub_name = str(self.params.get("subscription") or "")
        if not sub_name:
            raise RuntimeError("Pick a subscription to verify")

        sub = _find_subscription(sub_name)
        discovered = await _discovered_models(ctx)
        models: list[dict] = []
        for ref in sub["model_refs"]:
            ref_id = f"{ref['namespace']}/{ref['name']}"
            if ref_id in discovered:
                url, model_id = discovered[ref_id]
            else:
                # Not in /v1/models — try its dedicated per-model route
                # rather than letting discovery fall back to an unrelated
                # model (see inference.SendRequestsTask._discover_model).
                url, model_id = f"{ctx.maas_api_url}/{ref_id}/v1", ref["name"]
            models.append({
                "ref": ref_id,
                "url": url,
                "model_id": model_id,
                "listed": ref_id in discovered,
                "limit": _limit_text(ref),
            })
        ctx.shared_state["subscription_models"] = models
        ctx.shared_state["subscription_check"] = {
            "model_count": len(models),
            "listed_count": sum(m["listed"] for m in models),
        }
        ctx.shared_state.setdefault("_tables", {})["Subscription models"] = {
            "columns": ["Model", "Configured limit", "Listed in /v1/models", "Reachable"],
            "rows": [[m["ref"], m["limit"], "✓" if m["listed"] else "✗", "…"] for m in models],
        }
        ctx.shared_state["task_summary"] = (
            f"{sub_name} covers {len(models)} model(s): " + (", ".join(m["ref"] for m in models) or "none")
        )
        await ctx.emit_assertion_state()
        return TaskResult(task_name=self.name, status="PASS", duration_ms=(time.monotonic() - start) * 1000)

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


class SendRequestsToEachModelTask(Task):
    """Sends a short burst to every model discover_subscription_models found,
    using the API key the scenario created (the last one in
    shared_state["api_keys"]), and fills in the table's Reachable column.
    Reachability only — rate limits have their own scenarios."""

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        models = ctx.shared_state.get("subscription_models") or []
        api_keys = ctx.shared_state.get("api_keys") or []
        if not api_keys:
            raise RuntimeError("No API key to send with — run provision_api_key first")
        key = api_keys[-1]["key"]
        requests_per_model = int(self.params.get("requests_per_model", 3))
        prompt = str(self.params.get("prompt", "Hello, world!"))

        check = ctx.shared_state.setdefault("subscription_check", {"model_count": len(models)})
        check["reachable_count"] = 0
        table = ctx.shared_state.setdefault("_tables", {}).setdefault(
            "Subscription models", {"columns": [], "rows": []}
        )
        for i, m in enumerate(models):
            result_key = f"subscription_model_{i + 1}"
            await _inference.SendRequestsTask(
                self.name,
                {
                    "url": m["url"],
                    "model": m["model_id"],
                    "token": key,
                    "count": requests_per_model,
                    "concurrency": 1,
                    "prompt": prompt,
                    "retries": 0,
                    "result_key": result_key,
                    # The scenario's Request API / Streaming settings.
                    "api": self.params.get("api"),
                    "stream": self.params.get("stream"),
                },
            ).run(ctx)
            ctx.shared_state["_traffic"][result_key]["label"] = m["ref"]
            r = ctx.shared_state.get(result_key, {})
            reachable = r.get("success_count", 0) > 0
            check["reachable_count"] += int(reachable)
            if reachable:
                reach = f"✓ {r['success_count']}/{r['total_requests']} OK"
            else:
                causes = []
                if r.get("unauthorized_count"):
                    causes.append(f"{r['unauthorized_count']}× denied (401/403)")
                if r.get("rate_limited_count"):
                    causes.append(f"{r['rate_limited_count']}× throttled (429)")
                if r.get("server_error_count"):
                    causes.append(f"{r['server_error_count']}× server error")
                if r.get("other_error_count"):
                    causes.append(f"{r['other_error_count']}× other error")
                reach = "✗ " + (", ".join(causes) or "no successful request")
            for row in table["rows"]:
                if row and row[0] == m["ref"]:
                    row[-1] = reach
            ctx.shared_state["task_progress"] = {"current": i + 1, "total": len(models), "unit": "models"}
            ctx.shared_state["task_summary"] = (
                f"{check['reachable_count']} of {i + 1} model(s) answered with this subscription's key"
            )
            await ctx.emit_assertion_state()

        return TaskResult(task_name=self.name, status="PASS", duration_ms=(time.monotonic() - start) * 1000)

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


REGISTRY["read_subscription_limits"] = ReadSubscriptionLimitsTask
REGISTRY["discover_subscription_models"] = DiscoverSubscriptionModelsTask
REGISTRY["send_requests_to_each_model"] = SendRequestsToEachModelTask
