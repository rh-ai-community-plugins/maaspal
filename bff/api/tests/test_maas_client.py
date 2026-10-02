"""Unit tests for api/maas_client.py against fixture shapes captured live from
cluster-rkmhx.rkmhx.sandbox1230.opentlc.com (see docs/architecture/maas-domain-reference.md).
"""
from unittest.mock import MagicMock, patch

from kubernetes.client.exceptions import ApiException

from api import maas_client

_SUBSCRIPTION_FREE = {
    "metadata": {
        "name": "simulator-free",
        "namespace": "models-as-a-service",
        "annotations": {
            "openshift.io/display-name": "Simulator Free Tier",
            "openshift.io/description": "Free tier: 100 tokens/min for all authenticated users",
        },
    },
    "spec": {
        "priority": 10,
        "owner": {"groups": [{"name": "system:authenticated"}], "users": []},
        "modelRefs": [
            {
                "name": "facebook-opt-125m-simulated",
                "namespace": "llm",
                "tokenRateLimits": [{"limit": 100, "window": "1m"}],
            }
        ],
    },
    "status": {
        "phase": "Active",
        "conditions": [
            {"type": "Ready", "status": "True"},
            {"type": "SpecPriorityDuplicate", "status": "False"},
        ],
    },
}

_SUBSCRIPTION_CONFLICTING = {
    "metadata": {"name": "dup-sub", "namespace": "models-as-a-service", "annotations": {}},
    "spec": {"priority": 10, "owner": {"groups": [], "users": []}, "modelRefs": []},
    "status": {
        "phase": "Active",
        "conditions": [
            {"type": "Ready", "status": "True"},
            {"type": "SpecPriorityDuplicate", "status": "True"},
        ],
    },
}

_MODELREF = {
    "metadata": {
        "name": "facebook-opt-125m-simulated",
        "namespace": "llm",
        "annotations": {
            "openshift.io/display-name": "Facebook OPT 125M (Simulated)",
            "openshift.io/description": "CPU-only simulator for testing MaaS without a real LLM",
        },
    },
    "spec": {"modelRef": {"kind": "LLMInferenceService", "name": "facebook-opt-125m-simulated"}},
    "status": {
        "phase": "Ready",
        "conditions": [{"type": "Ready", "status": "True"}],
        "endpoint": "https://maas.example.com/llm/facebook-opt-125m-simulated",
        "httpRouteName": "facebook-opt-125m-simulated-kserve-route",
        "httpRouteNamespace": "llm",
    },
}

_LLM_ISVC = {
    "spec": {
        "replicas": 1,
        "template": {
            "containers": [
                {"resources": {"requests": {"cpu": "100m"}, "limits": {"cpu": "500m"}}}
            ]
        },
    },
    "status": {"conditions": [{"type": "MainWorkloadReady", "status": "True"}]},
}

_REST_MODELS_RESPONSE = {
    "data": [
        {
            "id": "facebook/opt-125m",
            "owned_by": "llm/facebook-opt-125m-simulated",
            "url": "https://maas.example.com/llm/facebook-opt-125m-simulated",
            "ready": True,
            "subscriptions": [
                {"name": "simulator-free", "displayName": "Simulator Free Tier", "description": "..."}
            ],
        }
    ]
}


def _mock_customobjects_api(list_result: dict | Exception, get_result: dict | None = None):
    api = MagicMock()
    if isinstance(list_result, Exception):
        api.list_cluster_custom_object.side_effect = list_result
    else:
        api.list_cluster_custom_object.return_value = list_result
    api.get_namespaced_custom_object.return_value = get_result
    return api


def test_list_subscriptions_shapes_fields() -> None:
    api = _mock_customobjects_api({"items": [_SUBSCRIPTION_FREE]})
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_subscriptions()

    assert result.available
    sub = result.items[0]
    assert sub["name"] == "simulator-free"
    assert sub["display_name"] == "Simulator Free Tier"
    assert sub["priority"] == 10
    assert sub["ready"] is True
    assert sub["priority_conflict"] is False
    assert sub["owner"]["groups"] == ["system:authenticated"]
    assert sub["model_refs"][0]["token_rate_limits"] == [{"limit": 100, "window": "1m"}]
    assert "simulator-free" in sub["raw_yaml"]
    assert "priority: 10" in sub["raw_yaml"]


