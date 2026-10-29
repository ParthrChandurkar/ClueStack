# ClueStack Architecture

ClueStack has two explicit modes.

## CLI boundary

```text
infrawatch CLI
  -> orchestration layer
  -> existing scripts / Terraform / Kustomize / Docker / Kubernetes
```

The Python package currently provides:

- `infrawatch --help`
- `infrawatch --version`
- `infrawatch doctor` for local prerequisite and readiness diagnostics
- `infrawatch status` for current Kubernetes runtime state
- `infrawatch start` for local Minikube/Terraform/Kustomize startup
- `infrawatch stop` for safe app runtime cleanup while preserving Terraform foundation state

The CLI does not duplicate Terraform or Kubernetes manifests. `infrawatch start` orchestrates the existing tools and resource definitions:

```text
infrawatch start
  -> prerequisite checks
  -> minikube start/use-context
  -> terraform init/fmt/validate/apply
  -> kubectl apply -k k8s
  -> rollout waits
  -> optional existing observability installer on Windows when requested
  -> status validation
```

`infrawatch stop` respects the same ownership boundary:

```text
infrawatch stop
  -> verify runtime resources
  -> verify kubectl can reach the current cluster
  -> verify Terraform state still tracks the 8 foundation resources
  -> kubectl delete -k k8s
  -> preserve generated app runtime Secret
  -> wait for app pods to disappear
  -> verify Terraform state and plan again
  -> verify Minikube is still running
```

The default stop path removes app-owned Kustomize runtime resources and leaves the Minikube cluster, Terraform state, namespace, RBAC, ResourceQuota, ServiceAccount, backend runtime ConfigMap, PostgreSQL PVC data, and generated `infrawatch-secrets` Secret intact. The Secret is preserved because the retained database volume expects the same password on restart. Observability workloads are preserved by default. `infrawatch stop --remove-observability` explicitly removes the ClueStack observability manifests and Helm releases while still preserving the Terraform foundation.

## Packaged runtime resources

The Python package includes the runtime resources required by the CLI:

```text
infrawatch.resources
  -> terraform/
  -> k8s/
  -> monitoring/
  -> logging/
  -> scripts/start-observability.ps1
```

When running from a source checkout, the CLI uses the checkout files directly. When running from an installed wheel outside the repository, it materializes the packaged resources into a versioned user cache directory and runs Terraform/Kustomize from there. Terraform state and downloaded providers are not packaged.

## Demo Mode

```text
React frontend
  -> browser sandbox store
  -> manifest simulation
  -> mock metrics/logs
```

Demo Mode is safe for local UI exploration and portfolio screenshots. It does not run Kubernetes workloads.

## Public GitHub repository analysis

Existing `github_repository` projects can request read-only analysis through:

```text
React repository panel
  -> POST /projects/{project_id}/repository-analysis
  -> official api.github.com endpoints
  -> bounded metadata, branch, tree, and recognized-file retrieval
  -> evidence-backed stack findings
  -> sanitized repository_analysis report
```

The analyzer recognizes Docker, Compose, Node, Python, Kubernetes, and Terraform configuration. Findings identify
their evidence paths and distinguish detected, missing, unknown, and unsupported evidence. Tree entries, files,
individual file size, total retrieved content, per-request time, and total analysis duration are bounded.

This capability supports public repositories only. It does not clone repositories, follow repository-provided
instructions, execute code, build images, deploy the analyzed application, or access private repository credentials.
Browser Demo Mode does not fabricate repository-analysis results.

## Local repository analysis

Existing `local` projects can analyze a folder selected explicitly in the browser:

```text
Browser directory picker
  -> recognized non-sensitive UTF-8 configuration files only
  -> POST /projects/{project_id}/local-repository-analysis
  -> bounded local source adapter
  -> shared repository analyzer
  -> sanitized repository_analysis report
```

The browser preserves relative paths but never sends an absolute local path. It excludes generated and dependency
directories, credential-like paths, private keys, certificates, unsupported files, and files outside the configured
count and byte limits. The backend repeats path, sensitivity, type, count, byte, and duration validation rather than
trusting browser filtering. Raw uploaded file contents are used only for the current request and are not stored in
project records or reports.

Local and public GitHub analysis produce the same normalized finding and readiness models while retaining a
`local_upload` or `github_public` source identifier. Local analysis does not discover the workstation filesystem,
follow symlinks, unpack archives, run code, install dependencies, build images, or deploy the selected application.
Browser Demo Mode does not simulate local analysis.

Repository readiness is a separate deterministic stage:

```text
persisted repository_analysis report
  -> POST /projects/{project_id}/repository-readiness
  -> containerization, Kubernetes, and infrastructure rule sets
  -> sanitized repository_readiness report
```

