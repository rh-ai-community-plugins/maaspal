import json

from pytest_httpx import HTTPXMock

from harness.tasks.auth import (
    ProvisionApiKeyTask,
    ProvisionKeysDistributedTask,
    RevokeApiKeysTask,
    VerifyApiKeySearchTask,
)
from harness.tasks.base import TaskContext


def _make_ctx(shared_state: dict | None = None) -> TaskContext:
    async def _emit() -> None:
        pass

    return TaskContext(
        run_id="test-001",
        scenario_name="test",
        maas_api_url="http://maas.test",
        sa_token="test-token",
        shared_state=shared_state or {},
        config={},
        assertions={},
        emit_assertion_state=_emit,
    )


async def test_provision_api_key_appends_to_shared_state(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={
            "id": "key-123",
            "key": "sk-oai-abc",
            "name": "test-key",
            "subscription": "simulator-free",
            "expiresAt": "2027-01-01T00:00:00Z",
        },
    )

    task = ProvisionApiKeyTask("provision_api_key", {"key_name": "test-key"})
    ctx = _make_ctx()
    result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["api_keys"] == [
        {
            "id": "key-123",
            "key": "sk-oai-abc",
            "name": "test-key",
            "subscription": "simulator-free",
            "expiresAt": "2027-01-01T00:00:00Z",
        }
    ]


async def test_provision_api_key_accumulates_multiple_keys(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-1", "key": "sk-1"},
    )
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-2", "key": "sk-2"},
    )

    task = ProvisionApiKeyTask("provision_api_key", {"key_name": "test-key"})
    ctx = _make_ctx()
    await task.run(ctx)
    await task.run(ctx)

    assert len(ctx.shared_state["api_keys"]) == 2


async def test_provision_api_key_cleanup_deletes_individually(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/id-1",
        method="DELETE",
        status_code=204,
    )
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/id-2",
        method="DELETE",
        status_code=204,
    )

    ctx = _make_ctx(
        {"api_keys": [{"id": "id-1", "key": "sk-1"}, {"id": "id-2", "key": "sk-2"}]}
    )
    task = ProvisionApiKeyTask("provision_api_key", {})
    await task.cleanup(ctx)

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    urls = {str(r.url) for r in requests}
    assert urls == {
        "http://maas.test/maas-api/v1/api-keys/id-1",
        "http://maas.test/maas-api/v1/api-keys/id-2",
    }


async def test_provision_api_key_cleanup_noop_when_empty() -> None:
    ctx = _make_ctx({"api_keys": []})
    task = ProvisionApiKeyTask("provision_api_key", {})
    await task.cleanup(ctx)


async def test_provision_api_key_uses_default_name(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-xyz", "key": "sk-oai-xyz"},
    )

    task = ProvisionApiKeyTask("provision_api_key", {})
    ctx = _make_ctx()
    result = await task.run(ctx)

    assert result.status == "PASS"
    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert body["name"].startswith("maaspal-")


async def test_provision_api_key_binds_explicit_subscription(httpx_mock: HTTPXMock) -> None:
    """Without this, a created key auto-selects whichever subscription the
    caller's identity resolves to — rate_limit_validation needs its keys
    pinned to the specific subscription it just created, not whatever else
    the SA happens to be eligible for (see ADR-009's Update section)."""
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-1", "key": "sk-1"},
    )

    task = ProvisionApiKeyTask(
        "provision_api_key", {"key_name": "test-key", "subscription": "maaspal-rate-limit-test"}
    )
    await task.run(_make_ctx())

    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert body["subscription"] == "maaspal-rate-limit-test"


async def test_provision_api_key_omits_subscription_field_when_not_set(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-1", "key": "sk-1"},
    )

    task = ProvisionApiKeyTask("provision_api_key", {"key_name": "test-key"})
    await task.run(_make_ctx())