_SUBSCRIPTION_MULTI_MODEL_MULTI_LIMIT = {
    "metadata": {"name": "multi-tier", "namespace": "models-as-a-service", "annotations": {}},
    "spec": {
        "priority": 10,
        "owner": {"groups": [{"name": "system:authenticated"}], "users": []},
        "modelRefs": [
            {
                "name": "facebook-opt-125m-simulated",
                "namespace": "llm",
                # A single model ref can carry more than one rate-limit tier
                # (e.g. a burst window and a sustained one) — the CRD field
                # is tokenRateLimits[], not a single limit.
                "tokenRateLimits": [
                    {"limit": 100, "window": "1m"},
                    {"limit": 2000, "window": "1h"},
                ],
            },
            {
                "name": "granite-8b",
                "namespace": "llm",
                "tokenRateLimits": [{"limit": 20, "window": "1m"}],
            },
        ],
    },
    "status": {"phase": "Active", "conditions": [{"type": "Ready", "status": "True"}]},
}


def test_list_subscriptions_supports_multiple_model_refs_with_multiple_rate_limits_each() -> None:
    """Rate limits belong to each model ref (spec.modelRefs[].tokenRateLimits[]),
    not the subscription as a whole — different models under the same
    subscription can carry entirely different limits, and a single model ref
    can carry more than one tier. Nothing here should collapse either list."""
    api = _mock_customobjects_api({"items": [_SUBSCRIPTION_MULTI_MODEL_MULTI_LIMIT]})
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_subscriptions()

    model_refs = result.items[0]["model_refs"]
    assert [m["name"] for m in model_refs] == ["facebook-opt-125m-simulated", "granite-8b"]
    assert model_refs[0]["token_rate_limits"] == [
        {"limit": 100, "window": "1m"},
        {"limit": 2000, "window": "1h"},
    ]
    assert model_refs[1]["token_rate_limits"] == [{"limit": 20, "window": "1m"}]


def test_list_subscriptions_flags_priority_conflict() -> None:
    api = _mock_customobjects_api({"items": [_SUBSCRIPTION_CONFLICTING]})
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_subscriptions()

    assert result.items[0]["priority_conflict"] is True


def test_list_subscriptions_forbidden_is_unavailable_not_raised() -> None:
    api = _mock_customobjects_api(ApiException(status=403))
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_subscriptions()

    assert result.available is False
    assert result.reason == "forbidden"
    assert result.items == []


def test_list_subscriptions_crd_not_installed() -> None:
    api = _mock_customobjects_api(ApiException(status=404))
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_subscriptions()

    assert result.available is False
    assert result.reason == "not_installed"


def test_list_subscriptions_unreachable_cluster_is_unavailable() -> None:
    """A connection-level failure (DNS/network/TLS) never even reaches the API
    server, so it isn't an ApiException — must still degrade gracefully rather
    than surface a raw urllib3 exception string to the UI."""
    api = _mock_customobjects_api(ConnectionError("Name or service not known"))
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_subscriptions()

    assert result.available is False
    assert result.reason == "unreachable"


_AUTH_POLICY_COVERING_MODEL = {
    "metadata": {"name": "simulator-access", "namespace": "models-as-a-service", "annotations": {}},
    "spec": {
        "modelRefs": [{"name": "facebook-opt-125m-simulated", "namespace": "llm"}],
        "subjects": {"groups": [{"name": "system:authenticated"}]},
    },
    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
}


def test_list_subscriptions_enriches_models_with_ref_and_auth_policy() -> None:
    """A subscription's model entries should surface the referenced
    MaaSModelRef's display name/readiness and whether a matching
    MaaSAuthPolicy exists for the subscription's own owners — this is what
    lets an admin spot a missing/broken auth policy or a dangling model
    reference directly on the subscription, without cross-referencing tabs."""
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maassubscriptions":
            return {"items": [_SUBSCRIPTION_FREE]}
        if plural == "maasmodelrefs":
            return {"items": [_MODELREF]}
        if plural == "maasauthpolicies":
            return {"items": [_AUTH_POLICY_COVERING_MODEL]}
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_subscriptions()

    model = result.items[0]["model_refs"][0]
    assert model["display_name"] == "Facebook OPT 125M (Simulated)"
    assert model["model_exists"] is True
    assert model["model_ready"] is True
    assert model["has_auth_policy"] is True


def test_list_subscriptions_flags_missing_auth_policy_and_dangling_model_ref() -> None:
    """No MaaSAuthPolicy covers this subscription's owner/model, and the
    referenced MaaSModelRef doesn't even exist — both must read as concrete
    False (not None), since both CRDs *were* readable."""
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maassubscriptions":
            return {"items": [_SUBSCRIPTION_FREE]}
        if plural in ("maasmodelrefs", "maasauthpolicies"):
            return {"items": []}
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_subscriptions()

    model = result.items[0]["model_refs"][0]
    assert model["display_name"] == "facebook-opt-125m-simulated"
    assert model["model_exists"] is False
    assert model["model_ready"] is None
    assert model["has_auth_policy"] is False


