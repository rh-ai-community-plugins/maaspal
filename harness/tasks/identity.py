import time
import traceback

import httpx
from kubernetes import client as k8s_client

from harness.result import TaskResult

# Module import, not names: auth imports the registry, which imports this
# module back — see harness/tasks/subscription_check.py.
from harness.tasks import auth as _auth
from harness.tasks.base import Task, TaskContext
from harness.tasks.registry import REGISTRY

# Matches harness/tasks/model.py's own _DEFAULT_NAMESPACE convention — the
# ServiceAccounts this task mints only need to exist in the harness's own
# namespace, where deploy/rbac-user-provisioning.yaml grants the create/
# delete/token RBAC (see ADR-023).
_DEFAULT_NAMESPACE = "maaspal"
_DEFAULT_NAME_PREFIX = "maaspal-user"
_DEFAULT_EXPIRATION_SECONDS = 3600


class CreateUserTask(Task):
    """Mints `count` throwaway ServiceAccounts (+ TokenRequest-issued
    tokens) in the harness's own namespace — the only identity-minting
    mechanism buildable from this harness's RBAC without IdP integration
    (ADR-023). Each SA's fully-qualified username
    (system:serviceaccount:<ns>:<name>) is directly matchable against a
    MaaSSubscription's spec.owner.users[] (confirmed live, ADR-018's
    Update), letting later tasks bind quota to a specific synthetic caller
    rather than the harness's own identity.

    Appends one entry per user to shared_state["users"]:
    {"name", "namespace", "username", "token"}.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        count = int(self.params.get("count", 1))
        name_prefix = str(self.params.get("name_prefix", _DEFAULT_NAME_PREFIX))
        namespace = str(self.params.get("namespace", _DEFAULT_NAMESPACE))
        expiration_seconds = int(
            self.params.get("expiration_seconds", _DEFAULT_EXPIRATION_SECONDS)
        )

        core_api = k8s_client.CoreV1Api()
        users = ctx.shared_state.setdefault("users", [])

        for i in range(count):
            sa_name = f"{name_prefix}-{ctx.run_id[:8]}-{i + 1}"
            print(f"[create_user] creating ServiceAccount {namespace}/{sa_name}", flush=True)
            core_api.create_namespaced_service_account(
                namespace=namespace,
                body=k8s_client.V1ServiceAccount(
                    metadata=k8s_client.V1ObjectMeta(name=sa_name)
                ),
            )

            token_request = k8s_client.AuthenticationV1TokenRequest(
                api_version="authentication.k8s.io/v1",
                kind="TokenRequest",
                spec=k8s_client.V1TokenRequestSpec(
                    expiration_seconds=expiration_seconds
                ),
            )
            resp = core_api.create_namespaced_service_account_token(
                name=sa_name, namespace=namespace, body=token_request
            )
            token = resp.status.token
            username = f"system:serviceaccount:{namespace}:{sa_name}"
            print(
                f"[create_user] minted token for {username} "
                f"(token={_auth._redact(token)}, expires_in={expiration_seconds}s)",
                flush=True,
            )

            users.append(
                {
                    "name": sa_name,
                    "namespace": namespace,
                    "username": username,
                    "token": token,
                }
            )

            ctx.shared_state["task_progress"] = {"current": i + 1, "total": count}
            ctx.shared_state["task_summary"] = (
                f"Minted {i + 1} throwaway identit{'y' if i == 0 else 'ies'} "
                f"(ServiceAccounts in {namespace}): "
                + ", ".join(u["name"] for u in users)
            )
            await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        users = ctx.shared_state.get("users", [])
        if not users:
            return

        core_api = k8s_client.CoreV1Api()
        for user in users:
            try:
                core_api.delete_namespaced_service_account(
                    name=user["name"], namespace=user["namespace"]
                )
                print(f"[create_user] deleted ServiceAccount {user['namespace']}/{user['name']}", flush=True)
            except Exception:
                print(
                    f"[create_user] cleanup FAILED for {user.get('name')}\n{traceback.format_exc()}",
                    flush=True,
                )


class ProvisionKeysForUsersTask(Task):
    """Provisions one MaaS API key per user in shared_state["users"]
    (created by create_user), each authenticated with that user's OWN
    token — not ctx.sa_token — so the resulting key is minted as that
    caller's identity (ADR-023). All keys are pinned to the same
    `subscription`, so they can be checked for shared-vs-per-user rate
    limiting once used.

    Appends each key to shared_state["api_keys"] (the same list
    send_requests's key_pool reads), with an added owner_username field.
    Reuses the key_provision_checks counters shape from
    ProvisionApiKeyTask (harness/tasks/auth.py) for assertion-path parity.
    """

    async def run(self, ctx: TaskContext) -> TaskResult:
        start = time.monotonic()
        users: list[dict] = ctx.shared_state.get("users", [])
        if not users:
            raise RuntimeError(
                "provision_keys_for_users requires users in shared_state — run create_user first"
            )

        key_name_prefix = str(self.params.get("key_name_prefix", "maaspal-user-key"))
        subscription = str(self.params["subscription"])

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
        async with httpx.AsyncClient() as client:
            for i, user in enumerate(users):
                name_i = f"{key_name_prefix}-{i + 1}"
                body = {"name": name_i, "subscription": subscription}
                print(
                    f"[provision_keys_for_users] POST {url} body={body!r} "
                    f"as user={user['username']} (token={_auth._redact(user['token'])})",
                    flush=True,
                )
                resp = await client.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {user['token']}"},
                )
                if not resp.is_success:
                    print(
                        f"[provision_keys_for_users] POST {url} → {resp.status_code}: {resp.text}",
                        flush=True,
                    )
                resp.raise_for_status()
                data = resp.json()
                print(
                    f"[provision_keys_for_users] created key id={data.get('id')} "
                    f"name={data.get('name')} owner={user['username']}",
                    flush=True,
                )

                ctx.shared_state.setdefault("api_keys", []).append(
                    {
                        "id": data["id"],
                        "key": data["key"],
                        "name": data.get("name"),
                        "subscription": data.get("subscription"),
                        "expiresAt": data.get("expiresAt"),
                        "owner_username": user["username"],
                    }
                )

                checks["total_keys"] += 1
                if data.get("name") == name_i:
                    checks["name_echo_match_count"] += 1
                checks["subscription_checked_count"] += 1
                if data.get("subscription") == subscription:
                    checks["subscription_echo_match_count"] += 1
                if data.get("expiresAt"):
                    checks["expires_at_present_count"] += 1

                ctx.shared_state["task_progress"] = {"current": i + 1, "total": len(users)}
                ctx.shared_state["task_summary"] = (
                    f"Created {i + 1} API key(s), one per user, each authenticated as "
                    f"that user · pinned to subscription {subscription}"
                )
                await ctx.emit_assertion_state()

        return TaskResult(
            task_name=self.name,
            status="PASS",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def cleanup(self, ctx: TaskContext) -> None:
        # Deletes via ctx.sa_token (the harness's own admin identity), not
        # each key's owning user's token — unverified assumption that the
        # harness SA can revoke a key it didn't create; see ADR-023.
        await _auth._revoke_keys(ctx)


REGISTRY["create_user"] = CreateUserTask
REGISTRY["provision_keys_for_users"] = ProvisionKeysForUsersTask