async def test_provision_api_key_checks_response_echo(httpx_mock: HTTPXMock) -> None:
    """REST-only lifecycle checks (ADR-019): does the create response honestly
    echo what was requested? Subscription is checked only when requested."""
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={
            "id": "key-1",
            "key": "sk-1",
            "name": "test-key",
            "subscription": "maaspal-rate-limit-test",
            "expiresAt": "2027-01-01T00:00:00Z",
        },
    )

    task = ProvisionApiKeyTask(
        "provision_api_key", {"key_name": "test-key", "subscription": "maaspal-rate-limit-test"}
    )
    ctx = _make_ctx()
    await task.run(ctx)

    checks = ctx.shared_state["key_provision_checks"]
    assert checks["total_keys"] == 1
    assert checks["name_echo_match_count"] == 1
    assert checks["subscription_checked_count"] == 1
    assert checks["subscription_echo_match_count"] == 1
    assert checks["expires_at_present_count"] == 1


async def test_provision_api_key_checks_flag_mismatches(httpx_mock: HTTPXMock) -> None:
    """A response that silently diverges from the request (wrong name,
    auto-selected subscription instead of the one asked for, no expiry) must
    not be counted as a match."""
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={
            "id": "key-1",
            "key": "sk-1",
            "name": "renamed-by-server",
            "subscription": "auto-selected-other-sub",
        },
    )

    task = ProvisionApiKeyTask(
        "provision_api_key", {"key_name": "test-key", "subscription": "maaspal-rate-limit-test"}
    )
    ctx = _make_ctx()
    await task.run(ctx)

    checks = ctx.shared_state["key_provision_checks"]
    assert checks["name_echo_match_count"] == 0
    assert checks["subscription_checked_count"] == 1
    assert checks["subscription_echo_match_count"] == 0
    assert checks["expires_at_present_count"] == 0


async def test_provision_api_key_subscription_not_checked_when_absent(httpx_mock: HTTPXMock) -> None:
    """A scenario that never passes `subscription` (e.g. api_key_lifecycle,
    kept CR-free) shouldn't have that counter treated as a failure."""
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-1", "key": "sk-1", "name": "test-key"},
    )

    task = ProvisionApiKeyTask("provision_api_key", {"key_name": "test-key"})
    ctx = _make_ctx()
    await task.run(ctx)

    checks = ctx.shared_state["key_provision_checks"]
    assert checks["subscription_checked_count"] == 0
    assert checks["subscription_echo_match_count"] == 0


async def test_provision_api_key_expect_subscription_matches(httpx_mock: HTTPXMock) -> None:
    """expect_subscription (ADR-021) verifies auto-selection's outcome
    without pinning — it must never appear in the request body."""
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-1", "key": "sk-1", "subscription": "maaspal-priority-high"},
    )

    task = ProvisionApiKeyTask(
        "provision_api_key",
        {"key_name": "test-key", "expect_subscription": "maaspal-priority-high"},
    )
    ctx = _make_ctx()
    await task.run(ctx)

    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert "subscription" not in body

    checks = ctx.shared_state["key_provision_checks"]
    assert checks["expected_subscription_checked_count"] == 1
    assert checks["expected_subscription_match_count"] == 1
    # expect_subscription is a check, not a pin — subscription_* counters
    # (which only fire when `subscription` itself was passed) stay untouched.
    assert checks["subscription_checked_count"] == 0


async def test_provision_api_key_expect_subscription_mismatch(httpx_mock: HTTPXMock) -> None:
    """Auto-selection picking the WRONG subscription must not be counted as
    a match — this is the case that would actually catch a real priority bug."""
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "key-1", "key": "sk-1", "subscription": "maaspal-priority-low"},
    )

    task = ProvisionApiKeyTask(
        "provision_api_key",
        {"key_name": "test-key", "expect_subscription": "maaspal-priority-high"},
    )
    ctx = _make_ctx()
    await task.run(ctx)

    checks = ctx.shared_state["key_provision_checks"]
    assert checks["expected_subscription_checked_count"] == 1
    assert checks["expected_subscription_match_count"] == 0