def test_list_subscriptions_model_ref_and_auth_policy_unknown_when_unreadable() -> None:
    """'Unknown' (RBAC/read failure on the cross-referenced CRD) must never
    collapse into 'confirmed missing' — None, not False."""
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maassubscriptions":
            return {"items": [_SUBSCRIPTION_FREE]}
        if plural in ("maasmodelrefs", "maasauthpolicies"):
            raise ApiException(status=403)
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_subscriptions()

    model = result.items[0]["model_refs"][0]
    assert model["display_name"] == "facebook-opt-125m-simulated"
    assert model["model_exists"] is None
    assert model["model_ready"] is None
    assert model["has_auth_policy"] is None


def test_list_models_merges_modelref_llmisvc_and_rest(monkeypatch) -> None:
    monkeypatch.setenv("MAAS_API_URL", "https://maas.example.com")
    monkeypatch.setattr(maas_client, "sa_token", lambda: "test-token")

    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maasmodelrefs":
            return {"items": [_MODELREF]}
        if plural == "externalmodels":
            return {"items": []}
        if plural == "maasauthpolicies":
            return {"items": [_AUTH_POLICY_COVERING_MODEL]}
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    api.get_namespaced_custom_object.return_value = _LLM_ISVC

    core_api = MagicMock()
    core_api.read_namespace.return_value = MagicMock(
        metadata=MagicMock(labels={"maas.opendatahub.io/gateway-access": "true"})
    )

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client.k8s, "CoreV1Api", return_value=core_api), \
         patch.object(maas_client, "_kube"), \
         patch("httpx.get") as mock_get:
        mock_get.return_value = MagicMock(
            json=lambda: _REST_MODELS_RESPONSE,
            raise_for_status=lambda: None,
        )
        result = maas_client.list_models()

    assert result.available
    model = result.items[0]
    assert model["name"] == "facebook-opt-125m-simulated"
    assert model["kind"] == "LLMInferenceService"
    assert model["hosting"] == "internal"
    assert model["ready"] is True
    assert model["subscriptions"] == [
        {"name": "simulator-free", "display_name": "Simulator Free Tier", "description": "..."}
    ]
    assert model["serving"]["replicas"] == 1
    # raw_yaml is just the MaaSModelRef's own YAML; the backing
    # LLMInferenceService gets its own serving_raw_yaml, not merged in.
    assert "facebook-opt-125m-simulated" in model["raw_yaml"]
    assert "llmInferenceService" not in model["raw_yaml"]
    assert model["serving_raw_yaml"] is not None
    assert "replicas: 1" in model["serving_raw_yaml"]
    assert model["has_auth_policy"] is True
    assert model["auth_policies"] == [
        {
            "name": "simulator-access",
            "namespace": "models-as-a-service",
            "display_name": "simulator-access",
            "ready": True,
            "raw_yaml": maas_client._to_yaml(_AUTH_POLICY_COVERING_MODEL),
        }
    ]
    assert model["gateway_access_label"] is True
    core_api.read_namespace.assert_called_once_with("llm")
    # Same format as Limitador's limitador_namespace label — the run launcher
    # autofills scenario config from it.
    assert model["http_route"] == "llm/facebook-opt-125m-simulated-kserve-route"


def test_list_models_auth_policies_empty_when_none_match() -> None:
    """A model with has_auth_policy False should carry an empty list, not
    None — None is reserved for 'couldn't read auth policies at all'."""
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maasmodelrefs":
            return {"items": [_MODELREF]}
        if plural in ("externalmodels", "maasauthpolicies"):
            return {"items": []}
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    api.get_namespaced_custom_object.return_value = _LLM_ISVC

    core_api = MagicMock()
    core_api.read_namespace.return_value = MagicMock(metadata=MagicMock(labels={}))

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client.k8s, "CoreV1Api", return_value=core_api), \
         patch.object(maas_client, "_kube"), \
         patch("httpx.get", side_effect=Exception("no MAAS_API_URL in test env")):
        result = maas_client.list_models()

    model = result.items[0]
    assert model["has_auth_policy"] is False
    assert model["auth_policies"] == []


