"""Deterministic tests for the read-only Kubernetes Cluster Overview."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.kubernetes_clusters import (
    KubernetesClients,
    KubernetesClusterService,
    KubernetesIntegrationError,
    OfficialKubernetesClientProvider,
)


def obj(**values):
    return SimpleNamespace(**values)


class FakeApiError(Exception):
    def __init__(self, status: int, message: str = "synthetic failure") -> None:
        super().__init__(message)
        self.status = status


class FakeCore:
    def __init__(self, *, nodes=None, pods=None, error: Exception | None = None) -> None:
        self.nodes = nodes if nodes is not None else sample_nodes()
        self.pods = pods if pods is not None else sample_pods()
        self.error = error

    def list_node(self, *, _request_timeout):
        if self.error:
            raise self.error
        return obj(items=self.nodes)

    def list_pod_for_all_namespaces(self, *, _request_timeout):
        if self.error:
            raise self.error
        return obj(items=self.pods)

    def read_namespace(self, name, *, _request_timeout):
        return obj(metadata=obj(uid="cluster-uid-123"))


class FakeVersion:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def get_code(self, *, _request_timeout):
        if self.error:
            raise self.error
        return obj(git_version="v1.31.4", major="1", minor="31")


class FakeMetrics:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload or {
            "items": [
                {"usage": {"cpu": "250m", "memory": "128Mi"}},
                {"usage": {"cpu": "500m", "memory": "1Gi"}},
            ]
        }
        self.error = error

    def list_cluster_custom_object(self, **kwargs):
        if self.error:
            raise self.error
        assert kwargs["group"] == "metrics.k8s.io"
        assert kwargs["plural"] == "nodes"
        return self.payload


class FakeProvider:
    def __init__(self, clients=None, *, contexts=None, current="minikube", error=None) -> None:
        self._clients = clients or KubernetesClients(
            core=FakeCore(),
            version=FakeVersion(),
            custom_objects=FakeMetrics(),
            api_server="https://127.0.0.1:8443",
        )
        self._contexts = contexts or ["minikube", "kind-lab"]
        self._current = current
        self._error = error

    def contexts(self):
        if self._error:
            raise self._error
        return self._contexts, self._current

    def clients(self, context):
        if context not in self._contexts:
            raise KubernetesIntegrationError("invalid_context", "The selected Kubernetes context was not found.", 404)
        if self._error:
            raise self._error
        return self._clients


def official_provider_modules(*, host="https://127.0.0.1:6443", load_error=None):
    api_client = obj(configuration=obj(host=host))
    config_calls = []
    constructed = []

    def list_kube_config_contexts(*, config_file):
        config_calls.append(("contexts", {"config_file": config_file}))
        return ([{"name": "minikube"}, {"name": "kind-lab"}], {"name": "minikube"})

    def new_client_from_config(**kwargs):
        config_calls.append(("client", kwargs))
        if load_error:
            raise load_error
        return api_client

    def build_client(name):
        def factory(bound_api_client):
            result = obj(kind=name, api_client=bound_api_client)
            constructed.append(result)
            return result

        return factory

    config_module = obj(
        list_kube_config_contexts=list_kube_config_contexts,
        new_client_from_config=new_client_from_config,
    )
    client_module = obj(
        CoreV1Api=build_client("core"),
        VersionApi=build_client("version"),
        CustomObjectsApi=build_client("custom_objects"),
    )
    return client_module, config_module, api_client, config_calls, constructed


def sample_nodes():
    timestamp = datetime(2026, 11, 6, tzinfo=UTC)
    return [
        obj(
            metadata=obj(name="control-plane", labels={"node-role.kubernetes.io/control-plane": ""}),
            status=obj(
                node_info=obj(kubelet_version="v1.31.4"),
                conditions=[
                    obj(
                        type="Ready",
                        status="True",
                        reason="KubeletReady",
                        message="ok",
                        last_transition_time=timestamp,
                    )
                ],
            ),
        ),
        obj(
            metadata=obj(name="worker-1", labels={"kubernetes.io/role": "worker"}),
            status=obj(
                node_info=obj(kubelet_version="v1.31.4"),
                conditions=[
                    obj(
                        type="Ready",
                        status="False",
                        reason="KubeletNotReady",
                        message="offline",
                        last_transition_time=timestamp,
                    )
                ],
            ),
        ),
    ]


def sample_pods():
    return [
        obj(metadata=obj(namespace="default"), status=obj(phase="Running")),
        obj(metadata=obj(namespace="default"), status=obj(phase="Pending")),
        obj(metadata=obj(namespace="monitoring"), status=obj(phase="Succeeded")),
        obj(metadata=obj(namespace="monitoring"), status=obj(phase="Failed")),
        obj(metadata=obj(namespace="monitoring"), status=obj(phase=None)),
    ]


def enabled_settings():
    return Settings(environment="test", execute_kubectl=True, kubernetes_request_timeout_seconds=3)


def test_official_provider_uses_explicit_context_custom_path_and_non_persistent_config(monkeypatch) -> None:
    modules = official_provider_modules()
    client_module, config_module, api_client, config_calls, constructed = modules
    monkeypatch.setattr(
        OfficialKubernetesClientProvider,
        "_modules",
        staticmethod(lambda: (client_module, config_module)),
    )
    kubeconfig_path = "C:/synthetic/config"

    clients = OfficialKubernetesClientProvider(
        Settings(environment="test", kubeconfig_path=kubeconfig_path)
    ).clients("kind-lab")

    assert config_calls == [
        ("contexts", {"config_file": kubeconfig_path}),
        ("client", {"config_file": kubeconfig_path, "context": "kind-lab", "persist_config": False}),
    ]
    assert [client.kind for client in constructed] == ["core", "version", "custom_objects"]
    assert all(client.api_client is api_client for client in constructed)
    assert clients.core is constructed[0]
    assert clients.version is constructed[1]
    assert clients.custom_objects is constructed[2]


@pytest.mark.parametrize(
    ("raw_url", "expected"),
    [
        ("https://cluster.example.test:6443", "https://cluster.example.test:6443"),
        (
            "https://synthetic-user:synthetic-password@cluster.example.test:6443/proxy/?token=secret#details",
            "https://cluster.example.test:6443/proxy",
        ),
    ],
)
def test_official_provider_returns_only_sanitized_api_server(monkeypatch, raw_url, expected) -> None:
    modules = official_provider_modules(host=raw_url)
    client_module, config_module = modules[:2]
    monkeypatch.setattr(
        OfficialKubernetesClientProvider,
        "_modules",
        staticmethod(lambda: (client_module, config_module)),
    )

    clients = OfficialKubernetesClientProvider(Settings(environment="test")).clients("minikube")

    assert clients.api_server == expected
    assert "synthetic-user" not in clients.api_server
    assert "synthetic-password" not in clients.api_server
    assert "token" not in clients.api_server


@pytest.mark.parametrize(
    "host",
    [
        "https://synthetic-user:synthetic-password@cluster.example.test:not-a-port/path?token=secret",
        "cluster.example.test:6443",
    ],
)
def test_official_provider_rejects_malformed_api_server_with_sanitized_error(monkeypatch, host) -> None:
    modules = official_provider_modules(host=host)
    client_module, config_module = modules[:2]
    monkeypatch.setattr(
        OfficialKubernetesClientProvider,
        "_modules",
        staticmethod(lambda: (client_module, config_module)),
    )

    with pytest.raises(KubernetesIntegrationError) as raised:
        OfficialKubernetesClientProvider(Settings(environment="test")).clients("minikube")

    assert raised.value.code == "kubeconfig_unavailable"
    assert raised.value.status_code == 503
    assert raised.value.public_message == "The selected Kubernetes context could not be loaded."
    assert "synthetic" not in raised.value.public_message
    assert host not in raised.value.public_message


def test_official_provider_maps_malformed_kubeconfig_to_sanitized_error(monkeypatch) -> None:
    modules = official_provider_modules(load_error=ValueError("token=synthetic-secret"))
    client_module, config_module = modules[:2]
    monkeypatch.setattr(
        OfficialKubernetesClientProvider,
        "_modules",
        staticmethod(lambda: (client_module, config_module)),
    )

    with pytest.raises(KubernetesIntegrationError) as raised:
        OfficialKubernetesClientProvider(Settings(environment="test")).clients("minikube")

    assert raised.value.code == "kubeconfig_unavailable"
    assert raised.value.status_code == 503
    assert "synthetic-secret" not in raised.value.public_message


def test_successful_cluster_overview_aggregates_nodes_pods_and_metrics() -> None:
    overview = KubernetesClusterService(enabled_settings(), FakeProvider()).overview("minikube")

    assert overview.context == "minikube"
    assert overview.connection_status == "connected"
    assert overview.server_version == "v1.31.4"
    assert overview.api_server == "https://127.0.0.1:8443"
    assert overview.cluster_identity == "cluster-uid-123"
    assert (overview.total_nodes, overview.ready_nodes, overview.not_ready_nodes) == (2, 1, 1)
    assert overview.nodes[0].roles == ["control-plane"]
    assert overview.nodes[1].ready is False
    assert overview.pods.model_dump(exclude={"namespaces"}) == {
        "total": 5,
        "running": 1,
        "pending": 1,
        "succeeded": 1,
        "failed": 1,
        "unknown": 1,
    }
    assert overview.resource_usage.available is True
    assert overview.resource_usage.cpu_cores == 0.75
    assert overview.resource_usage.memory_bytes == 1_207_959_552
    assert overview.resource_usage.nodes_reporting == 2


def test_context_discovery_does_not_select_or_change_a_context() -> None:
    provider = FakeProvider()
    result = KubernetesClusterService(enabled_settings(), provider).list_contexts()
    assert result.enabled is True
    assert result.contexts == ["minikube", "kind-lab"]
    assert result.current_context == "minikube"


def test_invalid_context_is_reported_without_fallback() -> None:
    with pytest.raises(KubernetesIntegrationError) as raised:
        KubernetesClusterService(enabled_settings(), FakeProvider()).overview("missing")
    assert raised.value.code == "invalid_context"
    assert raised.value.status_code == 404


@pytest.mark.parametrize(
    ("error", "code", "status_code"),
    [
        (ConnectionError("cluster offline"), "cluster_unavailable", 503),
        (FakeApiError(401, "token=synthetic-secret"), "authentication_failed", 401),
        (FakeApiError(403, "Authorization: Bearer synthetic-secret"), "authorization_failed", 403),
        (TimeoutError("request timed out"), "cluster_timeout", 504),
    ],
)
def test_cluster_failures_are_classified_and_credentials_are_redacted(error, code, status_code) -> None:
    clients = KubernetesClients(
        core=FakeCore(error=error),
        version=FakeVersion(),
        custom_objects=FakeMetrics(),
        api_server=None,
    )
    with pytest.raises(KubernetesIntegrationError) as raised:
        KubernetesClusterService(enabled_settings(), FakeProvider(clients=clients)).overview("minikube")
    assert raised.value.code == code
    assert raised.value.status_code == status_code
    assert "synthetic-secret" not in raised.value.public_message


def test_metrics_unavailable_is_distinct_from_zero_usage() -> None:
    clients = KubernetesClients(
        core=FakeCore(),
        version=FakeVersion(),
        custom_objects=FakeMetrics(error=FakeApiError(404)),
        api_server=None,
    )
    overview = KubernetesClusterService(enabled_settings(), FakeProvider(clients=clients)).overview("minikube")
    usage = overview.resource_usage
    assert usage.available is False
    assert usage.cpu_cores is None
    assert usage.memory_bytes is None
    assert usage.nodes_reporting == 0


def test_empty_metrics_response_is_unavailable_but_observed_zero_usage_is_valid() -> None:
    empty_clients = KubernetesClients(
        core=FakeCore(),
        version=FakeVersion(),
        custom_objects=FakeMetrics(payload={"items": []}),
        api_server=None,
    )
    zero_clients = KubernetesClients(
        core=FakeCore(),
        version=FakeVersion(),
        custom_objects=FakeMetrics(payload={"items": [{"usage": {"cpu": "0", "memory": "0"}}]}),
        api_server=None,
    )

    empty_usage = KubernetesClusterService(
        enabled_settings(),
        FakeProvider(clients=empty_clients),
    ).overview("minikube").resource_usage
    zero_usage = KubernetesClusterService(
        enabled_settings(),
        FakeProvider(clients=zero_clients),
    ).overview("minikube").resource_usage

    assert empty_usage.available is False
    assert empty_usage.cpu_cores is None
    assert zero_usage.available is True
    assert zero_usage.cpu_cores == 0
    assert zero_usage.memory_bytes == 0
    assert zero_usage.nodes_reporting == 1


def test_kubernetes_execution_disabled_never_loads_kubeconfig_or_clients() -> None:
    provider = FakeProvider(error=AssertionError("provider must not be called"))
    service = KubernetesClusterService(Settings(environment="test", execute_kubectl=False), provider)

    contexts = service.list_contexts()
    assert contexts.enabled is False
    assert contexts.contexts == []
    with pytest.raises(KubernetesIntegrationError) as raised:
        service.overview("minikube")
    assert raised.value.code == "kubernetes_disabled"


def test_kubernetes_execution_disabled_does_not_initialize_official_provider(monkeypatch) -> None:
    def fail_if_loaded():
        raise AssertionError("official Kubernetes modules must not be loaded")

    monkeypatch.setattr(
        OfficialKubernetesClientProvider,
        "_modules",
        staticmethod(fail_if_loaded),
    )
    service = KubernetesClusterService(Settings(environment="test", execute_kubectl=False))

    assert service.list_contexts().enabled is False
    with pytest.raises(KubernetesIntegrationError) as raised:
        service.overview("minikube")
    assert raised.value.code == "kubernetes_disabled"


def test_urllib3_timeout_chain_is_classified_without_exposing_details() -> None:
    read_timeout_type = type("ReadTimeoutError", (Exception,), {"__module__": "urllib3.exceptions"})
    max_retry_type = type("MaxRetryError", (Exception,), {"__module__": "urllib3.exceptions"})
    timeout = read_timeout_type("Authorization: Bearer synthetic-secret")
    wrapped = max_retry_type("synthetic connection failure")
    wrapped.reason = timeout

    classified = KubernetesClusterService._classify_error(wrapped)

    assert classified.code == "cluster_timeout"
    assert classified.status_code == 504
    assert classified.public_message == "The Kubernetes API request timed out."
    assert "synthetic-secret" not in classified.public_message


def test_kubernetes_api_requires_explicit_context_and_returns_structured_errors(tmp_path) -> None:
    settings = Settings(
        environment="test",
        execute_kubectl=True,
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit.json"),
        projects_file=str(tmp_path / "projects.json"),
        reports_file=str(tmp_path / "reports.json"),
        source_snapshots_file=str(tmp_path / "snapshots.json"),
        source_snapshots_root=str(tmp_path / "snapshots"),
    )
    app = create_app(settings)
    app.state.kubernetes_cluster_service = KubernetesClusterService(settings, FakeProvider())
    client = TestClient(app)

    missing = client.get("/kubernetes/overview")
    invalid = client.get("/kubernetes/overview", params={"context": "missing"})
    contexts = client.get("/kubernetes/contexts")
    overview = client.get("/kubernetes/overview", params={"context": "minikube"})

    assert missing.status_code == 422
    assert invalid.status_code == 404
    assert invalid.json()["detail"] == {
        "code": "invalid_context",
        "message": "The selected Kubernetes context was not found.",
    }
    assert contexts.status_code == 200
    assert contexts.json()["contexts"] == ["minikube", "kind-lab"]
    assert overview.status_code == 200
    assert overview.json()["ready_nodes"] == 1