async def test_revoke_api_keys_deletes_and_counts(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/id-1", method="DELETE", status_code=204
    )
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/id-2", method="DELETE", status_code=204
    )

    ctx = _make_ctx({"api_keys": [{"id": "id-1", "key": "sk-1"}, {"id": "id-2", "key": "sk-2"}]})
    task = RevokeApiKeysTask("revoke_api_keys", {})
    result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["revoked_count"] == 2


async def test_revoke_api_keys_counts_partial_failure(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/id-1", method="DELETE", status_code=204
    )
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/id-2", method="DELETE", status_code=404
    )

    ctx = _make_ctx({"api_keys": [{"id": "id-1", "key": "sk-1"}, {"id": "id-2", "key": "sk-2"}]})
    task = RevokeApiKeysTask("revoke_api_keys", {})
    result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["revoked_count"] == 1


async def test_revoke_api_keys_already_revoked_logs_quietly(
    httpx_mock: HTTPXMock, capsys
) -> None:
    """A 404 (key already revoked, e.g. cleanup re-running over a key
    revoke_api_keys already deleted mid-scenario) is expected, not a
    failure — it must not dump a full traceback into the run log."""
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/id-1", method="DELETE", status_code=404
    )

    ctx = _make_ctx({"api_keys": [{"id": "id-1", "key": "sk-1"}]})
    task = RevokeApiKeysTask("revoke_api_keys", {})
    result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["revoked_count"] == 0
    out = capsys.readouterr().out
    assert "already revoked, skipping" in out
    assert "Traceback" not in out


async def test_verify_api_key_search_matches_expected_count(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/search",
        method="POST",
        json={"data": [
            {"id": "id-1", "name": "maaspal-lifecycle-key-1", "status": "active"},
            {"id": "id-2", "name": "maaspal-lifecycle-key-2", "status": "active"},
        ], "has_more": False},
    )

    ctx = _make_ctx({"api_keys": [{"id": "id-1", "key": "sk-1"}, {"id": "id-2", "key": "sk-2"}]})
    task = VerifyApiKeySearchTask("verify_api_key_search", {"name_prefix": "maaspal-lifecycle-key"})
    result = await task.run(ctx)

    assert result.status == "PASS"
    assert ctx.shared_state["search_check"] == {"found_count": 2, "expected_count": 2}


async def test_verify_api_key_search_sends_name_prefix(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys/search",
        method="POST",
        json={"data": [], "has_more": False},
    )

    task = VerifyApiKeySearchTask("verify_api_key_search", {"name_prefix": "maaspal-lifecycle-key"})
    await task.run(_make_ctx())

    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert body == {"name_prefix": "maaspal-lifecycle-key"}

    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert "subscription" not in body


async def test_provision_keys_distributed_target_model_uses_namespace_name(
    httpx_mock: HTTPXMock,
) -> None:
    """target_model must be "<namespace>/<name>" (matching /v1/models' own
    owned_by format) — a bare CR name never routes correctly, and a model
    deployed this run may not even be visible in discovery yet."""
    httpx_mock.add_response(
        url="http://maas.test/maas-api/v1/api-keys",
        method="POST",
        json={"id": "id-1", "key": "sk-1", "name": "maaspal-dist-key-1", "subscription": "sub-1"},
    )

    ctx = _make_ctx(
        {
            "distributed_subscriptions": [
                {
                    "name": "sub-1",
                    "namespace": "models-as-a-service",
                    "model_refs": [{"name": "maaspal-sim-abcd-1", "namespace": "maaspal"}],
                }
            ]
        }
    )
    task = ProvisionKeysDistributedTask(
        "provision_keys_distributed", {"key_count": 1, "key_name_prefix": "maaspal-dist-key"}
    )
    await task.run(ctx)

    assert ctx.shared_state["api_keys"][0]["target_model"] == "maaspal/maaspal-sim-abcd-1"