def test_list_models_flags_missing_auth_policy_and_gateway_label() -> None:
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maasmodelrefs":
            return {"items": [_MODELREF]}
        if plural in ("externalmodels", "maasauthpolicies"):
            return {"items": []}
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    api.get_namespaced_custom_object.return_value = _LLM_ISVC

    core_api = MagicMock()
    core_api.read_namespace.return_value = MagicMock(metadata=MagicMock(labels={}))

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client.k8s, "CoreV1Api", return_value=core_api), \
         patch.object(maas_client, "_kube"), \
         patch("httpx.get", side_effect=Exception("no MAAS_API_URL in test env")):
        result = maas_client.list_models()

    model = result.items[0]
    assert model["has_auth_policy"] is False
    assert model["gateway_access_label"] is False


def test_list_models_auth_policy_and_gateway_label_unknown_when_unreadable() -> None:
    """None (not False) when auth policies/namespaces can't be read at all —
    'unknown' must never collapse into 'confirmed missing'."""
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maasmodelrefs":
            return {"items": [_MODELREF]}
        if plural == "externalmodels":
            return {"items": []}
        if plural == "maasauthpolicies":
            raise ApiException(status=403)
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    api.get_namespaced_custom_object.return_value = _LLM_ISVC

    core_api = MagicMock()
    core_api.read_namespace.side_effect = ApiException(status=403)

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client.k8s, "CoreV1Api", return_value=core_api), \
         patch.object(maas_client, "_kube"), \
         patch("httpx.get", side_effect=Exception("no MAAS_API_URL in test env")):
        result = maas_client.list_models()

    model = result.items[0]
    assert model["has_auth_policy"] is None
    assert model["gateway_access_label"] is None


_EXTERNAL_MODEL_35 = {
    "metadata": {
        "name": "gpt-4o-mini",
        "namespace": "external-models",
        "annotations": {"openshift.io/display-name": "GPT-4o (external)"},
    },
    "spec": {
        "modelName": "gpt-4o-mini",
        "externalProviderRefs": [
            {
                "ref": {"name": "openai"},
                "targetModel": "gpt-4o-mini",
                "apiFormat": "openai-chat",
                "path": "/v1/chat/completions",
            }
        ],
    },
    "status": {"phase": "Active", "conditions": [{"type": "Ready", "status": "True"}]},
}

_EXTERNAL_PROVIDER_OPENAI = {
    "metadata": {"name": "openai", "namespace": "external-models"},
    "spec": {
        "provider": "openai",
        "endpoint": "api.openai.com",
        "auth": {"type": "apikey", "secretRef": {"name": "openai-api-key"}},
    },
}


def test_list_models_external_model_35_resolves_provider() -> None:
    """RHOAI 3.5+: ExternalModel/ExternalProvider live under
    inference.opendatahub.io/v1alpha1, not maas.opendatahub.io — see the
    domain reference's Catalog item B."""
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "externalmodels":
            assert group == "inference.opendatahub.io"
            return {"items": [_EXTERNAL_MODEL_35]}
        if plural == "externalproviders":
            assert group == "inference.opendatahub.io"
            return {"items": [_EXTERNAL_PROVIDER_OPENAI]}
        return {"items": []}

    api.list_cluster_custom_object.side_effect = list_side_effect

    core_api = MagicMock()
    core_api.read_namespaced_secret.return_value = MagicMock(
        metadata=MagicMock(labels={"inference.llm-d.ai/ipp-managed": "true"})
    )

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client.k8s, "CoreV1Api", return_value=core_api), \
         patch.object(maas_client, "_kube"), \
         patch("httpx.get", side_effect=Exception("no MAAS_API_URL in test env")):
        result = maas_client.list_models()

    assert result.available
    model = result.items[0]
    assert model["kind"] == "ExternalModel"
    assert model["hosting"] == "external"
    assert model["ready"] is True
    assert model["backing_name"] == "gpt-4o-mini"
    assert model["endpoint"] == "api.openai.com"
    provider = model["external_providers"][0]
    assert provider["provider_name"] == "openai"
    assert provider["target_model"] == "gpt-4o-mini"
    assert provider["credential_secret_name"] == "openai-api-key"
    assert provider["credential_secret_label_ok"] is True
    core_api.read_namespaced_secret.assert_called_once_with("openai-api-key", "external-models")
    # raw_yaml is just the ExternalModel's own YAML; each provider carries
    # its own raw_yaml (below), not merged into the model's.
    assert "gpt-4o-mini" in model["raw_yaml"]
    assert "api.openai.com" not in model["raw_yaml"]
    assert model["serving_raw_yaml"] is None
    assert provider["raw_yaml"] is not None
    assert "api.openai.com" in provider["raw_yaml"]


