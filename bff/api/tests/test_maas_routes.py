"""API route tests for /api/maas/* — mocks api.maas_client entirely so these
don't need a real cluster (see api/tests/test_maas_client.py for the parsing
logic these routes are thin wrappers around)."""
from unittest.mock import patch

import pytest

from api import maas_client


@pytest.fixture(autouse=True)
def _temp_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))


@pytest.fixture()
async def client(_temp_db):
    from httpx import ASGITransport, AsyncClient

    from api.db import init_db
    from api.main import app

    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_subscriptions_available(client) -> None:
    fake = maas_client.ResourceList(available=True, items=[{"name": "simulator-free"}])
    with patch.object(maas_client, "list_subscriptions", return_value=fake):
        resp = await client.get("/api/maas/subscriptions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["items"] == [{"name": "simulator-free"}]


async def test_subscriptions_unavailable_reports_reason_not_error(client) -> None:
    fake = maas_client.ResourceList(available=False, reason="forbidden")
    with patch.object(maas_client, "list_subscriptions", return_value=fake):
        resp = await client.get("/api/maas/subscriptions")

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "forbidden"
    assert body["items"] == []


async def test_models_available(client) -> None:
    fake = maas_client.ResourceList(available=True, items=[{"name": "facebook-opt-125m-simulated"}])
    with patch.object(maas_client, "list_models", return_value=fake):
        resp = await client.get("/api/maas/models")

    assert resp.status_code == 200
    assert resp.json()["items"] == [{"name": "facebook-opt-125m-simulated"}]


async def test_access_available(client) -> None:
    fake = maas_client.ResourceList(available=True, items=[{"name": "team-a"}])
    with patch.object(maas_client, "list_access", return_value=fake):
        resp = await client.get("/api/maas/access")

    assert resp.status_code == 200
    assert resp.json()["items"] == [{"name": "team-a"}]


async def test_auth_policies_available(client) -> None:
    fake = maas_client.ResourceList(available=True, items=[{"name": "ap-a"}])
    with patch.object(maas_client, "list_auth_policies", return_value=fake):
        resp = await client.get("/api/maas/auth-policies")

    assert resp.status_code == 200
    assert resp.json()["items"] == [{"name": "ap-a"}]


async def test_rate_limit_policies_available(client) -> None:
    fake = maas_client.ResourceList(available=True, items=[{"name": "maas-trlp-x"}])
    with patch.object(maas_client, "list_rate_limit_policies", return_value=fake):
        resp = await client.get("/api/maas/rate-limit-policies")

    assert resp.status_code == 200
    assert resp.json()["items"] == [{"name": "maas-trlp-x"}]


async def test_limitador_available(client) -> None:
    fake = maas_client.ResourceList(available=True, items=[{"name": "limitador"}])
    with patch.object(maas_client, "list_limitador", return_value=fake):
        resp = await client.get("/api/maas/limitador")

    assert resp.status_code == 200
    assert resp.json()["items"] == [{"name": "limitador"}]


async def test_gateways_available(client) -> None:
    fake = maas_client.ResourceList(available=True, items=[{"name": "maas-default-gateway"}])
    with patch.object(maas_client, "list_gateways", return_value=fake):
        resp = await client.get("/api/maas/gateways")

    assert resp.status_code == 200
    assert resp.json()["items"] == [{"name": "maas-default-gateway"}]


async def test_http_routes_available(client) -> None:
    fake = maas_client.ResourceList(available=True, items=[{"name": "a-route"}])
    with patch.object(maas_client, "list_http_routes", return_value=fake):
        resp = await client.get("/api/maas/http-routes")

    assert resp.status_code == 200
    assert resp.json()["items"] == [{"name": "a-route"}]


async def test_platform_returns_client_shape_directly(client) -> None:
    fake_platform = {
        "tenants": {"available": True, "reason": None, "items": [{"name": "default-tenant"}]},
        "data_science_cluster": {"available": True, "reason": None, "item": {"name": "default-dsc"}},
        "odh_dashboard_config": {"available": False, "reason": "forbidden", "item": None},
    }
    with patch.object(maas_client, "list_platform", return_value=fake_platform):
        resp = await client.get("/api/maas/platform")

    assert resp.status_code == 200
    assert resp.json() == fake_platform


def _fake_platform_result(
    tenants_available: bool = True,
    dsc_available: bool = True,
    odh_available: bool = True,
) -> dict:
    return {
        "tenants": {"available": tenants_available, "reason": None if tenants_available else "forbidden", "items": []},
        "data_science_cluster": {"available": dsc_available, "reason": None if dsc_available else "forbidden", "item": None},
        "odh_dashboard_config": {"available": odh_available, "reason": None if odh_available else "forbidden", "item": None},
    }


async def test_status_reports_per_section_availability(client) -> None:
    subs = maas_client.ResourceList(available=False, reason="forbidden")
    models = maas_client.ResourceList(available=True, items=[])
    access = maas_client.ResourceList(available=False, reason="forbidden")
    auth_policies = maas_client.ResourceList(available=True, items=[])
    rate_limit_policies = maas_client.ResourceList(available=True, items=[])
    limitador = maas_client.ResourceList(available=True, items=[])
    gateways = maas_client.ResourceList(available=True, items=[])
    http_routes = maas_client.ResourceList(available=True, items=[])
    platform = _fake_platform_result(tenants_available=False)

    with patch.object(maas_client, "list_subscriptions", return_value=subs), \
         patch.object(maas_client, "list_models", return_value=models), \
         patch.object(maas_client, "list_access", return_value=access), \
         patch.object(maas_client, "list_auth_policies", return_value=auth_policies), \
         patch.object(maas_client, "list_rate_limit_policies", return_value=rate_limit_policies), \
         patch.object(maas_client, "list_limitador", return_value=limitador), \
         patch.object(maas_client, "list_gateways", return_value=gateways), \
         patch.object(maas_client, "list_http_routes", return_value=http_routes), \
         patch.object(maas_client, "list_platform", return_value=platform):
        resp = await client.get("/api/maas/status")

    assert resp.status_code == 200
    sections = resp.json()["sections"]
    assert sections["subscriptions"] == {"available": False, "reason": "forbidden"}
    assert sections["models"] == {"available": True, "reason": None}
    assert sections["access"] == {"available": False, "reason": "forbidden"}
    assert sections["auth_policies"] == {"available": True, "reason": None}
    assert sections["rate_limit_policies"] == {"available": True, "reason": None}
    assert sections["limitador"] == {"available": True, "reason": None}
    assert sections["gateways"] == {"available": True, "reason": None}
    assert sections["http_routes"] == {"available": True, "reason": None}
    assert sections["tenants"] == {"available": False, "reason": "forbidden"}
    assert sections["data_science_cluster"] == {"available": True, "reason": None}
    assert sections["odh_dashboard_config"] == {"available": True, "reason": None}
