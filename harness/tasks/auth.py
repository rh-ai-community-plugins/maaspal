import time
import traceback

import httpx

from harness.result import TaskResult
from harness.tasks.base import Task, TaskContext, record_created
from harness.tasks.registry import REGISTRY


def _redact(token: str) -> str:
    if not token:
        return "(empty)"
    return token[:8] + "****" if len(token) > 8 else "****"


async def _revoke_keys(ctx: TaskContext, keys: list[dict] | None = None) -> int:
    """DELETE every key in shared_state["api_keys"] (or just `keys`) via the
    MaaS REST API. Each key that's gone afterwards is flagged `revoked`, and
    its run-page resource record marked "revoked" when this is a mid-run
    revoke (cleanup's own marking otherwise handles it).

    Shared by ProvisionApiKeyTask.cleanup() (end of scenario, as before) and
    RevokeApiKeysTask.run() (mid-scenario — lets a later task confirm
    inference is denied immediately, not after some caching delay; see
    scenarios/api_key_lifecycle.yaml and ADR-019). Returns how many DELETEs
    succeeded; a key already gone (e.g. revoked earlier in the same run) just
    fails its own DELETE, logged and swallowed like any other cleanup error —
    not a new failure mode.
    """
    api_keys = ctx.shared_state.get("api_keys", []) if keys is None else keys
    if not api_keys:
        return 0

    revoked = 0
    async with httpx.AsyncClient() as client:
        for key in api_keys:
            key_id = key["id"]
            del_url = f"{ctx.maas_api_url}/maas-api/v1/api-keys/{key_id}"
            print(f"[revoke_keys] DELETE {del_url} (token={_redact(ctx.sa_token)})", flush=True)
            try:
                resp = await client.delete(
                    del_url,
                    headers={"Authorization": f"Bearer {ctx.sa_token}"},
                )
                if resp.status_code == 404:
                    # Expected, not a failure: the key was already revoked
                    # earlier in this same run (mid-scenario revoke_api_keys,
                    # or cleanup re-running over a key another task already
                    # deleted) — a quiet note, not a traceback, so an
                    # intentional no-op doesn't read as a crash in the logs.
                    print(f"[revoke_keys] key {key_id} already revoked, skipping", flush=True)
                    key["revoked"] = True
                    continue
                if not resp.is_success:
                    print(
                        f"[revoke_keys] DELETE {del_url} → {resp.status_code}: {resp.text}",
                        flush=True,
                    )
                resp.raise_for_status()
                revoked += 1
                key["revoked"] = True
                print(f"[revoke_keys] deleted key {key_id}", flush=True)
            except Exception:
                print(
                    f"[revoke_keys] DELETE {key_id} FAILED\n{traceback.format_exc()}",
                    flush=True,
                )
    return revoked


def _key_summary(created: int, key_name: str, subscriptions: list[str], pinned: bool) -> str:
    """Narration line for key-provisioning tasks: how many, and which
    subscription MaaS actually bound them to (pinned vs auto-selected)."""
    unique = sorted(set(subscriptions))
    how = "pinned to" if pinned else "auto-selected"
    noun = "key" if created == 1 else "keys"
    return f"Created {created} API {noun} ({key_name}…) · {how} subscription {', '.join(unique)}"


