"""Read-only access to the live MaaS domain model (subscriptions, models) and
the resources they reference, for the MaaS overview page in the UI.

Every list/get call is wrapped so a missing CRD or missing RBAC degrades to
an ``unavailable`` result for just that resource type rather than raising —
one section being unreadable must not blank out a section backed by a
different RBAC grant. See docs/architecture/adrs/ADR-017-maas-cluster-visibility.md
and chart/templates/rbac-maas-readonly.yaml for the permissions this relies on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml
from kubernetes import client as k8s
from kubernetes import config as k8s_cfg
from kubernetes.client import ApiException

_MAAS_GROUP = "maas.opendatahub.io"
_MAAS_VERSION = "v1alpha1"
_KSERVE_GROUP = "serving.kserve.io"
_KSERVE_VERSION = "v1alpha2"
_OPENSHIFT_USER_GROUP = "user.openshift.io"
_OPENSHIFT_USER_VERSION = "v1"
_KUADRANT_GROUP = "kuadrant.io"
_KUADRANT_VERSION = "v1alpha1"
_LIMITADOR_GROUP = "limitador.kuadrant.io"
_LIMITADOR_VERSION = "v1alpha1"
_GATEWAY_GROUP = "gateway.networking.k8s.io"
_GATEWAY_VERSION = "v1"
_DSC_GROUP = "datasciencecluster.opendatahub.io"
_DSC_VERSION = "v2"
_ODH_DASHBOARD_GROUP = "opendatahub.io"
_ODH_DASHBOARD_VERSION = "v1alpha"
# RHOAI 3.5+ moved ExternalModel out of maas.opendatahub.io into its own group,
# split into ExternalProvider (endpoint/auth) + ExternalModel (references a
# provider) — see docs/architecture/maas-domain-reference.md Catalog item B.
# This targets 3.5+ only; a 3.4 cluster's ExternalModel (maas.opendatahub.io/v1alpha1,
# single CRD) is not read here.
_INFERENCE_GROUP = "inference.opendatahub.io"
_INFERENCE_VERSION = "v1alpha1"
_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"

# Required on a model's namespace for the Gateway to accept HTTPRoutes from
# it at all — confirmed live and against the community RHOAI MaaS guide
# (https://rh-aiservices-bu.github.io/rhoai-maas-guide/). Applies to internal
# (LLMInferenceService) and external (ExternalModel) model namespaces alike.
_GATEWAY_ACCESS_LABEL = "maas.opendatahub.io/gateway-access"

# Required on the credential Secret an ExternalProvider's spec.auth.secretRef
# points to, so the gateway's wasm-shim can find and inject it (RHOAI 3.5+;
# see the community guide's external-models page). Never read the Secret's
# contents — existence + this label is all `_secret_has_label` checks.
_CREDENTIAL_SECRET_LABEL = "inference.llm-d.ai/ipp-managed"

_kube_loaded = False


def _kube() -> None:
    global _kube_loaded
    if _kube_loaded:
        return
    try:
        k8s_cfg.load_incluster_config()
    except k8s_cfg.ConfigException:
        k8s_cfg.load_kube_config()
    _kube_loaded = True


def sa_token() -> str:
    try:
        return Path(_SA_TOKEN_PATH).read_text().strip()
    except OSError:
        return ""


def maas_api_url() -> str:
    return os.environ.get("MAAS_API_URL", "")


@dataclass
class ResourceList:
    """Result of a best-effort cluster read."""

    available: bool
    items: list[dict] = field(default_factory=list)
    reason: str | None = None  # "forbidden" | "not_installed" | "error_<n>"


def _reason_for(exc: ApiException) -> str:
    if exc.status == 403:
        return "forbidden"
    if exc.status == 404:
        return "not_installed"
    return f"error_{exc.status}"


def _list(group: str, version: str, plural: str) -> ResourceList:
    """List a custom resource across all namespaces (or cluster-scoped).

    Kubernetes serves this the same way `kubectl get <plural> -A` does — no
    namespace segment in the URL — for both cluster-scoped and namespaced
    CRDs, as long as the caller has a cluster-wide list grant.
    """
    _kube()
    api = k8s.CustomObjectsApi()
    try:
        result = api.list_cluster_custom_object(group=group, version=version, plural=plural)
        return ResourceList(available=True, items=result.get("items", []))
    except ApiException as exc:
        return ResourceList(available=False, reason=_reason_for(exc))
    except Exception:
        # The API server itself couldn't be reached at all (DNS/network/TLS
        # failure) — distinct from an ApiException, which means the request
        # reached a real API server and got a structured error back. Kept as
        # a short code, not str(exc), since that exception text is a raw
        # urllib3 connection error not fit for display in the UI.
        return ResourceList(available=False, reason="unreachable")


def _get(group: str, version: str, namespace: str, plural: str, name: str) -> dict | None:
    _kube()
    api = k8s.CustomObjectsApi()
    try:
        return api.get_namespaced_custom_object(
            group=group, version=version, namespace=namespace, plural=plural, name=name
        )
    except Exception:
        return None


def _namespace_has_gateway_access_label(name: str) -> bool | None:
    """Whether a namespace carries `maas.opendatahub.io/gateway-access: "true"`.

    Returns None (not False) when the namespace can't be read at all — RBAC
    denial or the namespace not existing must read as "unknown", never as a
    confirmed-missing label the UI would flag as a misconfiguration.
    """
    _kube()
    api = k8s.CoreV1Api()
    try:
        ns = api.read_namespace(name)
        labels = ns.metadata.labels or {}
        return labels.get(_GATEWAY_ACCESS_LABEL) == "true"
    except Exception:
        return None


def _secret_has_label(namespace: str, name: str, label: str) -> bool | None:
    """Whether a named Secret carries `label=true`. Only ever `get`s one
    specific, already-known secret name (never lists/enumerates secrets in a
    namespace) and only ever inspects `.metadata.labels` — the Secret's
    `data`/`stringData` is never read or exposed. Returns None (not False)
    when the secret can't be read at all, same convention as
    `_namespace_has_gateway_access_label`.
    """
    _kube()
    api = k8s.CoreV1Api()
    try:
        secret = api.read_namespaced_secret(name, namespace)
        labels = secret.metadata.labels or {}
        return labels.get(label) == "true"
    except Exception:
        return None


def _condition(conditions: list[dict], cond_type: str) -> dict | None:
    for c in conditions or []:
        if c.get("type") == cond_type:
            return c
    return None


def _is_ready(conditions: list[dict], cond_type: str = "Ready") -> bool:
    cond = _condition(conditions, cond_type)
    return bool(cond and cond.get("status") == "True")


def _to_yaml(obj: dict) -> str:
    """Render a raw cluster object the same way the run-settings YAML viewer
    (api/routes/config.py) does, so "View YAML" looks identical everywhere."""
    return yaml.safe_dump(obj, sort_keys=False, default_flow_style=False)


def _modelref_lookup() -> dict[tuple[str | None, str | None], dict] | None:
    """`{(namespace, name): {display_name, ready}}` built from a live
    `maasmodelrefs` list — shared by `list_subscriptions()`/`list_auth_policies()`
    so each can resolve its own `spec.modelRefs[]` entries against the real
    MaaSModelRef catalog (a dangling reference is a real misconfiguration on
    either side). `None` only when the read itself failed — never collapsed
    into "no model refs exist."
    """
    modelrefs_result = _list(_MAAS_GROUP, _MAAS_VERSION, "maasmodelrefs")
    if not modelrefs_result.available:
        return None
    lookup: dict[tuple[str | None, str | None], dict] = {}
    for cr in modelrefs_result.items:
        meta = cr.get("metadata", {})
        annotations = meta.get("annotations", {})
        lookup[(meta.get("namespace"), meta.get("name"))] = {
            "display_name": annotations.get("openshift.io/display-name", meta.get("name")),
            "ready": _is_ready(cr.get("status", {}).get("conditions", [])),
        }
    return lookup


def list_subscriptions() -> ResourceList:
    """`MaaSSubscription` grants quota — it does NOT create the `MaaSModelRef`s
    it references (those are created separately, by publishing a model, see
    Catalog item B). It also does not create the `MaaSAuthPolicy` that governs
    gateway access for the same owners at the CRD/controller level — though
    the RHOAI Dashboard's own subscription-creation UI flow does auto-create
    one as a one-time convenience (verified live; see Catalog item A) — either
    way a `MaaSAuthPolicy` is an independently-existing object, not something
    this function can assume exists. Both are looked up here purely for
    *display*, per-model, so a dangling model reference or a missing auth
    policy is visible right on the subscription that would otherwise silently
    have no effect.
    """
    result = _list(_MAAS_GROUP, _MAAS_VERSION, "maassubscriptions")
    if not result.available:
        return result

    modelref_by_key = _modelref_lookup()

    auth_result = list_auth_policies()
    auth_items = auth_result.items if auth_result.available else None

    def _has_matching_auth_policy(owner: dict, namespace: str | None, name: str | None) -> bool | None:
        if auth_items is None:
            return None
        owner_names = set(owner["groups"]) | set(owner["users"])
        for policy in auth_items:
            policy_names = set(policy["owner"]["groups"]) | set(policy["owner"]["users"])
            if owner_names & policy_names and any(
                m["namespace"] == namespace and m["name"] == name for m in policy["model_refs"]
            ):
                return True
        return False

    shaped = []
    for cr in result.items:
        meta = cr.get("metadata", {})
        spec = cr.get("spec", {})
        status = cr.get("status", {})
        annotations = meta.get("annotations", {})
        conditions = status.get("conditions", [])
        conflict_cond = _condition(conditions, "SpecPriorityDuplicate")

        owner = {
            "groups": [g.get("name") for g in (spec.get("owner", {}).get("groups") or [])],
            "users": spec.get("owner", {}).get("users") or [],
        }

        model_refs = []
        for m in spec.get("modelRefs") or []:
            m_name, m_namespace = m.get("name"), m.get("namespace")
            ref_info = modelref_by_key.get((m_namespace, m_name)) if modelref_by_key is not None else None
            model_exists = (ref_info is not None) if modelref_by_key is not None else None
            model_refs.append(
                {
                    "name": m_name,
                    "namespace": m_namespace,
                    "token_rate_limits": m.get("tokenRateLimits") or [],
                    "display_name": ref_info["display_name"] if ref_info else m_name,
                    "model_exists": model_exists,
                    "model_ready": ref_info["ready"] if ref_info else None,
                    "has_auth_policy": _has_matching_auth_policy(owner, m_namespace, m_name),
                }
            )

        shaped.append(
            {
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "display_name": annotations.get("openshift.io/display-name", meta.get("name")),
                "description": annotations.get("openshift.io/description", ""),
                "priority": spec.get("priority"),
                "owner": owner,
                "model_refs": model_refs,
                "phase": status.get("phase"),
                "ready": _is_ready(conditions),
                "priority_conflict": bool(conflict_cond and conflict_cond.get("status") == "True"),
                "raw": cr,
                "raw_yaml": _to_yaml(cr),
            }
        )

    return ResourceList(available=True, items=shaped)


def _fetch_v1_models() -> tuple[bool, list[dict]]:
    """GET {MAAS_API_URL}/v1/models — used to cross-reference which
    subscriptions grant each model, since that reverse index isn't on the
    MaaSModelRef CR itself.
    """
    url = maas_api_url()
    token = sa_token()
    if not url or not token:
        return False, []
    try:
        resp = httpx.get(
            f"{url}/v1/models",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return True, resp.json().get("data", [])
    except Exception:
        return False, []


def list_models() -> ResourceList:
    modelrefs_result = _list(_MAAS_GROUP, _MAAS_VERSION, "maasmodelrefs")
    externalmodels_result = _list(_INFERENCE_GROUP, _INFERENCE_VERSION, "externalmodels")
    externalproviders_result = _list(_INFERENCE_GROUP, _INFERENCE_VERSION, "externalproviders")
    providers_by_key = (
        {(p.get("metadata", {}).get("namespace"), p.get("metadata", {}).get("name")): p
         for p in externalproviders_result.items}
        if externalproviders_result.available else {}
    )

    if not modelrefs_result.available and not externalmodels_result.available:
        # Neither readable — report the MaaSModelRef reason since that's the
        # primary resource; ExternalModel is optional/tech-preview.
        return modelrefs_result

    _, rest_models = _fetch_v1_models()
    rest_by_owner = {m.get("owned_by"): m for m in rest_models}

    # A MaaSModelRef only reaches Ready when BOTH a MaaSSubscription AND a
    # MaaSAuthPolicy reference it (confirmed live + community guide) — the
    # Models tab already shows subscription coverage via `subscriptions[]`
    # below; this adds the other half. `None` (not False) when auth policies
    # can't be read at all, since "no policy" and "can't tell" are different
    # things to show an admin. Uses list_auth_policies() (not a raw _list()
    # call) so each matching policy carries a display name/readiness/raw_yaml
    # too — the Models tab surfaces these directly (clickable "View YAML"),
    # not just the has_auth_policy boolean, since with the boolean alone a
    # missing policy was one click away from nowhere.
    auth_result = list_auth_policies()
    auth_policies_by_model: dict[tuple[str | None, str | None], list[dict]] | None = None
    if auth_result.available:
        auth_policies_by_model = {}
        for policy in auth_result.items:
            for m in policy["model_refs"]:
                key = (m["namespace"], m["name"])
                auth_policies_by_model.setdefault(key, []).append(policy)

    def _auth_policies_for(namespace: str | None, name: str | None) -> list[dict] | None:
        if auth_policies_by_model is None:
            return None
        return [
            {
                "name": p["name"],
                "namespace": p["namespace"],
                "display_name": p["display_name"],
                "ready": p["ready"],
                "raw_yaml": p["raw_yaml"],
            }
            for p in auth_policies_by_model.get((namespace, name), [])
        ]

    # Cache namespace label lookups within this call — several models
    # commonly share one namespace (e.g. all internally-served models in `llm`).
    _namespace_gateway_access: dict[str, bool | None] = {}

    def _gateway_access_for(namespace: str) -> bool | None:
        if namespace not in _namespace_gateway_access:
            _namespace_gateway_access[namespace] = _namespace_has_gateway_access_label(namespace)
        return _namespace_gateway_access[namespace]

    shaped = []
    for cr in modelrefs_result.items if modelrefs_result.available else []:
        meta = cr.get("metadata", {})
        spec = cr.get("spec", {})
        status = cr.get("status", {})
        annotations = meta.get("annotations", {})
        name, namespace = meta.get("name"), meta.get("namespace")
        conditions = status.get("conditions", [])

        model_ref = spec.get("modelRef", {})
        rest_entry = rest_by_owner.get(f"{namespace}/{name}")

        # "Internal" here means served through OpenShift AI itself
        # (LLMInferenceService); anything else (a future backing kind, or no
        # modelRef.kind at all) is treated as external/unknown rather than
        # silently assumed internal.
        hosting = "internal" if model_ref.get("kind") == "LLMInferenceService" else "external"

        serving = None
        llm_isvc = None
        if model_ref.get("kind") == "LLMInferenceService":
            llm_isvc = _get(
                _KSERVE_GROUP, _KSERVE_VERSION, namespace, "llminferenceservices",
                model_ref.get("name"),
            )
            if llm_isvc:
                llm_spec = llm_isvc.get("spec", {})
                llm_status = llm_isvc.get("status", {})
                containers = llm_spec.get("template", {}).get("containers", [])
                serving = {
                    "replicas": llm_spec.get("replicas"),
                    "resources": containers[0].get("resources") if containers else None,
                    "conditions": llm_status.get("conditions", []),
                }

        model_auth_policies = _auth_policies_for(namespace, name)

        shaped.append(
            {
                "name": name,
                "namespace": namespace,
                "display_name": annotations.get("openshift.io/display-name", name),
                "description": annotations.get("openshift.io/description", ""),
                "kind": model_ref.get("kind"),
                "hosting": hosting,
                "backing_name": model_ref.get("name"),
                "phase": status.get("phase"),
                "ready": _is_ready(conditions),
                "endpoint": status.get("endpoint"),
                # "<namespace>/<HTTPRoute name>" — exactly the format
                # Limitador's limitador_namespace metric label uses (ADR-014),
                # so the run launcher can fill it from the model picker
                # instead of scenarios hard-coding one cluster's route.
                "http_route": (
                    f"{status['httpRouteNamespace']}/{status['httpRouteName']}"
                    if status.get("httpRouteNamespace") and status.get("httpRouteName")
                    else None
                ),
                "subscriptions": [
                    {
                        "name": s.get("name"),
                        "display_name": s.get("displayName"),
                        "description": s.get("description"),
                    }
                    for s in ((rest_entry or {}).get("subscriptions") or [])
                ],
                "has_auth_policy": (
                    len(model_auth_policies) > 0 if model_auth_policies is not None else None
                ),
                "auth_policies": model_auth_policies or [],
                "gateway_access_label": _gateway_access_for(namespace),
                "external_providers": [],
                "serving": serving,
                "raw": cr,
                # Just the MaaSModelRef's own YAML — the backing
                # LLMInferenceService (a separate real object) gets its own
                # serving_raw_yaml below rather than being merged into one
                # synthetic multi-object document.
                "raw_yaml": _to_yaml(cr),
                "serving_raw_yaml": _to_yaml(llm_isvc) if llm_isvc else None,
            }
        )

    for cr in externalmodels_result.items if externalmodels_result.available else []:
        meta = cr.get("metadata", {})
        spec = cr.get("spec", {})
        status = cr.get("status", {})
        annotations = meta.get("annotations", {})
        name, namespace = meta.get("name"), meta.get("namespace")

        resolved_providers = []
        for pref in spec.get("externalProviderRefs") or []:
            ref = pref.get("ref", {})
            provider_name = ref.get("name")
            # The guide's examples always co-locate ExternalProvider and
            # ExternalModel; `ref` carries no namespace field of its own, so
            # same-namespace is the only resolution rule there is to follow.
            provider_cr = providers_by_key.get((namespace, provider_name))
            provider_spec = (provider_cr or {}).get("spec", {})
            secret_ref = provider_spec.get("auth", {}).get("secretRef", {})
            secret_name = secret_ref.get("name")
            secret_label_ok = (
                _secret_has_label(namespace, secret_name, _CREDENTIAL_SECRET_LABEL)
                if secret_name else None
            )
            resolved_providers.append(
                {
                    "provider_name": provider_name,
                    "target_model": pref.get("targetModel"),
                    "api_format": pref.get("apiFormat"),
                    "path": pref.get("path"),
                    "endpoint": provider_spec.get("endpoint"),
                    "credential_secret_name": secret_name,
                    "credential_secret_label_ok": secret_label_ok,
                    # This provider's own ExternalProvider CR YAML alone —
                    # None when it couldn't be resolved, not merged into the
                    # ExternalModel's own raw_yaml below.
                    "raw_yaml": _to_yaml(provider_cr) if provider_cr is not None else None,
                }
            )

        model_auth_policies = _auth_policies_for(namespace, name)

        shaped.append(
            {
                "name": name,
                "namespace": namespace,
                "display_name": annotations.get("openshift.io/display-name", name),
                "description": annotations.get("openshift.io/description", ""),
                "kind": "ExternalModel",
                "hosting": "external",
                "backing_name": spec.get("modelName"),
                "phase": status.get("phase"),
                "ready": _is_ready(status.get("conditions", [])),
                "endpoint": resolved_providers[0]["endpoint"] if resolved_providers else None,
                "subscriptions": [],
                "has_auth_policy": (
                    len(model_auth_policies) > 0 if model_auth_policies is not None else None
                ),
                "auth_policies": model_auth_policies or [],
                "gateway_access_label": _gateway_access_for(namespace),
                "external_providers": resolved_providers,
                "serving": None,
                "raw": cr,
                # Just the ExternalModel's own YAML — each provider's YAML is
                # on its own external_providers[] entry (raw_yaml above).
                "raw_yaml": _to_yaml(cr),
                "serving_raw_yaml": None,
            }
        )

    seen: set[tuple[str | None, str | None]] = set()
    deduped = []
    for item in shaped:
        key = (item["namespace"], item["name"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return ResourceList(available=True, items=deduped)


def list_auth_policies() -> ResourceList:
    """`MaaSAuthPolicy` — grants gateway access (separate from subscription
    quota). Shaped to mirror `list_subscriptions()` (`owner.{groups,users}`,
    per-model `model_refs[]`) so the two can be compared model-by-model the
    same way — see Catalog item C and `resolve_access()`, which is exactly
    that comparison for a candidate set of groups. Also backs its own
    "Authorization Policies" tab, listing every `MaaSAuthPolicy` CR directly —
    it's an independently-created object (see Catalog item A's correction:
    the RHOAI Dashboard auto-creates one alongside a subscription as a
    one-time convenience, but nothing enforces that pairing afterwards),
    so it gets the same direct-listing treatment `list_subscriptions()` does.
    """
    result = _list(_MAAS_GROUP, _MAAS_VERSION, "maasauthpolicies")
    if not result.available:
        return result

    modelref_by_key = _modelref_lookup()

    shaped = []
    for cr in result.items:
        meta = cr.get("metadata", {})
        spec = cr.get("spec", {})
        status = cr.get("status", {})
        annotations = meta.get("annotations", {})
        subjects = spec.get("subjects", {})

        model_refs = []
        for m in spec.get("modelRefs") or []:
            m_name, m_namespace = m.get("name"), m.get("namespace")
            ref_info = modelref_by_key.get((m_namespace, m_name)) if modelref_by_key is not None else None
            model_exists = (ref_info is not None) if modelref_by_key is not None else None
            model_refs.append(
                {
                    "name": m_name,
                    "namespace": m_namespace,
                    "display_name": ref_info["display_name"] if ref_info else m_name,
                    "model_exists": model_exists,
                    "model_ready": ref_info["ready"] if ref_info else None,
                }
            )

        shaped.append(
            {
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "display_name": annotations.get("openshift.io/display-name", meta.get("name")),
                "description": annotations.get("openshift.io/description", ""),
                "owner": {
                    "groups": [g.get("name") for g in (subjects.get("groups") or [])],
                    "users": subjects.get("users") or [],
                },
                "model_refs": model_refs,
                "phase": status.get("phase"),
                "ready": _is_ready(status.get("conditions", [])),
                "raw_yaml": _to_yaml(cr),
            }
        )

    return ResourceList(available=True, items=shaped)


def list_access() -> ResourceList:
    """Cross-reference subscriptions (quota), MaaSAuthPolicy (gateway access),
    and OpenShift Groups (membership) into one row per group — see Catalog
    item C in docs/architecture/maas-domain-reference.md for why both a
    subscription AND a matching auth policy are required, and what it means
    when a group has one but not the other.

    Needs subscriptions AND auth policies to do anything meaningful (the
    whole point is comparing them), so either being unreadable makes the
    section unavailable as a whole. Groups is treated as a softer
    dependency — its absence still lets us show group names, subscriptions
    and auth policies; it just can't resolve them to member usernames.
    """
    subs = list_subscriptions()
    if not subs.available:
        return subs

    auth = list_auth_policies()
    if not auth.available:
        return auth

    groups = _list(_OPENSHIFT_USER_GROUP, _OPENSHIFT_USER_VERSION, "groups")
    group_users: dict[str, list[str]] | None = None
    group_raw_yaml: dict[str, str] = {}
    if groups.available:
        group_users = {
            g.get("metadata", {}).get("name"): g.get("users") or [] for g in groups.items
        }
        group_raw_yaml = {
            g.get("metadata", {}).get("name"): _to_yaml(g) for g in groups.items
        }

    shaped_auth = [
        {**a, "groups": a["owner"]["groups"], "models": [(m["namespace"], m["name"]) for m in a["model_refs"]]}
        for a in auth.items
    ]

    group_names: set[str] = set()
    for sub in subs.items:
        group_names.update(sub["owner"]["groups"])
    for ap in shaped_auth:
        group_names.update(ap["groups"])
    if group_users is not None:
        group_names.update(group_users.keys())

    rows = []
    for name in sorted(group_names):
        subs_for_group = [s for s in subs.items if name in s["owner"]["groups"]]
        auth_for_group = [a for a in shaped_auth if name in a["groups"]]

        quota_models = {(m["namespace"], m["name"]) for s in subs_for_group for m in s["model_refs"]}
        access_models = {m for a in auth_for_group for m in a["models"]}

        rows.append(
            {
                "name": name,
                "users": group_users.get(name) if group_users is not None else None,
                "raw_yaml": group_raw_yaml.get(name),
                "subscriptions": [
                    {
                        "name": s["name"],
                        "display_name": s["display_name"],
                        "priority": s["priority"],
                        "raw_yaml": s["raw_yaml"],
                    }
                    for s in subs_for_group
                ],
                "auth_policies": [
                    {
                        "name": a["name"],
                        "display_name": a["display_name"],
                        "ready": a["ready"],
                        "raw_yaml": a["raw_yaml"],
                    }
                    for a in auth_for_group
                ],
                "quota_without_access": sorted(f"{ns}/{n}" for ns, n in quota_models - access_models),
                "access_without_quota": sorted(f"{ns}/{n}" for ns, n in access_models - quota_models),
            }
        )

    return ResourceList(available=True, items=rows)


def list_rate_limit_policies() -> ResourceList:
    """`TokenRateLimitPolicy` (Kuadrant) — the object a `MaaSSubscription`
    actually compiles down to. See Catalog item D. Deliberately does not
    also list Authorino `AuthConfig` (the analogous object beneath
    `MaaSAuthPolicy`) — those are hash-named and live alongside every other
    AuthConfig in `kuadrant-system` with no reliable way to filter to only
    MaaS-relevant ones, so listing them would be noise rather than signal.
    """
    result = _list(_KUADRANT_GROUP, _KUADRANT_VERSION, "tokenratelimitpolicies")
    if not result.available:
        return result

    shaped = []
    for cr in result.items:
        meta = cr.get("metadata", {})
        spec = cr.get("spec", {})
        status = cr.get("status", {})
        target_ref = spec.get("targetRef", {})
        limits = spec.get("limits") or spec.get("defaults", {}).get("limits") or {}
        conditions = status.get("conditions", [])
        shaped.append(
            {
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "target_kind": target_ref.get("kind"),
                "target_name": target_ref.get("name"),
                "limit_names": sorted(limits.keys()),
                "accepted": _is_ready(conditions, "Accepted"),
                "enforced": _is_ready(conditions, "Enforced"),
                "raw_yaml": _to_yaml(cr),
            }
        )

    return ResourceList(available=True, items=shaped)


def list_limitador() -> ResourceList:
    """`Limitador` — the cluster-singleton counter definitions Envoy/Limitador
    actually enforce, the literal compiled form of every `TokenRateLimitPolicy`.
    See Catalog item D."""
    result = _list(_LIMITADOR_GROUP, _LIMITADOR_VERSION, "limitadors")
    if not result.available:
        return result

    shaped = []
    for cr in result.items:
        meta = cr.get("metadata", {})
        spec = cr.get("spec", {})
        status = cr.get("status", {})
        service = status.get("service", {})
        shaped.append(
            {
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "limit_count": len(spec.get("limits") or []),
                "ready": _is_ready(status.get("conditions", [])),
                "service_host": service.get("host"),
                "raw_yaml": _to_yaml(cr),
            }
        )

    return ResourceList(available=True, items=shaped)


def list_gateways() -> ResourceList:
    """`Gateway` (Gateway API) — the entry point every model route attaches
    to. See Catalog item F."""
    result = _list(_GATEWAY_GROUP, _GATEWAY_VERSION, "gateways")
    if not result.available:
        return result

    shaped = []
    for cr in result.items:
        meta = cr.get("metadata", {})
        spec = cr.get("spec", {})
        status = cr.get("status", {})
        addresses = status.get("addresses") or []
        conditions = status.get("conditions", [])
        shaped.append(
            {
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "gateway_class": spec.get("gatewayClassName"),
                "address": addresses[0].get("value") if addresses else None,
                "programmed": _is_ready(conditions, "Programmed"),
                "raw_yaml": _to_yaml(cr),
            }
        )

    return ResourceList(available=True, items=shaped)


def list_http_routes() -> ResourceList:
    """`HTTPRoute` (Gateway API) — one per model, routing gateway traffic to
    the backing Service. `owning_model`/`owning_model_namespace` come from
    the route's `ownerReferences` (set by the KServe/LLMInferenceService
    controller), letting the UI trace a route straight back to its model
    without guessing from the route's name. See Catalog item F."""
    result = _list(_GATEWAY_GROUP, _GATEWAY_VERSION, "httproutes")
    if not result.available:
        return result

    shaped = []
    for cr in result.items:
        meta = cr.get("metadata", {})
        spec = cr.get("spec", {})
        owner_refs = meta.get("ownerReferences") or []
        owning_model = next(
            (o for o in owner_refs if o.get("kind") == "LLMInferenceService"), None
        )
        parent_refs = spec.get("parentRefs") or []
        parent = parent_refs[0] if parent_refs else {}
        shaped.append(
            {
                "name": meta.get("name"),
                "namespace": meta.get("namespace"),
                "parent_gateway": parent.get("name"),
                "parent_gateway_namespace": parent.get("namespace"),
                "owning_model": owning_model.get("name") if owning_model else None,
                "raw_yaml": _to_yaml(cr),
            }
        )

    return ResourceList(available=True, items=shaped)


