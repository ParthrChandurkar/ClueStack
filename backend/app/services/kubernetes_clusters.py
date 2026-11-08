"""Read-only Kubernetes context discovery and cluster overview integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from app.config import Settings
from app.schemas import (
    KubernetesClusterOverview,
    KubernetesContextList,
    KubernetesNamespacePodSummary,
    KubernetesNodeCondition,
    KubernetesNodeOverview,
    KubernetesPodSummary,
    KubernetesResourceUsage,
)


class KubernetesIntegrationError(RuntimeError):
    """Safe, classified Kubernetes integration failure for API consumers."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.status_code = status_code


@dataclass(frozen=True)
class KubernetesClients:
    """Structured clients bound to one kubeconfig context without global switching."""

    core: Any
    version: Any
    custom_objects: Any
    api_server: str | None


class KubernetesClientProvider(Protocol):
    def contexts(self) -> tuple[list[str], str | None]: ...

    def clients(self, context: str) -> KubernetesClients: ...


class OfficialKubernetesClientProvider:
    """Load official Kubernetes Python clients from the user's local kubeconfig."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def contexts(self) -> tuple[list[str], str | None]:
        client_module, config_module = self._modules()
        del client_module
        try:
            contexts, current = config_module.list_kube_config_contexts(config_file=self._settings.kubeconfig_path)
        except Exception as exc:
            raise KubernetesIntegrationError(
                "kubeconfig_unavailable",
                "Kubernetes contexts could not be loaded from the local kubeconfig.",
                503,
            ) from exc
        names = [
            name
            for entry in contexts or []
            if (name := self._safe_context_name(entry.get("name"))) is not None
        ][:100]
        current_name = self._safe_context_name((current or {}).get("name"))
        return names, current_name

    def clients(self, context: str) -> KubernetesClients:
        client_module, config_module = self._modules()
        contexts, _ = self.contexts()
        if context not in contexts:
            raise KubernetesIntegrationError("invalid_context", "The selected Kubernetes context was not found.", 404)
        try:
            api_client = config_module.new_client_from_config(
                config_file=self._settings.kubeconfig_path,
                context=context,
                persist_config=False,
            )
            api_server = self._safe_api_server(getattr(api_client.configuration, "host", None))
            core = client_module.CoreV1Api(api_client)
            version = client_module.VersionApi(api_client)
            custom_objects = client_module.CustomObjectsApi(api_client)
        except KubernetesIntegrationError:
            raise
        except Exception as exc:
            raise KubernetesIntegrationError(
                "kubeconfig_unavailable",
                "The selected Kubernetes context could not be loaded.",
                503,
            ) from exc
        return KubernetesClients(
            core=core,
            version=version,
            custom_objects=custom_objects,
            api_server=api_server,
        )

    @staticmethod
    def _modules() -> tuple[Any, Any]:
        try:
            from kubernetes import client, config
        except ImportError as exc:
            raise KubernetesIntegrationError(
                "client_unavailable",
                "The Kubernetes Python client is not installed in the backend environment.",
                503,
            ) from exc
        return client, config

    @staticmethod
    def _safe_context_name(value: Any) -> str | None:
        if not isinstance(value, str) or not value or len(value) > 253:
            return None
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return None
        return value

    @staticmethod
    def _safe_api_server(value: Any) -> str | None:
        if value is None:
            return None
        try:
            if not isinstance(value, str):
                raise ValueError("invalid API server value")
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("invalid API server URL")
            port = parsed.port
            hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            netloc = f"{hostname}:{port}" if port is not None else hostname
            return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))
        except (UnicodeError, ValueError) as exc:
            raise KubernetesIntegrationError(
                "kubeconfig_unavailable",
                "The selected Kubernetes context could not be loaded.",
                503,
            ) from exc


class KubernetesClusterService:
    """Produce bounded read-only cluster views for explicitly selected contexts."""

    def __init__(self, settings: Settings, provider: KubernetesClientProvider | None = None) -> None:
        self._settings = settings
        self._provider = provider or OfficialKubernetesClientProvider(settings)

    def list_contexts(self) -> KubernetesContextList:
        if not self._settings.execute_kubectl:
            return KubernetesContextList(
                enabled=False,
                message="Kubernetes access is disabled by backend configuration.",
            )
        contexts, current = self._provider.contexts()
        return KubernetesContextList(
            enabled=True,
            contexts=contexts,
            current_context=current if current in contexts else None,
            message=None if contexts else "No Kubernetes contexts were found in the local kubeconfig.",
        )

    def overview(self, context: str) -> KubernetesClusterOverview:
        if not self._settings.execute_kubectl:
            raise KubernetesIntegrationError(
                "kubernetes_disabled",
                "Kubernetes access is disabled by backend configuration.",
                503,
            )
        clients = self._provider.clients(context)
        timeout = self._settings.kubernetes_request_timeout_seconds
        try:
            version = clients.version.get_code(_request_timeout=timeout)
            node_items = clients.core.list_node(_request_timeout=timeout).items
            pod_items = clients.core.list_pod_for_all_namespaces(_request_timeout=timeout).items
        except Exception as exc:
            raise self._classify_error(exc) from exc

        nodes = [self._node_overview(node) for node in node_items]
        pods = self._pod_summary(pod_items)
        return KubernetesClusterOverview(
            context=context,
            server_version=self._server_version(version),
            api_server=clients.api_server,
            cluster_identity=self._cluster_identity(clients.core, timeout),
            total_nodes=len(nodes),
            ready_nodes=sum(1 for node in nodes if node.ready),
            not_ready_nodes=sum(1 for node in nodes if not node.ready),
            nodes=nodes,
            pods=pods,
            resource_usage=self._resource_usage(clients.custom_objects, timeout),
            refreshed_at=datetime.now(UTC),
        )

    def _cluster_identity(self, core: Any, timeout: float) -> str | None:
        try:
            namespace = core.read_namespace("kube-system", _request_timeout=timeout)
            uid = getattr(getattr(namespace, "metadata", None), "uid", None)
            return str(uid)[:128] if uid else None
        except Exception:
            return None

    @staticmethod
    def _server_version(version: Any) -> str | None:
        value = getattr(version, "git_version", None)
        if value:
            return str(value)[:80]
        major = getattr(version, "major", None)
        minor = getattr(version, "minor", None)
        return f"v{major}.{minor}" if major is not None and minor is not None else None

    @staticmethod
    def _node_overview(node: Any) -> KubernetesNodeOverview:
        metadata = getattr(node, "metadata", None)
        status = getattr(node, "status", None)
        labels = getattr(metadata, "labels", None) or {}
        roles = sorted(
            {
                key.removeprefix("node-role.kubernetes.io/")
                for key in labels
                if key.startswith("node-role.kubernetes.io/") and key.removeprefix("node-role.kubernetes.io/")
            }
        )
        legacy_role = labels.get("kubernetes.io/role")
        if legacy_role and legacy_role not in roles:
            roles.append(str(legacy_role))
        conditions = []
        for condition in getattr(status, "conditions", None) or []:
            conditions.append(
                KubernetesNodeCondition(
                    type=str(getattr(condition, "type", "Unknown"))[:80],
                    status=str(getattr(condition, "status", "Unknown"))[:20],
                    reason=str(getattr(condition, "reason", ""))[:120] or None,
                    message=str(getattr(condition, "message", ""))[:500] or None,
                    last_transition_time=getattr(condition, "last_transition_time", None),
                )
            )
        ready = any(condition.type == "Ready" and condition.status == "True" for condition in conditions)
        node_info = getattr(status, "node_info", None)
        return KubernetesNodeOverview(
            name=str(getattr(metadata, "name", "unknown"))[:253],
            roles=roles,
            kubernetes_version=str(getattr(node_info, "kubelet_version", ""))[:80] or None,
            ready=ready,
            conditions=conditions,
        )

    @staticmethod
    def _pod_summary(pods: list[Any]) -> KubernetesPodSummary:
        totals = {phase: 0 for phase in ("running", "pending", "succeeded", "failed", "unknown")}
        namespaces: dict[str, dict[str, int]] = {}
        for pod in pods:
            namespace = str(getattr(getattr(pod, "metadata", None), "namespace", None) or "default")[:253]
            raw_phase = str(getattr(getattr(pod, "status", None), "phase", None) or "Unknown")
            phase = raw_phase.lower() if raw_phase.lower() in totals else "unknown"
            totals[phase] += 1
            bucket = namespaces.setdefault(
                namespace,
                {"total": 0, **{name: 0 for name in totals}},
            )
            bucket["total"] += 1
            bucket[phase] += 1
        distribution = [
            KubernetesNamespacePodSummary(namespace=namespace, **counts)
            for namespace, counts in sorted(namespaces.items())
        ]
        return KubernetesPodSummary(total=len(pods), namespaces=distribution, **totals)

    def _resource_usage(self, custom_objects: Any, timeout: float) -> KubernetesResourceUsage:
        try:
            payload = custom_objects.list_cluster_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                plural="nodes",
                _request_timeout=timeout,
            )
            items = payload.get("items", [])
            measurements = [
                item["usage"]
                for item in items
                if isinstance(item, dict)
                and isinstance(item.get("usage"), dict)
                and item["usage"].get("cpu") is not None
                and item["usage"].get("memory") is not None
            ]
            if not measurements:
                raise ValueError("Kubernetes Metrics API returned no node usage samples")
            cpu = sum((self._cpu_cores(usage["cpu"]) for usage in measurements), Decimal(0))
            memory = sum((self._memory_bytes(usage["memory"]) for usage in measurements), Decimal(0))
            return KubernetesResourceUsage(
                available=True,
                cpu_cores=float(cpu),
                memory_bytes=int(memory),
                nodes_reporting=len(measurements),
            )
        except Exception:
            return KubernetesResourceUsage(
                available=False,
                message="Kubernetes Metrics API data is unavailable or access is not permitted.",
            )

    @staticmethod
    def _cpu_cores(value: Any) -> Decimal:
        return KubernetesClusterService._quantity(
            value,
            {"n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal("1e-3")},
        )

    @staticmethod
    def _memory_bytes(value: Any) -> Decimal:
        return KubernetesClusterService._quantity(
            value,
            {
                "Ki": Decimal(1024),
                "Mi": Decimal(1024**2),
                "Gi": Decimal(1024**3),
                "Ti": Decimal(1024**4),
                "K": Decimal(1000),
                "M": Decimal(1000**2),
                "G": Decimal(1000**3),
            },
        )

    @staticmethod
    def _quantity(value: Any, suffixes: dict[str, Decimal]) -> Decimal:
        text = str(value or "0")
        for suffix in sorted(suffixes, key=len, reverse=True):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                multiplier = suffixes[suffix]
                break
        else:
            multiplier = Decimal(1)
        try:
            return Decimal(text) * multiplier
        except InvalidOperation as exc:
            raise ValueError("invalid Kubernetes resource quantity") from exc

    @staticmethod
    def _classify_error(exc: Exception) -> KubernetesIntegrationError:
        status = getattr(exc, "status", None)
        if status == 401:
            return KubernetesIntegrationError("authentication_failed", "Kubernetes authentication failed.", 401)
        if status == 403:
            return KubernetesIntegrationError(
                "authorization_failed",
                "Kubernetes access was denied. Verify the configured read-only RBAC permissions.",
                403,
            )
        if status in {408, 504} or KubernetesClusterService._is_timeout_error(exc):
            return KubernetesIntegrationError("cluster_timeout", "The Kubernetes API request timed out.", 504)
        return KubernetesIntegrationError("cluster_unavailable", "The selected Kubernetes cluster is unavailable.", 503)

    @staticmethod
    def _is_timeout_error(exc: BaseException) -> bool:
        """Recognize built-in and urllib3 timeout chains without exposing their details."""

        pending: list[BaseException] = [exc]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            if isinstance(current, TimeoutError):
                return True
            exception_type = type(current)
            if exception_type.__module__.startswith("urllib3.") and exception_type.__name__ in {
                "ConnectTimeoutError",
                "ReadTimeoutError",
                "TimeoutError",
                "TimeoutStateError",
            }:
                return True
            for related in (
                getattr(current, "reason", None),
                current.__cause__,
                current.__context__,
            ):
                if isinstance(related, BaseException):
                    pending.append(related)
        return False