class ProvisionApiKeyTask(Task):
    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        key_name = str(self.params.get("key_name") or f"maaspal-{ctx.run_id[:8]}")
        count = int(self.params.get("count", 1))
        # Explicit subscription binding — without it, key creation
        # auto-selects whichever subscription the caller's identity resolves
        # to (highest priority among eligible ones), which may not be the
        # subscription a scenario is specifically trying to validate (e.g.
        # rate_limit_validation's test CR). Passing this pins the created
        # keys to a known subscription regardless of what else the caller is
        # eligible for.
        subscription = self.params.get("subscription")
        # Verification-only, distinct from `subscription` above: does NOT get
        # sent in the request body, so it never influences which subscription
        # gets picked. Used to check the outcome of auto-selection (no
        # `subscription` pinned at all) against an expected winner — e.g.
        # confirming priority-based precedence (ADR-021,
        # scenarios/rate_limit_priority_precedence.yaml) — as opposed to
        # `subscription`, which forces a specific one.
        expect_subscription = self.params.get("expect_subscription")

        # REST-only lifecycle checks (ADR-019, empirical-verification-checklist.md):
        # does the create response actually echo what we asked for, rather
        # than us trusting it silently? Pure request-vs-response comparison —
        # no CR read involved, so this holds up across MaaS schema changes.
        # subscription_checked_count/expected_subscription_checked_count are
        # their own counters (not just total_keys) because a scenario may not
        # pass `subscription`/`expect_subscription` at all.
        checks = ctx.shared_state.setdefault(
            "key_provision_checks",
            {
                "total_keys": 0,
                "name_echo_match_count": 0,
                "subscription_checked_count": 0,
                "subscription_echo_match_count": 0,
                "expected_subscription_checked_count": 0,
                "expected_subscription_match_count": 0,
                "expires_at_present_count": 0,
            },
        )

        url = f"{ctx.maas_api_url}/maas-api/v1/api-keys"
        bound_subscriptions: list[str] = []
        async with httpx.AsyncClient() as client:
            for i in range(count):
                name_i = f"{key_name}-{i + 1}" if count > 1 else key_name
                body: dict = {"name": name_i}
                if subscription:
                    body["subscription"] = subscription
                print(
                    f"[provision_api_key] POST {url} "
                    f"body={body!r} "
                    f"(token={_redact(ctx.sa_token)})",
                    flush=True,
                )
                resp = await client.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {ctx.sa_token}"},
                )
                if not resp.is_success:
                    print(
                        f"[provision_api_key] POST {url} → {resp.status_code}: {resp.text}",
                        flush=True,
                    )
                resp.raise_for_status()
                data = resp.json()
                print(
                    f"[provision_api_key] created key id={data.get('id')} name={data.get('name')}",
                    flush=True,
                )
                ctx.shared_state.setdefault("api_keys", []).append(
                    {
                        "id": data["id"],
                        "key": data["key"],
                        "name": data.get("name"),
                        "subscription": data.get("subscription"),
                        "expiresAt": data.get("expiresAt"),
                    }
                )
                record_created(
                    ctx, self.name, "API key", str(data.get("name") or data["id"]),
                    key_id=data["id"], subscription=data.get("subscription"),
                )

                checks["total_keys"] += 1
                if data.get("name") == name_i:
                    checks["name_echo_match_count"] += 1
                if subscription:
                    checks["subscription_checked_count"] += 1
                    if data.get("subscription") == subscription:
                        checks["subscription_echo_match_count"] += 1
                if expect_subscription:
                    checks["expected_subscription_checked_count"] += 1
                    if data.get("subscription") == expect_subscription:
                        checks["expected_subscription_match_count"] += 1
                if data.get("expiresAt"):
                    checks["expires_at_present_count"] += 1

                bound_subscriptions.append(str(data.get("subscription") or "?"))
                ctx.shared_state["task_progress"] = {"current": i + 1, "total": count}
                ctx.shared_state["task_summary"] = _key_summary(
                    i + 1, key_name, bound_subscriptions, pinned=bool(subscription)
                )
                await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        await _revoke_keys(ctx)


class RevokeApiKeysTask(Task):
    """Revoke every currently-provisioned API key mid-scenario, not at
    cleanup time — lets a later `send_requests`-family task confirm
    inference is denied right away. See scenarios/api_key_lifecycle.yaml
    and ADR-019. Reuses the same DELETE loop as ProvisionApiKeyTask's own
    cleanup(); that cleanup still runs at the end of the scenario and will
    harmlessly re-attempt DELETE on these already-gone keys.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        # `count`: revoke only the first N keys (e.g. 1 of 3, to check the
        # others keep working); default every key.
        api_keys = ctx.shared_state.get("api_keys", [])
        count = self.params.get("count")
        targets = api_keys[: int(count)] if count not in (None, "") else api_keys
        revoked = await _revoke_keys(ctx, targets)
        revoked_ids = {k["id"] for k in targets if k.get("revoked")}
        for rec in ctx.shared_state.get("_created") or []:
            if rec.get("key_id") in revoked_ids:
                rec["status"] = "revoked"
        ctx.shared_state["revoked_count"] = revoked
        kept = len(api_keys) - len(targets)
        ctx.shared_state["task_summary"] = f"Revoked {revoked} API key(s) mid-run" + (
            f" · {kept} left active" if kept else ""
        )
        await ctx.emit_assertion_state()
        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


class VerifyApiKeySearchTask(Task):
    """Confirm POST /maas-api/v1/api-keys/search actually finds the keys
    this run created, filtered by name_prefix — REST-only, no CR read.

    Honest scope limit: with only the harness's own SA identity available,
    this proves inclusion (our keys are findable) and filtering (name_prefix
    narrows correctly), not true caller-scoping (that a *different* caller's
    keys are excluded) — see the open caveat in
    docs/architecture/empirical-verification-checklist.md.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        name_prefix = str(self.params.get("name_prefix", ""))
        url = f"{ctx.maas_api_url}/maas-api/v1/api-keys/search"
        body: dict = {"name_prefix": name_prefix} if name_prefix else {}
        print(f"[verify_api_key_search] POST {url} body={body!r}", flush=True)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {ctx.sa_token}"},
            )
            resp.raise_for_status()
            resp_body = resp.json()

        # The MaaS search API uses "data" (not "items") and ignores name_prefix
        # server-side — it returns all keys for the caller regardless, newest
        # first, paginated (has_more). We filter client-side: prefix match +
        # active status only, so revoked keys from previous runs with the same
        # prefix don't inflate the count.
        all_items = resp_body.get("data", [])
        print(f"[verify_api_key_search] response: {len(all_items)} total keys, has_more={resp_body.get('has_more')}", flush=True)
        found = [
            item for item in all_items
            if item.get("name", "").startswith(name_prefix) and item.get("status") == "active"
        ] if name_prefix else all_items
        found_count = len(found)
        expected_count = len(ctx.shared_state.get("api_keys", []))
        ctx.shared_state["search_check"] = {
            "found_count": found_count,
            "expected_count": expected_count,
        }
        print(
            f"[verify_api_key_search] found={found_count} expected={expected_count}",
            flush=True,
        )
        ctx.shared_state["task_summary"] = (
            f"Search for active keys named '{name_prefix}…' found {found_count} "
            f"(this run created {expected_count})"
        )
        await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        pass