def list_platform() -> dict:
    """Platform-level MaaS configuration — `Tenant`/`MaasTenantConfig`,
    `DataScienceCluster` (confirms MaaS itself is enabled), and
    `OdhDashboardConfig` (adjacent dashboard feature flags). See Catalog
    item E.

    Returns a plain dict, not a `ResourceList` — unlike every other
    `list_*()` here, this isn't one list of similar objects but three
    independently-degrading settings sections (each backed by a different
    RBAC grant), so each gets its own `available`/`reason` inline.
    """
    tenants = _list(_MAAS_GROUP, _MAAS_VERSION, "tenants")
    shaped_tenants = []
    if tenants.available:
        for cr in tenants.items:
            meta = cr.get("metadata", {})
            spec = cr.get("spec", {})
            status = cr.get("status", {})
            shaped_tenants.append(
                {
                    "name": meta.get("name"),
                    "namespace": meta.get("namespace"),
                    "gateway_ref": spec.get("gatewayRef"),
                    "max_api_key_expiration_days": spec.get("apiKeys", {}).get("maxExpirationDays"),
                    "telemetry_enabled": spec.get("telemetry", {}).get("enabled"),
                    "phase": status.get("phase"),
                    "raw_yaml": _to_yaml(cr),
                }
            )

    dsc = _list(_DSC_GROUP, _DSC_VERSION, "datascienceclusters")
    dsc_item = None
    if dsc.available and dsc.items:
        cr = dsc.items[0]
        components = cr.get("spec", {}).get("components", {})
        # Field path is RHOAI-version-dependent: 3.5+ uses
        # aigateway.modelsAsAService, 3.4 uses the deprecated
        # kserve.modelsAsService — check both rather than assume one, since a
        # given cluster could be running either.
        aigateway_state = components.get("aigateway", {}).get("modelsAsAService", {}).get("managementState")
        kserve_state = components.get("kserve", {}).get("modelsAsService", {}).get("managementState")
        dsc_item = {
            "name": cr.get("metadata", {}).get("name"),
            "maas_management_state": aigateway_state or kserve_state,
            "maas_field_path": (
                "aigateway.modelsAsAService" if aigateway_state
                else "kserve.modelsAsService (deprecated)" if kserve_state
                else None
            ),
            "raw_yaml": _to_yaml(cr),
        }

    odh = _list(_ODH_DASHBOARD_GROUP, _ODH_DASHBOARD_VERSION, "odhdashboardconfigs")
    odh_item = None
    if odh.available and odh.items:
        cr = odh.items[0]
        flags = cr.get("spec", {}).get("dashboardConfig", {})
        odh_item = {
            "name": cr.get("metadata", {}).get("name"),
            "model_as_service": flags.get("modelAsService"),
            "external_models": flags.get("externalModels"),
            "gen_ai_studio": flags.get("genAiStudio"),
            "observability_dashboard": flags.get("observabilityDashboard"),
            "raw_yaml": _to_yaml(cr),
        }

    return {
        "tenants": {"available": tenants.available, "reason": tenants.reason, "items": shaped_tenants},
        "data_science_cluster": {"available": dsc.available, "reason": dsc.reason, "item": dsc_item},
        "odh_dashboard_config": {"available": odh.available, "reason": odh.reason, "item": odh_item},
    }
