import json
from unittest.mock import MagicMock, patch

import pytest
from pytest_httpx import HTTPXMock

from harness.tasks.base import TaskContext
from harness.tasks.identity import CreateUserTask, ProvisionKeysForUsersTask


def _make_ctx(shared_state: dict | None = None) -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="test-0001abcd",
        scenario_name="test",
        maas_api_url="http://maas.test",
        sa_token="harness-sa-token",
        shared_state=shared_state or {},
        config={},
        assertions={},
        emit_assertion_state=_emit,
    )


def _token_response(token: str) -> MagicMock:
    resp = MagicMock()
    resp.status.token = token
    return resp


async def test_create_user_mints_sa_and_token() -> None:
    with patch("harness.tasks.identity.k8s_client.CoreV1Api") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.create_namespaced_service_account_token.return_value = _token_response("tok-a")

        task = CreateUserTask("create_user", {})
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    assert len(ctx.shared_state["users"]) == 1
    user = ctx.shared_state["users"][0]
    assert user["namespace"] == "maaspal"
    assert user["token"] == "tok-a"
    assert user["username"] == f"system:serviceaccount:maaspal:{user['name']}"
    assert user["name"].startswith("maaspal-user-")

    api.create_namespaced_service_account.assert_called_once()
    sa_body = api.create_namespaced_service_account.call_args.kwargs["body"]
    assert sa_body.metadata.name == user["name"]

    token_call = api.create_namespaced_service_account_token.call_args
    assert token_call.kwargs["name"] == user["name"]
    assert token_call.kwargs["namespace"] == "maaspal"
    assert token_call.kwargs["body"].spec.expiration_seconds == 3600


async def test_create_user_multiple_count_gets_distinct_names() -> None:
    with patch("harness.tasks.identity.k8s_client.CoreV1Api") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.create_namespaced_service_account_token.side_effect = [
            _token_response("tok-a"),
            _token_response("tok-b"),
        ]

        task = CreateUserTask("create_user", {"count": 2, "name_prefix": "pool-user"})
        ctx = _make_ctx()
        result = await task.run(ctx)

    assert result.status == "PASS"
    users = ctx.shared_state["users"]
    assert len(users) == 2
    assert users[0]["name"] != users[1]["name"]
    assert users[0]["token"] == "tok-a"
    assert users[1]["token"] == "tok-b"
    assert all(u["name"].startswith("pool-user-") for u in users)


async def test_create_user_custom_namespace_and_expiration() -> None:
    with patch("harness.tasks.identity.k8s_client.CoreV1Api") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.create_namespaced_service_account_token.return_value = _token_response("tok-a")

        task = CreateUserTask(
            "create_user", {"namespace": "other-ns", "expiration_seconds": 60}
        )
        ctx = _make_ctx()
        await task.run(ctx)

    user = ctx.shared_state["users"][0]
    assert user["namespace"] == "other-ns"
    assert user["username"].startswith("system:serviceaccount:other-ns:")
    token_call = api.create_namespaced_service_account_token.call_args
    assert token_call.kwargs["body"].spec.expiration_seconds == 60


async def test_create_user_cleanup_deletes_service_accounts() -> None:
    with patch("harness.tasks.identity.k8s_client.CoreV1Api") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api

        ctx = _make_ctx(
            {
                "users": [
                    {"name": "u1", "namespace": "maaspal", "username": "x", "token": "t1"},
                    {"name": "u2", "namespace": "maaspal", "username": "y", "token": "t2"},
                ]
            }
        )
        task = CreateUserTask("create_user", {})
        await task.cleanup(ctx)

    assert api.delete_namespaced_service_account.call_count == 2


async def test_create_user_cleanup_noop_when_empty() -> None:
    with patch("harness.tasks.identity.k8s_client.CoreV1Api") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api

        task = CreateUserTask("create_user", {})
        await task.cleanup(_make_ctx())

    api.delete_namespaced_service_account.assert_not_called()