def test_list_models_flags_credential_secret_missing_label() -> None:
    api = MagicMock()
    api.list_cluster_custom_object.side_effect = lambda group, version, plural: (
        {"items": [_EXTERNAL_MODEL_35]} if plural == "externalmodels"
        else {"items": [_EXTERNAL_PROVIDER_OPENAI]} if plural == "externalproviders"
        else {"items": []}
    )

    core_api = MagicMock()
    core_api.read_namespaced_secret.return_value = MagicMock(metadata=MagicMock(labels={}))

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client.k8s, "CoreV1Api", return_value=core_api), \
         patch.object(maas_client, "_kube"), \
         patch("httpx.get", side_effect=Exception("no MAAS_API_URL in test env")):
        result = maas_client.list_models()

    provider = result.items[0]["external_providers"][0]
    assert provider["credential_secret_label_ok"] is False


def test_list_models_credential_secret_unreadable_is_unknown() -> None:
    api = MagicMock()
    api.list_cluster_custom_object.side_effect = lambda group, version, plural: (
        {"items": [_EXTERNAL_MODEL_35]} if plural == "externalmodels"
        else {"items": [_EXTERNAL_PROVIDER_OPENAI]} if plural == "externalproviders"
        else {"items": []}
    )

    core_api = MagicMock()
    core_api.read_namespaced_secret.side_effect = ApiException(status=403)

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client.k8s, "CoreV1Api", return_value=core_api), \
         patch.object(maas_client, "_kube"), \
         patch("httpx.get", side_effect=Exception("no MAAS_API_URL in test env")):
        result = maas_client.list_models()

    provider = result.items[0]["external_providers"][0]
    assert provider["credential_secret_label_ok"] is None


def test_list_models_external_model_without_resolvable_provider() -> None:
    """The provider CRD read failed/is unavailable — the model still renders,
    just without endpoint/secret details resolved."""
    api = MagicMock()
    api.list_cluster_custom_object.side_effect = lambda group, version, plural: (
        {"items": [_EXTERNAL_MODEL_35]} if plural == "externalmodels" else {"items": []}
    )

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client.k8s, "CoreV1Api", return_value=MagicMock()), \
         patch.object(maas_client, "_kube"), \
         patch("httpx.get", side_effect=Exception("no MAAS_API_URL in test env")):
        result = maas_client.list_models()

    assert result.available
    model = result.items[0]
    provider = model["external_providers"][0]
    assert provider["provider_name"] == "openai"
    assert provider["endpoint"] is None
    assert provider["credential_secret_name"] is None
    assert provider["raw_yaml"] is None
    assert model["endpoint"] is None


def test_list_models_survives_rest_call_failure(monkeypatch) -> None:
    """The MaaS REST cross-reference is best-effort — if MAAS_API_URL/token
    aren't usable, models still render (just without the subscriptions[] chip)."""
    monkeypatch.delenv("MAAS_API_URL", raising=False)

    api = MagicMock()
    api.list_cluster_custom_object.side_effect = lambda group, version, plural: (
        {"items": [_MODELREF]} if plural == "maasmodelrefs" else {"items": []}
    )
    api.get_namespaced_custom_object.return_value = _LLM_ISVC

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client.k8s, "CoreV1Api", return_value=MagicMock()), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_models()

    assert result.available
    assert result.items[0]["subscriptions"] == []


def test_list_models_forbidden_is_unavailable() -> None:
    api = _mock_customobjects_api(ApiException(status=403))
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_models()

    assert result.available is False
    assert result.reason == "forbidden"


def _subscription(name: str, group: str, model_name: str, model_namespace: str = "llm") -> dict:
    return {
        "metadata": {"name": name, "namespace": "models-as-a-service", "annotations": {}},
        "spec": {
            "priority": 10,
            "owner": {"groups": [{"name": group}], "users": []},
            "modelRefs": [{"name": model_name, "namespace": model_namespace, "tokenRateLimits": []}],
        },
        "status": {"phase": "Active", "conditions": [{"type": "Ready", "status": "True"}]},
    }


def _auth_policy(name: str, group: str, model_name: str, model_namespace: str = "llm") -> dict:
    return {
        "metadata": {"name": name, "namespace": "models-as-a-service", "annotations": {}},
        "spec": {
            "subjects": {"groups": [{"name": group}], "users": []},
            "modelRefs": [{"name": model_name, "namespace": model_namespace}],
        },
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }


def _access_api(subscriptions: list[dict], auth_policies: list[dict], groups: list[dict] | Exception):
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maassubscriptions":
            return {"items": subscriptions}
        if plural == "maasauthpolicies":
            return {"items": auth_policies}
        if plural == "maasmodelrefs":
            # list_subscriptions()/list_auth_policies() both resolve their
            # model_refs[] against a live maasmodelrefs list — irrelevant to
            # what these list_access() tests are checking, so empty is fine.
            return {"items": []}
        if plural == "groups":
            if isinstance(groups, Exception):
                raise groups
            return {"items": groups}
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    return api


