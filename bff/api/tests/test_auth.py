"""Access gate (api/auth.py, ADR-026): every route needs a dashboard user
token whose SelfSubjectAccessReview allows the virtual maaspal permission."""

import httpx
import pytest

import api.auth


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch) -> None:
    monkeypatch.setenv("MAASPAL_AUTH_MODE", "sar")
    monkeypatch.setenv("K8S_API_BASE", "https://k8s.test")
    monkeypatch.setenv("NAMESPACE", "cp-maaspal")
    api.auth._decisions.clear()


@pytest.fixture()
async def client(tmp_path, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    from api.db import init_db
    from api.main import app

    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _sar_response(allowed: bool) -> dict:
    return {"kind": "SelfSubjectAccessReview", "status": {"allowed": allowed}}


async def test_health_is_ungated(client) -> None:
    resp = await client.get("/api/health")
    assert resp.status_code == 200


async def test_missing_token_is_401(client) -> None:
    resp = await client.get("/api/runs")
    assert resp.status_code == 401


async def test_denied_user_is_403_and_names_the_role(client, httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://k8s.test/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
        json=_sar_response(False),
    )
    resp = await client.get("/api/runs", headers={"Authorization": "Bearer user-token"})
    assert resp.status_code == 403
    assert "maaspal-user" in resp.json()["detail"]
    assert "cp-maaspal" in resp.json()["detail"]


async def test_allowed_user_passes_and_review_uses_their_token(client, httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://k8s.test/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
        json=_sar_response(True),
    )
    resp = await client.get("/api/runs", headers={"Authorization": "Bearer user-token"})
    assert resp.status_code == 200

    [request] = httpx_mock.get_requests()
    assert request.headers["authorization"] == "Bearer user-token"
    attrs = __import__("json").loads(request.content)["spec"]["resourceAttributes"]
    assert attrs == {
        "namespace": "cp-maaspal",
        "verb": "use",
        "group": "maaspal.rh-ai-community-plugins.io",
        "resource": "harness",
    }


async def test_decision_is_cached_per_token(client, httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://k8s.test/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
        json=_sar_response(True),
    )
    headers = {"Authorization": "Bearer user-token"}
    assert (await client.get("/api/runs", headers=headers)).status_code == 200
    assert (await client.get("/api/runs", headers=headers)).status_code == 200
    assert len(httpx_mock.get_requests()) == 1


async def test_invalid_token_is_403(client, httpx_mock) -> None:
    httpx_mock.add_response(
        url="https://k8s.test/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
        status_code=401,
    )
    resp = await client.get("/api/runs", headers={"Authorization": "Bearer bad"})
    assert resp.status_code == 403


async def test_cluster_unreachable_is_503(client, httpx_mock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    resp = await client.get("/api/runs", headers={"Authorization": "Bearer user-token"})
    assert resp.status_code == 503
