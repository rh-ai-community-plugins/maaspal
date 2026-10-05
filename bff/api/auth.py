"""Access gate for every /api route (ADR-026).

The RHOAI dashboard reaches this backend through its `proxyService`, with
`authorize: true`, so each request carries the dashboard user's own OpenShift
token as `Authorization: Bearer <token>`. The backend itself acts with the
maaspal ServiceAccount, which holds far more than a typical dashboard user
(Jobs, MaaSSubscriptions, throwaway models, optionally minting SA tokens), so
without a check any dashboard user could use those rights.

The check is a SelfSubjectAccessReview made *with the user's token* for a
virtual permission — verb `use` on `harness.maaspal.rh-ai-community-plugins.io`
in the plugin's namespace. Nothing on the cluster serves that resource, so
granting it (the chart's `maaspal-user` Role) gives a user no real cluster
power; cluster-admins pass through their wildcard rules. The SAR needs no RBAC
on the maaspal ServiceAccount, since every user may review their own access.
"""

import hashlib
import os
import time
from pathlib import Path

import httpx
from fastapi import HTTPException, Request

_SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_CACHE_TTL_S = 60.0
_UNGATED_PATHS = {"/api/health"}

SAR_GROUP = "maaspal.rh-ai-community-plugins.io"
SAR_RESOURCE = "harness"
SAR_VERB = "use"

# sha256(token) → (allowed, expires_at). The UI polls every 1–2 s, so without
# this each poll would be one more SAR against the API server.
_decisions: dict[str, tuple[bool, float]] = {}


def _auth_mode() -> str:
    # "off" is for local development only (no dashboard in front, no token).
    return os.environ.get("MAASPAL_AUTH_MODE", "sar").lower()


def _namespace() -> str:
    return os.environ.get("NAMESPACE", "maaspal")


def _api_base() -> tuple[str, str | bool]:
    """Kubernetes API URL and TLS verify setting: in-cluster from the service
    env vars and the mounted CA, or K8S_API_BASE for local development."""
    base = os.environ.get("K8S_API_BASE")
    if base:
        insecure = os.environ.get("K8S_TLS_INSECURE", "").lower() == "true"
        return base.rstrip("/"), not insecure
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    ca = _SA_DIR / "ca.crt"
    return f"https://{host}:{port}", str(ca) if ca.exists() else True


async def _review(token: str) -> bool:
    base, verify = _api_base()
    body = {
        "apiVersion": "authorization.k8s.io/v1",
        "kind": "SelfSubjectAccessReview",
        "spec": {
            "resourceAttributes": {
                "namespace": _namespace(),
                "verb": SAR_VERB,
                "group": SAR_GROUP,
                "resource": SAR_RESOURCE,
            }
        },
    }
    async with httpx.AsyncClient(verify=verify, timeout=10.0) as client:
        resp = await client.post(
            f"{base}/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
    if resp.status_code in (401, 403):
        # An invalid or expired token can't even review itself.
        return False
    resp.raise_for_status()
    return bool(resp.json().get("status", {}).get("allowed"))


async def require_user(request: Request) -> None:
    """FastAPI dependency applied to the whole app; /api/health stays open for
    the kubelet's probes."""
    if request.url.path in _UNGATED_PATHS or _auth_mode() == "off":
        return

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="No user token. Open MaaS:PAL from the RHOAI dashboard.",
        )

    key = hashlib.sha256(token.strip().encode()).hexdigest()
    now = time.monotonic()
    cached = _decisions.get(key)
    if cached and cached[1] > now:
        allowed = cached[0]
    else:
        try:
            allowed = await _review(token.strip())
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503, detail=f"Could not check access with the cluster: {exc}"
            ) from exc
        _decisions[key] = (allowed, now + _CACHE_TTL_S)
        # Expired entries are dropped lazily so the cache can't grow forever.
        for k in [k for k, (_, exp) in _decisions.items() if exp <= now]:
            _decisions.pop(k, None)

    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=(
                f"You don't have access to MaaS:PAL. Ask an administrator to bind the "
                f"maaspal-user Role in namespace {_namespace()} to you or your group."
            ),
        )