The readiness endpoint uses a selected analysis report or the latest compatible successful/partial report. It does
not contact GitHub or reread local files. Missing, incompatible, cross-project, or older-than-configured analysis evidence is rejected
explicitly and recorded as a failed readiness attempt.

Status calculation is conservative:

- incomplete or truncated source evidence makes each dimension and the overall result `unknown`;
- missing blocking prerequisites makes a dimension `not_ready`;
- incomplete required service or port evidence makes Kubernetes `partially_ready`;
- optional items such as health probes, resource policies, CI/CD, and observability are recommendations and do not by
  themselves make a complete core configuration not ready;
- overall status gives `unknown` precedence, followed by `not_ready`, `partially_ready`, and `ready`.

These statuses describe static configuration evidence only. ClueStack does not infer image availability, manifest
validity, build success, rollout success, runtime health, or external-service availability from readiness assessment.

## Container build orchestration boundary

### Immutable source snapshots

The source-snapshot service captures either an exact public GitHub commit through `api.github.com` or the exact bounded
UTF-8 files submitted by the browser for a local project. It rejects unsafe paths, traversal, duplicate normalized
paths, unsupported binary content, credentials, generated/dependency trees, and configured file/size/time limits.
GitHub branches and tags are resolved to a 40-character commit SHA before any tree or blob is fetched; truncated trees
are rejected because they cannot produce an exact snapshot.

Each published snapshot contains source identity, a canonical manifest ordered by relative path, per-file SHA-256
hashes, and one overall SHA-256 hash over format version, source identity, and every included path, size, and file hash.
Timestamps, lifecycle state, and storage paths are deliberately excluded from the content hash. Publication writes to
a generated staging directory and atomically renames it only after all content and the manifest are complete. Metadata
is stored atomically in JSON and survives restart.

The filesystem implementation requests read-only permissions after publication and verifies every file before build
preparation. This detects ordinary tampering but is not an absolute immutability guarantee against an administrator or
storage system that can change permissions or bytes. Raw source content is never returned by metadata APIs or copied
into reports. Available snapshots expire after the configured retention interval; referenced build snapshots cannot be
deleted. The supported lifecycle is `creating`, `available`, `failed`, `expired`, and `deleted`.

Container build requests use persisted analysis and readiness reports through project-scoped eligibility and build
APIs. Validation binds the request to explicit analysis/readiness report IDs, an immutable source snapshot identifier,
a detected Dockerfile, repository-root context, a validated image name/tag, and explicit approval. Build attempts are
recorded as `container_build` reports and never imply deployment, runtime health, or rollout success.

The backend has separate coordinator, worker-provider, isolation-runtime, lifecycle, result-validation, bounded-log,
and cleanup contracts. Worker requests contain UUID identities, verified snapshot metadata, relative build paths,
validated image coordinates, conservative resource limits, a disabled-network policy, timeout, and correlation ID.
They contain no host source path. The snapshot service independently re-verifies project association, expiry, format,
and content integrity before a capable runtime may start; incomplete local submissions are not complete build contexts.

The resource policy defaults to one concurrent job, four queued jobs, 0.5 CPU, 512 MiB memory, 1 GiB temporary storage,
64 processes, a ten-minute timeout, and 2,000 bytes of persisted logs. Worker output is bounded and sanitized for
common credentials and host paths. Success additionally requires matching request identities, valid timestamps, zero
exit status, an immutable `sha256` image digest, and successful cleanup. Lifecycle states are `queued`, `starting`,
`running`, `succeeded`, `failed`, `cancelled`, `timed_out`, `rejected`, and `cleanup_failed`. Cancellation is unavailable
unless a runtime can terminate and clean the complete isolated environment.

The production runtime remains deliberately unavailable. The inspected development environment has no verified VM or
sandbox runtime, and a normal Docker container is not treated as a complete security boundary. ClueStack does not use
the backend Docker client, Docker socket, privileged containers, arbitrary host mounts, host networking, or repository
commands. Eligibility therefore reports a worker blocker and approved attempts finish as `rejected`; no Docker or shell
command is invoked. Fake-runtime tests validate contracts and lifecycle behavior only, not real isolation.

## Real Local Kubernetes Mode

```text
Minikube
  -> frontend Deployment
  -> Nginx /api proxy
  -> backend Service
  -> FastAPI
  -> kubectl
  -> user workload Deployment + Service
  -> Prometheus/Loki/Grafana/Alertmanager
```

Real mode is enabled by Kubernetes config:

- `INFRAWATCH_EXECUTE_KUBECTL=true`
- `INFRAWATCH_ALLOW_MOCK_OBSERVABILITY=false`
- `DATABASE_URL=postgresql://...`

## Deployment flow