def test_list_auth_policies_shapes_fields() -> None:
    policy_cr = _auth_policy("ap-a", "team-a", "model-x")
    policy_cr["metadata"]["annotations"] = {"openshift.io/description": "Access for team-a"}
    policy_cr["status"]["phase"] = "Active"

    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maasauthpolicies":
            return {"items": [policy_cr]}
        if plural == "maasmodelrefs":
            return {"items": []}
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_auth_policies()

    assert result.available
    policy = result.items[0]
    assert policy["name"] == "ap-a"
    assert policy["description"] == "Access for team-a"
    assert policy["phase"] == "Active"
    assert policy["owner"]["groups"] == ["team-a"]
    assert policy["ready"] is True

    model_ref = policy["model_refs"][0]
    assert model_ref["name"] == "model-x"
    assert model_ref["namespace"] == "llm"
    # maasmodelrefs read succeeded but had nothing matching — a genuine
    # dangling reference, not "couldn't tell."
    assert model_ref["model_exists"] is False
    assert model_ref["model_ready"] is None
    assert model_ref["display_name"] == "model-x"


def test_list_auth_policies_resolves_existing_model_ref() -> None:
    policy_cr = _auth_policy("ap-a", "team-a", "facebook-opt-125m-simulated", "llm")

    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maasauthpolicies":
            return {"items": [policy_cr]}
        if plural == "maasmodelrefs":
            return {"items": [_MODELREF]}
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_auth_policies()

    model_ref = result.items[0]["model_refs"][0]
    assert model_ref["model_exists"] is True
    assert model_ref["model_ready"] is True
    assert model_ref["display_name"] == "Facebook OPT 125M (Simulated)"


def test_list_auth_policies_model_ref_unknown_when_modelrefs_unreadable() -> None:
    policy_cr = _auth_policy("ap-a", "team-a", "model-x")

    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maasauthpolicies":
            return {"items": [policy_cr]}
        if plural == "maasmodelrefs":
            raise ApiException(status=403)
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_auth_policies()

    model_ref = result.items[0]["model_refs"][0]
    assert model_ref["model_exists"] is None
    assert model_ref["model_ready"] is None


def test_list_auth_policies_forbidden_is_unavailable() -> None:
    api = _mock_customobjects_api(ApiException(status=403))
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_auth_policies()

    assert result.available is False
    assert result.reason == "forbidden"


def test_list_access_flags_quota_without_access() -> None:
    """A group has subscription quota for a model but no matching auth policy
    — real misconfiguration: they have quota but can't reach the gateway."""
    subs = [_subscription("sub-a", "team-a", "model-x")]
    api = _access_api(subs, auth_policies=[], groups=[])

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_access()

    assert result.available
    row = next(r for r in result.items if r["name"] == "team-a")
    assert row["quota_without_access"] == ["llm/model-x"]
    assert row["access_without_quota"] == []


def test_list_access_flags_access_without_quota() -> None:
    """A group has an auth policy but no subscription — they can reach the
    gateway but are rate-limited to zero by the cluster's default-deny policy."""
    auth = [_auth_policy("ap-b", "team-b", "model-y")]
    api = _access_api(subscriptions=[], auth_policies=auth, groups=[])

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_access()

    row = next(r for r in result.items if r["name"] == "team-b")
    assert row["access_without_quota"] == ["llm/model-y"]
    assert row["quota_without_access"] == []


def test_list_access_no_mismatch_when_matched() -> None:
    subs = [_subscription("sub-a", "team-a", "model-x")]
    auth = [_auth_policy("ap-a", "team-a", "model-x")]
    api = _access_api(subs, auth, groups=[{"metadata": {"name": "team-a"}, "users": ["alice"]}])

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_access()

    row = next(r for r in result.items if r["name"] == "team-a")
    assert row["quota_without_access"] == []
    assert row["access_without_quota"] == []
    assert row["users"] == ["alice"]
    assert "team-a" in row["raw_yaml"]
    assert "sub-a" in row["subscriptions"][0]["raw_yaml"]
    assert "ap-a" in row["auth_policies"][0]["raw_yaml"]


def test_list_access_groups_unavailable_still_shows_names() -> None:
    """Groups is a softer dependency than subscriptions/auth policies — its
    absence shouldn't blank out the whole tab, just leave users unresolved."""
    subs = [_subscription("sub-a", "team-a", "model-x")]
    auth = [_auth_policy("ap-a", "team-a", "model-x")]
    api = _access_api(subs, auth, groups=ApiException(status=403))

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_access()

    assert result.available
    row = next(r for r in result.items if r["name"] == "team-a")
    assert row["users"] is None
    assert row["raw_yaml"] is None