async def test_create_user_cleanup_continues_after_one_failure() -> None:
    with patch("harness.tasks.identity.k8s_client.CoreV1Api") as mock_cls:
        api = MagicMock()
        mock_cls.return_value = api
        api.delete_namespaced_service_account.side_effect = [Exception("boom"), None]

        ctx = _make_ctx(
            {
                "users": [
                    {"name": "u1", "namespace": "maaspal", "username": "x", "token": "t1"},
                    {"name": "u2", "namespace": "maaspal", "username": "y", "token": "t2"},
                ]
            }
        )
        task = CreateUserTask("create_user", {})
        await task.cleanup(ctx)

    assert api.delete_namespaced_service_account.call_count == 2


async def test_provision_keys_for_users_requires_users() -> None:
    task = ProvisionKeysForUsersTask(
        "provision_keys_for_users", {"subscription": "maaspal-shared-pool-test"}
    )
    with pytest.raises(RuntimeError):
        await task.run(_make_ctx())


async def test_provision_keys_for_users_authenticates_as_each_user(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-a", "key": "sk-a", "name": "maaspal-user-key-1", "subscription": "shared-sub"},
    )
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-b", "key": "sk-b", "name": "maaspal-user-key-2", "subscription": "shared-sub"},
    )

    ctx = _make_ctx(
        {
            "users": [
                {"name": "u1", "namespace": "maaspal", "username": "user-a", "token": "user-a-token"},
                {"name": "u2", "namespace": "maaspal", "username": "user-b", "token": "user-b-token"},
            ]
        }
    )
    task = ProvisionKeysForUsersTask(
        "provision_keys_for_users", {"subscription": "shared-sub"}
    )
    result = await task.run(ctx)

    assert result.status == "PASS"
    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    auth_headers = [r.headers["Authorization"] for r in requests]
    assert auth_headers == ["Bearer user-a-token", "Bearer user-b-token"]

    keys = ctx.shared_state["api_keys"]
    assert [k["owner_username"] for k in keys] == ["user-a", "user-b"]
    assert [k["key"] for k in keys] == ["sk-a", "sk-b"]

    for req in requests:
        body = json.loads(req.content)
        assert body["subscription"] == "shared-sub"


async def test_provision_keys_for_users_checks_response_echo(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={
            "id": "key-a",
            "key": "sk-a",
            "name": "maaspal-pool-key-1",
            "subscription": "shared-sub",
            "expiresAt": "2027-01-01T00:00:00Z",
        },
    )

    ctx = _make_ctx(
        {"users": [{"name": "u1", "namespace": "maaspal", "username": "user-a", "token": "tok"}]}
    )
    task = ProvisionKeysForUsersTask(
        "provision_keys_for_users", {"key_name_prefix": "maaspal-pool-key", "subscription": "shared-sub"}
    )
    await task.run(ctx)

    checks = ctx.shared_state["key_provision_checks"]
    assert checks["total_keys"] == 1
    assert checks["name_echo_match_count"] == 1
    assert checks["subscription_checked_count"] == 1
    assert checks["subscription_echo_match_count"] == 1
    assert checks["expires_at_present_count"] == 1


async def test_provision_keys_for_users_cleanup_revokes_via_sa_token(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/key-a", method="DELETE", status_code=204
    )

    ctx = _make_ctx(
        {"api_keys": [{"id": "key-a", "key": "sk-a", "owner_username": "user-a"}]}
    )
    task = ProvisionKeysForUsersTask("provision_keys_for_users", {})
    await task.cleanup(ctx)

    req = httpx_mock.get_requests()[0]
    # Cleanup revokes via the harness's own admin SA token, not the owning
    # user's token — see ADR-023's flagged, unverified cross-identity-delete
    # assumption.
    assert req.headers["Authorization"] == "Bearer harness-sa-token"
