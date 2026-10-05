from fastapi import APIRouter

from api import maas_client

router = APIRouter()


def _section(result: maas_client.ResourceList) -> dict:
    return {"available": result.available, "reason": result.reason}


def _items_response(result: maas_client.ResourceList) -> dict:
    return {"available": result.available, "reason": result.reason, "items": result.items}


@router.get("/api/maas/status")
async def get_maas_status() -> dict:
    """Cheap availability probe for each MaaS section, so the UI can hide/
    warn on sections the SA lacks RBAC for (or whose CRDs aren't installed
    on this cluster) instead of erroring. See ADR-017.
    """
    subscriptions = maas_client.list_subscriptions()
    models = maas_client.list_models()
    access = maas_client.list_access()
    auth_policies = maas_client.list_auth_policies()
    rate_limit_policies = maas_client.list_rate_limit_policies()
    limitador = maas_client.list_limitador()
    gateways = maas_client.list_gateways()
    http_routes = maas_client.list_http_routes()
    platform = maas_client.list_platform()
    return {
        "sections": {
            "subscriptions": _section(subscriptions),
            "models": _section(models),
            "access": _section(access),
            "auth_policies": _section(auth_policies),
            "rate_limit_policies": _section(rate_limit_policies),
            "limitador": _section(limitador),
            "gateways": _section(gateways),
            "http_routes": _section(http_routes),
            "tenants": {
                "available": platform["tenants"]["available"],
                "reason": platform["tenants"]["reason"],
            },
            "data_science_cluster": {
                "available": platform["data_science_cluster"]["available"],
                "reason": platform["data_science_cluster"]["reason"],
            },
            "odh_dashboard_config": {
                "available": platform["odh_dashboard_config"]["available"],
                "reason": platform["odh_dashboard_config"]["reason"],
            },
        }
    }


@router.get("/api/maas/subscriptions")
async def get_subscriptions() -> dict:
    return _items_response(maas_client.list_subscriptions())


@router.get("/api/maas/models")
async def get_models() -> dict:
    return _items_response(maas_client.list_models())


@router.get("/api/maas/access")
async def get_access() -> dict:
    return _items_response(maas_client.list_access())


@router.get("/api/maas/auth-policies")
async def get_auth_policies() -> dict:
    return _items_response(maas_client.list_auth_policies())


@router.get("/api/maas/rate-limit-policies")
async def get_rate_limit_policies() -> dict:
    return _items_response(maas_client.list_rate_limit_policies())


@router.get("/api/maas/limitador")
async def get_limitador() -> dict:
    return _items_response(maas_client.list_limitador())


@router.get("/api/maas/gateways")
async def get_gateways() -> dict:
    return _items_response(maas_client.list_gateways())


@router.get("/api/maas/http-routes")
async def get_http_routes() -> dict:
    return _items_response(maas_client.list_http_routes())


@router.get("/api/maas/platform")
async def get_platform() -> dict:
    return maas_client.list_platform()