def test_list_access_unavailable_when_auth_policies_unreadable() -> None:
    """Subscriptions and auth policies are compared against each other, so
    either being unreadable makes the whole comparison meaningless."""
    subs = [_subscription("sub-a", "team-a", "model-x")]
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "maassubscriptions":
            return {"items": subs}
        if plural == "maasauthpolicies":
            raise ApiException(status=403)
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_access()

    assert result.available is False
    assert result.reason == "forbidden"


_TOKEN_RATE_LIMIT_POLICY = {
    "metadata": {"name": "maas-trlp-facebook-opt-125m-simulated", "namespace": "llm"},
    "spec": {
        "targetRef": {"kind": "HTTPRoute", "name": "facebook-opt-125m-simulated-kserve-route"},
        "limits": {
            "models-as-a-service-simulator-free-facebook-opt-125m-simulated-tokens": {
                "rates": [{"limit": 100, "window": "1m"}]
            }
        },
    },
    "status": {
        "conditions": [
            {"type": "Accepted", "status": "True"},
            {"type": "Enforced", "status": "True"},
        ]
    },
}


def test_list_rate_limit_policies_shapes_fields() -> None:
    api = _mock_customobjects_api({"items": [_TOKEN_RATE_LIMIT_POLICY]})
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_rate_limit_policies()

    assert result.available
    policy = result.items[0]
    assert policy["target_kind"] == "HTTPRoute"
    assert policy["target_name"] == "facebook-opt-125m-simulated-kserve-route"
    assert policy["limit_names"] == ["models-as-a-service-simulator-free-facebook-opt-125m-simulated-tokens"]
    assert policy["accepted"] is True
    assert policy["enforced"] is True


def test_list_rate_limit_policies_forbidden_is_unavailable() -> None:
    api = _mock_customobjects_api(ApiException(status=403))
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_rate_limit_policies()

    assert result.available is False
    assert result.reason == "forbidden"


_LIMITADOR_CR = {
    "metadata": {"name": "limitador", "namespace": "kuadrant-system"},
    "spec": {"limits": [{"name": "a"}, {"name": "b"}]},
    "status": {
        "conditions": [{"type": "Ready", "status": "True"}],
        "service": {"host": "limitador-limitador.kuadrant-system.svc.cluster.local"},
    },
}


def test_list_limitador_shapes_fields() -> None:
    api = _mock_customobjects_api({"items": [_LIMITADOR_CR]})
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_limitador()

    assert result.available
    limitador = result.items[0]
    assert limitador["limit_count"] == 2
    assert limitador["ready"] is True
    assert limitador["service_host"] == "limitador-limitador.kuadrant-system.svc.cluster.local"


def test_list_limitador_forbidden_is_unavailable() -> None:
    api = _mock_customobjects_api(ApiException(status=403))
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_limitador()

    assert result.available is False
    assert result.reason == "forbidden"


_GATEWAY_CR = {
    "metadata": {"name": "maas-default-gateway", "namespace": "openshift-ingress"},
    "spec": {"gatewayClassName": "openshift-default"},
    "status": {
        "addresses": [{"value": "a5022c964ce964d4f85bc428e4f4a58f-514146043.us-east-2.elb.amazonaws.com"}],
        "conditions": [{"type": "Programmed", "status": "True"}],
    },
}


def test_list_gateways_shapes_fields() -> None:
    api = _mock_customobjects_api({"items": [_GATEWAY_CR]})
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_gateways()

    assert result.available
    gw = result.items[0]
    assert gw["gateway_class"] == "openshift-default"
    assert gw["address"] == "a5022c964ce964d4f85bc428e4f4a58f-514146043.us-east-2.elb.amazonaws.com"
    assert gw["programmed"] is True


def test_list_gateways_forbidden_is_unavailable() -> None:
    api = _mock_customobjects_api(ApiException(status=403))
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_gateways()

    assert result.available is False
    assert result.reason == "forbidden"


_HTTP_ROUTE_CR = {
    "metadata": {
        "name": "facebook-opt-125m-simulated-kserve-route",
        "namespace": "llm",
        "ownerReferences": [
            {"apiVersion": "serving.kserve.io/v1alpha2", "kind": "LLMInferenceService", "name": "facebook-opt-125m-simulated"}
        ],
    },
    "spec": {
        "parentRefs": [
            {"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": "maas-default-gateway", "namespace": "openshift-ingress"}
        ]
    },
}


