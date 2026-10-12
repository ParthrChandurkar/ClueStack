# InfraWatch

InfraWatch is a local-first Kubernetes deployment and observability tool for lightweight local application testing.

It helps developers, students, and early SRE learners run a small local stack, inspect workload health, and understand metrics, logs, and alerts without manually wiring every component from scratch. InfraWatch integrates existing tools such as Docker, Minikube, Kubernetes, Terraform, Kustomize, Prometheus, Grafana, Loki, Grafana Alloy, Alertmanager, PostgreSQL, and Redis. It does not replace those tools.

InfraWatch v0.3.0 is a developer preview. It is meant for local development, testing, and learning, not for production cluster management or a hosted multi-tenant SaaS platform.

## What InfraWatch does

- Provides an installable Python CLI:
  - `infrawatch doctor`
  - `infrawatch start`
  - `infrawatch status`
  - `infrawatch stop`
- Runs a React dashboard for deployment, workload, metrics, logs, and alerts views.
- Runs a FastAPI backend for local deployment operations and observability APIs.
- Supports Docker Compose and Minikube-oriented local workflows.
- Uses Terraform for Kubernetes foundation resources such as namespace, RBAC, ResourceQuota, ServiceAccount, and backend runtime ConfigMap.
- Uses Kustomize for application runtime resources such as backend, frontend, PostgreSQL, Redis, HPA, Services, and ConfigMaps.
- Uses PostgreSQL for persistence.
- Uses Redis as a short-TTL cache for repeated Prometheus dashboard metric queries.
- Reads service metrics from Prometheus.
- Reads Kubernetes logs from Loki through Grafana Alloy labels.
- Reads alert state from Alertmanager.
- Includes packaged runtime resources so the CLI can run from an installed wheel outside the repository checkout.
- Provides demo/mock behavior for first-time local exploration when live observability sources are not available.

## Who it is for

InfraWatch is currently aimed at:

- developers testing lightweight containerized services locally;
- students learning Kubernetes and observability workflows;
- early SRE learners practicing rollout, health, metrics, logs, and alert scenarios;
- contributors who want a small local-first project for experimenting with Kubernetes automation and dashboard behavior.

It is not currently designed for platform teams operating production multi-tenant infrastructure.

## Architecture at a glance

```text
Developer
  -> infrawatch CLI
  -> Docker / Minikube / Terraform / Kustomize / kubectl
  -> InfraWatch Kubernetes runtime

Browser
  -> React dashboard
  -> Nginx /api proxy
  -> FastAPI backend
  -> Kubernetes API, PostgreSQL, Redis
  -> Prometheus, Loki, Alertmanager
```

Main components:

| Component | Role |
| --- | --- |
| Python CLI | Runs diagnostics, starts/stops the local Kubernetes stack, and reports runtime status. |
| React dashboard | Shows deployment state, workload health, metrics, logs, alerts, and local activity. |
| FastAPI backend | Handles deployment operations and observability API responses. |
| Terraform | Owns the local Kubernetes foundation resources. |
| Kustomize | Owns the app runtime resources. |
| PostgreSQL | Stores deployment and audit state. |
| Redis | Caches repeated Prometheus metric responses briefly. |
| Prometheus | Source for live metrics. |
| Loki + Grafana Alloy | Source path for live Kubernetes logs. |
| Alertmanager | Source for live alert visibility. |
| Grafana | Provides observability dashboards for the local stack. |

Prometheus, Grafana, Alertmanager, and kube-state-metrics are installed through Kubernetes manifests. Loki and Grafana Alloy are installed through Helm.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for deeper component boundaries and request flows.

## Demo/mock data versus live telemetry

InfraWatch intentionally separates demo behavior from live observability.

In Docker Compose and first-time local exploration, mock fallback can be enabled so the dashboard remains usable even when a full Kubernetes observability stack is not running. Mock responses should be treated as sample data.

In real local Kubernetes mode, live data comes from:

- Kubernetes API for workload status;
- Prometheus for CPU, memory, request-rate, and error-rate metrics;
- Loki through Grafana Alloy for logs;
- Alertmanager for alerts;
- PostgreSQL for persisted deployment/audit state;
- Redis for short-lived caching of real Prometheus responses.

Redis does not replace Prometheus. It only avoids repeating identical Prometheus dashboard queries for a short TTL. If Redis is unavailable, the backend can query Prometheus directly.

## Prerequisites

Choose the setup path you want to use.

For Docker Compose:

- Docker Desktop or Docker Engine with the Docker Compose plugin

For local Kubernetes with Minikube:

- Python 3.11 or newer
- Docker Desktop or Docker Engine
- Minikube
- `kubectl`
- Terraform
- Helm, when installing Loki and Grafana Alloy observability components

InfraWatch is primarily validated around local Docker Desktop and Minikube-oriented workflows. Other Kubernetes distributions may work, but broad compatibility is not claimed.

## Quick start with Docker Compose

Use this path first if you want the fastest local dashboard/API experience.

PowerShell:

```powershell
Copy-Item .env.example .env
.\scripts\start-local.ps1 -Detached
```

Manual Docker Compose path:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

Default local URLs:

| Tool | URL |
| --- | --- |
| InfraWatch dashboard | http://localhost:3000 |
| FastAPI docs | http://localhost:8000/docs |
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9090 |
| Loki | http://localhost:3100 |
| Grafana Alloy | http://localhost:12345 |
| Alertmanager | http://localhost:9093 |

Grafana credentials come from your local `.env` file. Start from `.env.example` and change local passwords before sharing logs or screenshots.

Stop the Compose stack:

```powershell
.\scripts\stop-local.ps1
```

Or manually:

```powershell
docker compose down
```

## Python CLI usage

From a repository checkout:

```powershell
python -m pip install -e .
infrawatch --help
infrawatch --version
infrawatch doctor
```

For a clean local wheel install before PyPI publication:

```powershell
python -m pip install build
python -m build
python -m venv C:\Temp\infrawatch-wheel-test
C:\Temp\infrawatch-wheel-test\Scripts\python.exe -m pip install (Get-ChildItem .\dist\infrawatch-*-py3-none-any.whl | Select-Object -Last 1).FullName
Set-Location C:\Temp
infrawatch --version
infrawatch doctor
```

InfraWatch is not published to PyPI yet. The current package version is `0.3.0`, but locally built artifacts should be regenerated only when you intentionally build them.

The installed wheel includes runtime Terraform, Kubernetes, monitoring, logging, and helper resources used by the CLI. When the CLI runs outside a repository checkout, it materializes those packaged resources into a local user cache.

## Local Kubernetes workflow

The preferred Minikube-oriented path is:

```powershell
infrawatch doctor
infrawatch start
infrawatch status
```

`infrawatch start` reconciles the local workflow:

1. checks local prerequisites;
2. starts or reuses Minikube;
3. initializes and validates Terraform;
4. reconciles Terraform-managed foundation resources;
5. applies Kustomize-managed app resources;
6. waits for PostgreSQL, Redis, backend, and frontend readiness;
7. reports runtime status.

To install the observability stack through the CLI when supported:

```powershell
infrawatch start --install-observability
```

To require live observability responses instead of mock fallback:

```powershell
infrawatch start --strict-observability
```

To stop the app runtime while preserving the local foundation and data:

```powershell
infrawatch stop
```

The default stop path preserves:

- the Minikube cluster;
- Terraform state;
- Terraform-owned foundation resources;
- PostgreSQL PVC data;
- the generated `infrawatch-secrets` Secret, because retained PostgreSQL data needs the same password;
- observability workloads by default.

To intentionally remove observability workloads too:

```powershell
infrawatch stop --remove-observability
```

## Repository helper scripts

The repository also includes script-backed workflows.

Docker Compose:

```powershell
.\scripts\start-local.ps1 -Detached
.\scripts\doctor.ps1
.\scripts\stop-local.ps1
```

Windows Minikube path:

```powershell
.\scripts\start-k8s.ps1
.\scripts\start-observability.ps1
.\scripts\stop-observability.ps1
.\scripts\stop-k8s.ps1
```

Linux/macOS Bash path:

```bash
bash scripts/setup.sh
bash scripts/health-check.sh
```

Use the scripts when you want more explicit control or when debugging a local environment.

## Manual Kubernetes path

The manual path is useful for contributors who want to inspect each layer.

```powershell
minikube start
minikube addons enable metrics-server

terraform -chdir=terraform init
terraform -chdir=terraform validate
terraform -chdir=terraform apply -var="kube_context=minikube"

kubectl create secret generic infrawatch-secrets `
  --namespace infrawatch `
  --from-literal=POSTGRES_PASSWORD=use-a-strong-local-password `
  --from-literal=DATABASE_URL=postgresql://infrawatch:use-a-strong-local-password@infrawatch-postgres:5432/infrawatch

kubectl apply -k k8s
kubectl rollout status statefulset/infrawatch-postgres --namespace infrawatch --timeout=180s
kubectl rollout status deployment/infrawatch-backend --namespace infrawatch --timeout=180s
kubectl rollout status deployment/infrawatch-frontend --namespace infrawatch --timeout=180s
minikube service infrawatch-frontend --namespace infrawatch
```

Do not commit generated Secret manifests or real credentials.

## Validation and development checks

These commands are supported by the repository configuration.

Backend:

```powershell
Set-Location backend
python -m ruff check app tests
python -m pytest
Set-Location ..
```

CLI:

```powershell
python -m ruff check infrawatch tests
python -m pytest tests
```

Frontend:

```powershell
Set-Location frontend
npm ci
npm run lint
npm run build
Set-Location ..
```

Infrastructure and packaging:

```powershell
docker compose config --quiet
kubectl kustomize k8s
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform fmt -check
terraform -chdir=terraform validate
python -m build
```

These checks validate configuration and build behavior. They do not prove that your local Kubernetes cluster is healthy unless you also run the local runtime workflow against a reachable cluster.

## CI/CD notes

The GitHub Actions workflow validates backend, CLI, frontend, Docker Compose, Kustomize, Terraform, Docker image builds, and the Python package build.

The workflow can publish Docker images when DockerHub credentials are configured. Remote Kubernetes deployment requires `KUBE_CONFIG_B64`; if that secret is missing, deployment is skipped. A GitHub-hosted runner cannot directly reach a laptop-only Minikube cluster. The repository includes a separate self-hosted runner path for local Minikube use.

## Limitations

InfraWatch is a developer preview. Current limitations include:

- no user authentication;
- no multi-tenant authorization model;
- no hosted SaaS control plane;
- no automatic onboarding or building of arbitrary Git repositories;
- no production Ingress or TLS setup by default;
- no production cluster management claim;
- no universal Kubernetes distribution or operating system support claim;
- primary validation around local Docker Desktop and Minikube-oriented workflows.

Future work may explore a limited user-provided container image or Kubernetes manifest workflow, but arbitrary repository builds and automatic GitHub onboarding are not implemented today.

## Project boundaries and roadmap

- [SCOPE.md](SCOPE.md) explains the project purpose, current capabilities, limitations, and non-goals.
- [ROADMAP.md](ROADMAP.md) describes the directional roadmap for future local-first improvements.
- [docs/README.md](docs/README.md) is the documentation index.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains component boundaries and request flows.
- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) provides a more detailed onboarding path.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) covers common local setup issues.

## Contributing and security

Contributions are welcome when they stay within the local-first scope and include clear validation results.

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- Report vulnerabilities using [SECURITY.md](SECURITY.md).
- Avoid posting credentials, tokens, kubeconfig contents, private logs, or sensitive application data in public issues or pull requests.

## License

InfraWatch is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for the full license text.