```text
GitHub
  -> GitHub Actions validate job
  -> Docker build
  -> DockerHub image tagged with GITHUB_SHA
  -> Kubernetes apply
  -> kubectl set image
  -> kubectl rollout status
```

The workflow uses immutable SHA tags for the rollout. The `latest` tag is also pushed for convenience, but Kubernetes deployment uses `${GITHUB_SHA}`.

## Application flow

```text
Browser
  -> React dashboard
  -> Nginx /api proxy
  -> infrawatch-backend Service
  -> FastAPI
  -> kubectl
  -> Kubernetes Deployment/Service
```

The frontend container uses Nginx to serve static assets and proxy `/api/*` to `http://infrawatch-backend:8000/`. This avoids adding Ingress just to connect the dashboard to the API in Minikube.

## Deployment request internals

1. React sends service name, image, replicas, and port.
2. FastAPI validates with Pydantic.
3. FastAPI generates a Kubernetes `List` containing:
   - Deployment
   - Service
4. In Demo Mode, the manifest is returned and state is recorded without kubectl.
5. In real mode:
   - `kubectl apply -f -`
   - `kubectl rollout status deployment/<name>`
   - `kubectl get deployment/<name> -o json`
   - Ready and available replicas are verified.
6. The deployment is marked `Running` only after rollout verification.
7. Rollout failures are stored as `Failed` with failure details.

## Rollback flow

```text
React rollback action
  -> POST /deployment/{name}/rollback
  -> kubectl rollout undo deployment/{name}
  -> kubectl rollout status
  -> ready/available replica verification
  -> audit event
```

Rollback is intentionally simple. It uses standard Kubernetes Deployment revision history instead of a custom release database.

## Metrics flow

```text
Application/Kubernetes
  -> Prometheus scrape
  -> Redis short TTL cache
  -> FastAPI /metrics/{service}
  -> React charts
```

Prometheus sources:

- kubelet/cAdvisor metrics for CPU and memory.
- kube-state-metrics for pod/deployment/HPA state.
- FastAPI `/metrics` or `/internal/metrics` for app request metrics.
- annotated managed pods with `prometheus.io/scrape=true`.

Backend queries:

- CPU: `container_cpu_usage_seconds_total`
- Memory: `container_memory_working_set_bytes`
- Request rate: `http_requests_total`
- Error rate: `http_requests_total{status=~"5.."}`

Redis sits between FastAPI and Prometheus as a cache-aside optimization:

```text
FastAPI
  -> Redis cache lookup
    -> hit: return cached Prometheus response
    -> miss: query Prometheus, store real response briefly, return response
```

The cache key includes the service, namespace, Prometheus URL, PromQL queries, range, and step. The TTL is intentionally short so repeated dashboard refreshes avoid duplicate Prometheus API work without hiding fresh telemetry for long.

Redis is optional. If Redis is unavailable, FastAPI continues directly to Prometheus. Mock fallback responses are not cached as Prometheus truth.

In real mode, mock fallback is disabled. If Prometheus is unavailable, the API returns an error instead of fake data.

## Logs flow

```text
Application stdout/stderr
  -> Kubernetes container logs
  -> Grafana Alloy
  -> Loki
  -> FastAPI /logs/{service}
  -> React near-real-time log polling
```

The dashboard polls every 5 seconds. This is near-real-time log streaming via polling, not WebSockets.

Grafana Alloy labels pod logs with:

- `namespace`
- `pod`
- `container`
- `app`

FastAPI queries Loki with:

```logql
{app="<service>"}
```

## Alerts flow

```text
Kubernetes metrics
  -> Prometheus rules
  -> Alertmanager
  -> FastAPI /alerts
  -> React alerts panel
```

Important alerts:

- `PodCrashLooping`
- `HighCpuUsage`
- `HighMemoryUsage`
- `BackendUnavailable`
- `ServiceDown`
- `DeploymentUnavailable`

The React dashboard does not hardcode alert cards. FastAPI queries the configured
Alertmanager API, normalizes active alerts, and returns an explicit availability
error if Alertmanager is unreachable in real local mode.

## Autoscaling flow

```text
Traffic/load
  -> backend CPU usage
  -> metrics-server
  -> HPA controller
  -> desired replicas
  -> Deployment
  -> new Pods
```

ClueStack includes an HPA for `deployment/infrawatch-backend`:

- min replicas: 1
- max replicas: 3
- CPU target: 70%

This requires Minikube metrics-server:

```bash
minikube addons enable metrics-server
kubectl get hpa -n infrawatch -w
```

## Persistence flow

```text
FastAPI
  -> DATABASE_URL configured?
    -> yes: PostgreSQL deployments/audit tables
    -> no: JSON file fallback
```

Real local Kubernetes mode should use PostgreSQL. Demo Mode and tests can safely use JSON fallback.