def test_list_http_routes_traces_owning_model() -> None:
    api = _mock_customobjects_api({"items": [_HTTP_ROUTE_CR]})
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_http_routes()

    assert result.available
    route = result.items[0]
    assert route["parent_gateway"] == "maas-default-gateway"
    assert route["parent_gateway_namespace"] == "openshift-ingress"
    assert route["owning_model"] == "facebook-opt-125m-simulated"


def test_list_http_routes_no_owner_reference() -> None:
    route_without_owner = {"metadata": {"name": "some-route", "namespace": "llm"}, "spec": {"parentRefs": []}}
    api = _mock_customobjects_api({"items": [route_without_owner]})
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_http_routes()

    assert result.items[0]["owning_model"] is None
    assert result.items[0]["parent_gateway"] is None


def test_list_http_routes_forbidden_is_unavailable() -> None:
    api = _mock_customobjects_api(ApiException(status=403))
    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        result = maas_client.list_http_routes()

    assert result.available is False
    assert result.reason == "forbidden"


_TENANT_CR = {
    "metadata": {"name": "default-tenant", "namespace": "models-as-a-service"},
    "spec": {
        "gatewayRef": {"name": "maas-default-gateway", "namespace": "openshift-ingress"},
        "apiKeys": {"maxExpirationDays": 90},
        "telemetry": {"enabled": True},
    },
    "status": {"phase": "Active"},
}

_DSC_35_STYLE = {
    "metadata": {"name": "default-dsc"},
    "spec": {"components": {"aigateway": {"modelsAsAService": {"managementState": "Managed"}}}},
}

_DSC_34_STYLE = {
    "metadata": {"name": "default-dsc"},
    "spec": {"components": {"kserve": {"modelsAsService": {"managementState": "Managed"}}}},
}

_ODH_DASHBOARD_CR = {
    "metadata": {"name": "odh-dashboard-config"},
    "spec": {
        "dashboardConfig": {
            "modelAsService": True,
            "externalModels": True,
            "genAiStudio": False,
            "observabilityDashboard": True,
        }
    },
}


def test_list_platform_shapes_all_sections() -> None:
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "tenants":
            return {"items": [_TENANT_CR]}
        if plural == "datascienceclusters":
            return {"items": [_DSC_35_STYLE]}
        if plural == "odhdashboardconfigs":
            return {"items": [_ODH_DASHBOARD_CR]}
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        platform = maas_client.list_platform()

    assert platform["tenants"]["available"] is True
    tenant = platform["tenants"]["items"][0]
    assert tenant["max_api_key_expiration_days"] == 90
    assert tenant["telemetry_enabled"] is True

    assert platform["data_science_cluster"]["available"] is True
    dsc = platform["data_science_cluster"]["item"]
    assert dsc["maas_management_state"] == "Managed"
    assert dsc["maas_field_path"] == "aigateway.modelsAsAService"

    assert platform["odh_dashboard_config"]["available"] is True
    odh = platform["odh_dashboard_config"]["item"]
    assert odh["model_as_service"] is True
    assert odh["gen_ai_studio"] is False


def test_list_platform_reads_deprecated_34_style_field_path() -> None:
    api = MagicMock()
    api.list_cluster_custom_object.side_effect = lambda group, version, plural: (
        {"items": [_DSC_34_STYLE]} if plural == "datascienceclusters" else {"items": []}
    )

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        platform = maas_client.list_platform()

    dsc = platform["data_science_cluster"]["item"]
    assert dsc["maas_management_state"] == "Managed"
    assert dsc["maas_field_path"] == "kserve.modelsAsService (deprecated)"


def test_list_platform_sections_degrade_independently() -> None:
    api = MagicMock()

    def list_side_effect(group, version, plural):
        if plural == "tenants":
            raise ApiException(status=403)
        if plural == "datascienceclusters":
            return {"items": [_DSC_35_STYLE]}
        if plural == "odhdashboardconfigs":
            raise ApiException(status=404)
        raise AssertionError(f"unexpected plural {plural}")

    api.list_cluster_custom_object.side_effect = list_side_effect

    with patch.object(maas_client.k8s, "CustomObjectsApi", return_value=api), \
         patch.object(maas_client, "_kube"):
        platform = maas_client.list_platform()

    assert platform["tenants"]["available"] is False
    assert platform["tenants"]["reason"] == "forbidden"
    assert platform["data_science_cluster"]["available"] is True
    assert platform["odh_dashboard_config"]["available"] is False
    assert platform["odh_dashboard_config"]["reason"] == "not_installed"