class ProvisionKeysDistributedTask(Task):
    """Creates key_count API keys distributed evenly across the subscriptions
    in shared_state["distributed_subscriptions"] (set by
    provision_subscriptions_distributed). Each key is pinned to its
    subscription via the `subscription` field in the create request.

    All keys are appended to shared_state["api_keys"] so send_requests with
    key_pool: true distributes requests across the full key pool.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        subscriptions: list[dict] = ctx.shared_state.get("distributed_subscriptions", [])
        if not subscriptions:
            raise RuntimeError("provision_keys_distributed requires distributed_subscriptions in shared_state — run provision_subscriptions_distributed first")

        key_count = int(self.params.get("key_count", 10))
        key_name_prefix = str(self.params.get("key_name_prefix", "maaspal-dist-key"))

        sub_count = len(subscriptions)
        keys_per_sub = key_count // sub_count
        remainder = key_count % sub_count

        checks = ctx.shared_state.setdefault(
            "key_provision_checks",
            {
                "total_keys": 0,
                "name_echo_match_count": 0,
                "subscription_checked_count": 0,
                "subscription_echo_match_count": 0,
                "expected_subscription_checked_count": 0,
                "expected_subscription_match_count": 0,
                "expires_at_present_count": 0,
            },
        )

        url = f"{ctx.maas_api_url}/maas-api/v1/api-keys"
        global_key_index = 0
        async with httpx.AsyncClient() as client:
            for sub_idx, sub in enumerate(subscriptions):
                sub_name = sub["name"]
                count_for_sub = keys_per_sub + (1 if sub_idx < remainder else 0)
                for j in range(count_for_sub):
                    global_key_index += 1
                    name_i = f"{key_name_prefix}-{global_key_index}"
                    body = {"name": name_i, "subscription": sub_name}
                    print(
                        f"[provision_keys_distributed] POST {url} "
                        f"body={body!r} "
                        f"(token={_redact(ctx.sa_token)})",
                        flush=True,
                    )
                    resp = await client.post(
                        url,
                        json=body,
                        headers={"Authorization": f"Bearer {ctx.sa_token}"},
                    )
                    if not resp.is_success:
                        print(
                            f"[provision_keys_distributed] POST {url} → {resp.status_code}: {resp.text}",
                            flush=True,
                        )
                    resp.raise_for_status()
                    data = resp.json()
                    print(
                        f"[provision_keys_distributed] created key id={data.get('id')} "
                        f"name={data.get('name')} subscription={sub_name}",
                        flush=True,
                    )
                    model_refs = sub.get("model_refs", [])
                    key_record: dict = {
                        "id": data["id"],
                        "key": data["key"],
                        "name": data.get("name"),
                        "subscription": data.get("subscription"),
                        "expiresAt": data.get("expiresAt"),
                    }
                    if model_refs:
                        # "<namespace>/<name>" — the same identifier form
                        # /v1/models reports as owned_by, confirmed live to
                        # be accepted directly by the inference endpoint even
                        # before a freshly-deployed model becomes visible in
                        # discovery (see SendRequestsTask's target_model/
                        # model_from_shared_state handling).
                        key_record["target_model"] = f"{model_refs[0]['namespace']}/{model_refs[0]['name']}"
                    ctx.shared_state.setdefault("api_keys", []).append(key_record)
                    record_created(
                        ctx, self.name, "API key", str(data.get("name") or data["id"]),
                        key_id=data["id"], subscription=data.get("subscription"),
                    )

                    checks["total_keys"] += 1
                    if data.get("name") == name_i:
                        checks["name_echo_match_count"] += 1
                    checks["subscription_checked_count"] += 1
                    if data.get("subscription") == sub_name:
                        checks["subscription_echo_match_count"] += 1
                    if data.get("expiresAt"):
                        checks["expires_at_present_count"] += 1

                    ctx.shared_state["task_progress"] = {"current": global_key_index, "total": key_count}
                    ctx.shared_state["task_summary"] = (
                        f"Created {global_key_index} API keys across "
                        f"{len({k.get('subscription') for k in ctx.shared_state['api_keys']})} subscriptions"
                    )
                    await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        await _revoke_keys(ctx)


REGISTRY["provision_api_key"] = ProvisionApiKeyTask
REGISTRY["revoke_api_keys"] = RevokeApiKeysTask
REGISTRY["verify_api_key_search"] = VerifyApiKeySearchTask
REGISTRY["provision_keys_distributed"] = ProvisionKeysDistributedTask
# Alias (ADR-019 pattern): scenarios/api_key_lifecycle.yaml searches again
# after revoking, to confirm revoked keys drop out of the active-key search —
# a second search step needs its own chip/progress identity.
REGISTRY["verify_revoked_key_not_searchable"] = VerifyApiKeySearchTask
