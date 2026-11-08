# Kubernetes read-only access

ClueStack's Cluster Overview reads an explicitly selected kubeconfig context. It does not change the global `kubectl` context, submit arbitrary commands, or modify cluster resources.

The backend must be started with Kubernetes execution explicitly enabled before context discovery or overview requests are accepted. The kubeconfig remains on the backend host; only context names and bounded cluster summaries are returned to the browser.

For a local backend, set `INFRAWATCH_EXECUTE_KUBECTL=true`. The official Kubernetes client uses the normal local kubeconfig location unless `INFRAWATCH_KUBECONFIG_PATH` is set. `INFRAWATCH_KUBERNETES_REQUEST_TIMEOUT_SECONDS` bounds each API request. These settings do not change the current context stored in kubeconfig.

## Minimum permissions

Use a dedicated identity with only these read permissions:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: cluestack-cluster-overview
rules:
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["list"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["list"]
  - apiGroups: [""]
    resources: ["namespaces"]
    resourceNames: ["kube-system"]
    verbs: ["get"]
  - apiGroups: ["metrics.k8s.io"]
    resources: ["nodes"]
    verbs: ["list"]
  - nonResourceURLs: ["/version/"]
    verbs: ["get"]
```

These rules correspond to listing cluster nodes, listing pods across namespaces, reading only the `kube-system` namespace identity, reading the API server version, and optionally listing node metrics. Bind this role only to the user or service account used for the read-only overview. Do not grant `cluster-admin`. The `metrics.k8s.io` rule is optional: when the Metrics API is missing or denied, ClueStack reports utilization as unavailable rather than zero.

Context discovery reads local kubeconfig metadata. ClueStack never returns kubeconfig contents, tokens, client keys, certificate data, or authentication-provider configuration through its API.
