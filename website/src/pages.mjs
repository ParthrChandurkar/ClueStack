const code = (value) => `<div class="code-block"><pre><code>${escapeHtml(value)}</code></pre></div>`;

const rows = (items) =>
  `<div class="status-list">${items
    .map(([label, text]) => `<div class="status-row"><strong>${label}</strong><span>${text}</span></div>`)
    .join("")}</div>`;

const cards = (items, className = "info-grid") =>
  `<div class="${className}">${items
    .map(
      ([title, text]) =>
        `<article><h3>${title}</h3><p>${text}</p></article>`
    )
    .join("")}</div>`;

const docLinks = (items) =>
  `<div class="doc-list">${items
    .map(([href, title, text]) => `<a href="${href}"><strong>${title}</strong><span>${text}</span></a>`)
    .join("")}</div>`;

export const pages = [
  {
    href: "index.html",
    nav: "Overview",
    title: "ClueStack - Every signal tells a story",
    description:
      "Local Application Deployment & Observability Platform for supported local developer workflows.",
    bodyClass: "home",
    content: `
      <section class="hero">
        <div class="container hero-shell">
          <div class="hero-copy">
            <img class="hero-brand" src="assets/logo.svg" alt="ClueStack" width="760" height="170">
            <h1>Every signal tells a story.</h1>
            <p class="lead">ClueStack is a Local Application Deployment &amp; Observability Platform that combines deployments, metrics, logs, alerts, and reports to help developers understand system behavior.</p>
            <div class="actions">
              <a class="button primary" href="docs-quick-start.html">Get Started</a>
              <a class="button" href="docs.html">Documentation</a>
              <a class="button" href="https://github.com/ParthrChandurkar/InfraWatch">GitHub</a>
            </div>
            <p class="hero-note">v0.3.0 developer preview · Docker Compose and Minikube-oriented workflows · live telemetry when observability services are running</p>
          </div>
          <div class="hero-visual signal-board" aria-label="ClueStack workflow preview">
            <div class="signal-board-header"><span>Local workflow</span><strong>CLI + Web App + Observability</strong></div>
            <div class="signal-lanes">
              <article><span>01</span><strong>Check</strong><p>doctor validates local prerequisites and service reachability.</p></article>
              <article><span>02</span><strong>Deploy</strong><p>start reconciles the supported Docker or Minikube-oriented workflow.</p></article>
              <article><span>03</span><strong>Observe</strong><p>dashboard reads workload state, metrics, logs, and alerts from the backend.</p></article>
              <article><span>04</span><strong>Report</strong><p>local records preserve deployment and diagnostic history where supported.</p></article>
            </div>
            <div class="source-strip"><span>Kubernetes</span><span>Prometheus</span><span>Loki + Alloy</span><span>Alertmanager</span><span>PostgreSQL</span><span>Redis</span></div>
          </div>
        </div>
      </section>

      <section class="section intro-section">
        <div class="container split-feature">
          <div>
            <span class="eyebrow">Problem</span>
            <h2>Local deployment signals are scattered across commands, services, and dashboards.</h2>
            <p>ClueStack gives a single local-first surface for deployment status, workload readiness, metrics, logs, alerts, reports, and audit activity. It integrates with the tools that own those signals instead of pretending to replace them.</p>
          </div>
          ${cards([
            ["Web application", "A React dashboard for deployments, workloads, metrics, logs, alerts, reports, demo/live states, and local activity."],
            ["Python CLI", "The installable CLI exposes doctor, start, status, and stop for supported local workflows."],
            ["Backend APIs", "FastAPI provides deployment, project, report, audit, workload, metrics, logs, and alerts endpoints."],
            ["Local-first scope", "Docker Compose and Minikube-oriented paths are the current focus. Hosted SaaS and production multi-tenancy are not implemented."]
          ])}
        </div>
      </section>

      <section class="section">
        <div class="container">
          <div class="section-header wide">
            <span class="eyebrow">Explore the product</span>
            <h2>Understand ClueStack without leaving the website.</h2>
            <p>These pages document the actual implemented interfaces and clearly mark planned capabilities.</p>
          </div>
          ${docLinks([
            ["product.html", "Product overview", "Web app, CLI, Local Mode, and future Repository Mode."],
            ["web-app.html", "Web application", "Dashboard areas, backend sources, live/mock states, and persistence."],
            ["cli.html", "CLI", "Commands, flags, lifecycle behavior, prerequisites, and common failure modes."],
            ["technology-stack.html", "Technology stack", "React, FastAPI, PostgreSQL, Redis, Prometheus, Loki, Alloy, Alertmanager, Grafana, Docker, Kubernetes, Terraform, Helm."],
            ["deployments.html", "Deployments", "Current deployment request model, records, reports, execution modes, and limitations."],
            ["github-integration.html", "GitHub integration roadmap", "Planned repository analysis, readiness checks, and future reviewed write flows."]
          ])}
        </div>
      </section>

      <section class="section fit-section">
        <div class="container fit-grid">
          <div>
            <span class="eyebrow">How it fits together</span>
            <h2>Interfaces cover complementary parts of the local workflow.</h2>
            <p>The CLI checks and reconciles supported local runtime resources. The web application uses FastAPI for project, deployment, report, audit, and observability data. A fully shared project workflow across both interfaces remains future work.</p>
          </div>
          <div class="flow-diagram flow-diagram-detailed" aria-label="ClueStack data flow">
            <div>CLI</div><span></span><div>Local lifecycle tools</div><span></span><div>Kubernetes</div>
            <div>Dashboard</div><span></span><div>FastAPI</div><span></span><div>Observability · PostgreSQL / JSON</div>
          </div>
        </div>
      </section>

      <section class="section">
        <div class="container grid two">
          <div class="callout"><h2>Available today</h2><ul><li>Installable CLI with local lifecycle commands.</li><li>FastAPI backend with deployment, project, report, audit, workload, metrics, logs, and alerts endpoints.</li><li>React dashboard with live and demo states.</li><li>Docker Compose and Minikube-oriented local workflows.</li><li>Prometheus metrics, Loki logs through Alloy, and Alertmanager alerts when the local stack is running.</li></ul></div>
          <div class="callout warning"><h2>Not implemented today</h2><ul><li>No hosted SaaS or production multi-tenant control plane.</li><li>No GitHub OAuth or arbitrary repository onboarding.</li><li>No automatic builds from arbitrary repositories.</li><li>No production cluster management claim.</li><li>No PyPI publication in the v0.3.0 developer-preview release.</li></ul></div>
        </div>
      </section>
    `,
  },
  {
    href: "product.html",
    nav: "Product",
    title: "ClueStack Product",
    description: "Product overview for ClueStack web app, CLI, Local Mode, and planned GitHub Repository Mode.",
    content: page("Product", "ClueStack is a web application plus CLI for local deployment visibility and observability.", `
      <section><h2>Product surfaces</h2>${cards([
        ["Web Application", "The dashboard presents deployment state, workload and service health, metrics, logs, alerts, reports, and local activity. It talks to the FastAPI backend rather than calling infrastructure tools directly."],
        ["CLI", "The Python CLI provides environment diagnostics, local startup, runtime status, and safe stop operations for the supported local workflow."],
        ["Local Mode", "The current implemented workflow focuses on Docker Compose and Minikube-oriented local infrastructure, with Terraform and Kustomize separating foundation resources from runtime manifests."],
        ["GitHub Repository Mode", "Repository connection and source analysis are planned product capabilities. They are not implemented as OAuth, repository editing, branch creation, or pull-request automation today."]
      ])}</section>
      <section><h2>Useful product pages</h2>${docLinks([
        ["web-app.html", "Web Application", "Dashboard areas, backend endpoints, and data-source status."],
        ["cli.html", "CLI", "Supported commands, options, and lifecycle behavior."],
        ["local-mode.html", "Local Mode", "Supported local runtime model and boundaries."],
        ["github-integration.html", "GitHub Integration", "Planned repository analysis and secure future write workflows."]
      ])}</section>
      <section><h2>Status model</h2>${rows([
        ["Implemented", "Local CLI lifecycle, dashboard/API, project/report repositories, observability endpoints, Docker Compose, Minikube-oriented workflow, Terraform foundation, Kustomize runtime."],
        ["Implemented with limits", "Live telemetry requires reachable observability services. Mock/demo states are available but must not be represented as live telemetry."],
        ["Planned", "Repository analysis, GitHub permissions, reviewed source changes, provider-specific deployment guidance, and richer project-centric workflows."]
      ])}</section>
    `),
  },
  {
    href: "web-app.html",
    title: "ClueStack Web Application",
    description: "ClueStack dashboard areas, backend sources, live data, and demo limitations.",
    content: page("Web Application", "The dashboard is the visual interface for local deployment and observability state.", `
      <section><h2>Dashboard areas</h2>${rows([
        ["Projects", "Project records are exposed through /projects, /projects/default-local, and related project-scoped history endpoints."],
        ["Deployments", "The dashboard can present records from /deployments and project-scoped /projects/{project_id}/deployments."],
        ["Reports", "Read-only report history is exposed through /reports, /reports/{report_id}, and /projects/{project_id}/reports."],
        ["Workloads", "Workload status comes from /workloads/{service}, backed by Kubernetes workload inspection when available."],
        ["Metrics", "Service metrics come from /metrics/{service}. Prometheus is the source of truth and Redis is a short-TTL cache."],
        ["Logs", "Logs come from /logs/{service}. Loki is queried with namespace/app/pod/container labels when available."],
        ["Alerts", "Alert visibility comes from /alerts through Alertmanager when configured."],
        ["Audit history", "Audit logs are exposed through /audit-logs and record local backend activity."]
      ])}</section>
      <section><h2>Live, simulated, and planned states</h2>${rows([
        ["Live", "Kubernetes workload data, Prometheus metrics, Loki logs, and Alertmanager alerts are live only when those services are running and reachable."],
        ["Demo or mock", "Browser Demo Mode and mock observability fallback help first-time exploration but are not proof of a running stack."],
        ["Planned", "GitHub repository browsing, file editing, branches, commits, and pull requests are not implemented in the current web app."]
      ])}</section>
      <section><h2>Related docs</h2>${docLinks([
        ["architecture.html", "Architecture", "How the dashboard connects to FastAPI and backend services."],
        ["observability.html", "Observability", "Metrics, logs, alerts, and troubleshooting unavailable telemetry."],
        ["deployments.html", "Deployments", "How deployment records, reports, and execution modes are represented."]
      ])}</section>
    `),
  },
  {
    href: "cli.html",
    title: "ClueStack CLI",
    description: "Supported ClueStack CLI commands, flags, prerequisites, and examples.",
    content: page("CLI", "The Python CLI is the developer-facing interface for local diagnostics and lifecycle operations.", `
      <section><h2>Commands implemented today</h2>${rows([
        ["infrawatch doctor", "Checks local prerequisites, tooling, repository configuration, rendered manifests, Terraform formatting, and service reachability where applicable."],
        ["infrawatch start", "Starts or reconciles the local Minikube, Terraform, Kustomize, and ClueStack runtime workflow."],
        ["infrawatch status", "Inspects the current Kubernetes runtime state for ClueStack components and observability services."],
        ["infrawatch stop", "Stops app runtime resources while preserving Minikube, Terraform foundation state, and PostgreSQL data by default."]
      ])}</section>
      <section><h2>Common examples</h2>${code(`infrawatch --version\ninfrawatch doctor\ninfrawatch start --namespace infrawatch\ninfrawatch start --install-observability\ninfrawatch start --strict-observability\ninfrawatch status\ninfrawatch stop\ninfrawatch stop --remove-observability`)}</section>
      <section><h2>Important options</h2>${rows([
        ["--namespace", "Supported by doctor, start, status, and stop. Defaults to infrawatch."],
        ["--image-repository", "start option for the DockerHub namespace used by the Kubernetes image workflow. Default is currently parthchn178."],
        ["--minikube-memory-mb / --minikube-cpus", "start options used when Minikube must be started."],
        ["--timeout-seconds", "start and stop timeout control for rollout/deletion waits."],
        ["--install-observability", "start option that also runs the existing observability installer when supported."],
        ["--strict-observability", "start option that disables mock observability fallback after startup."],
        ["--remove-observability", "stop option that also removes ClueStack observability workloads; default stop preserves them."]
      ])}</section>
      <section><h2>Future CLI direction</h2><p>The planned direction is for CLI workflows to share project, deployment, report, and audit concepts with the web app. That work is in progress and should not be described as a complete GitHub repository workflow yet.</p></section>
    `),
  },
  {
    href: "local-mode.html",
    title: "ClueStack Local Mode",
    description: "Current local-first operating model for Docker Compose and Minikube-oriented workflows.",
    content: page("Local Mode", "Local Mode is the current supported product path.", `
      <section><h2>What Local Mode does</h2>${cards([
        ["Diagnose", "doctor checks required files, local tooling, Kubernetes client availability, manifest rendering, Terraform formatting, and reachable services."],
        ["Start", "start can reuse or start Minikube, initialize/validate Terraform, reconcile foundation resources, apply Kustomize runtime resources, and wait for readiness."],
        ["Observe", "status and the dashboard inspect backend, frontend, PostgreSQL, Redis, Prometheus, Grafana, Loki, Alertmanager, and pod health where available."],
        ["Stop safely", "stop removes app runtime resources while preserving Minikube, Terraform foundation resources, PostgreSQL PVC data, and generated secrets by default."]
      ])}</section>
      <section><h2>Boundaries</h2><p>Local Mode is not a universal Kubernetes distribution support promise. Primary validation is around local Docker Desktop and Minikube-oriented workflows. Existing local resources should be inspected before running lifecycle commands.</p></section>
    `),
  },
  {
    href: "github-integration.html",
    title: "ClueStack GitHub Integration Roadmap",
    description: "Planned GitHub Repository Mode design and security boundaries.",
    content: page("GitHub Integration", "GitHub Repository Mode is a planned capability, not a shipped OAuth or repository-editing feature.", `
      <section><h2>Intended workflow</h2>${rows([
        ["Select repository", "Future users should be able to connect to or select a repository with explicit authorization."],
        ["Analyze source", "Read-only source analysis should inspect repository structure and detect technology stack and deployment hints before any build or deployment."],
        ["Assess readiness", "ClueStack should identify missing deployment configuration, unsupported patterns, and prerequisites."],
        ["Guide deployment", "The product should guide users toward supported targets and explain telemetry/rollback limitations per target."],
        ["Review changes", "Future write workflows may propose file changes through diffs before any branch, commit, or pull request."],
        ["Approve writes", "Branch, commit, and pull-request workflows must require explicit user approval and least-privilege permissions."]
      ])}</section>
      <section><h2>Status</h2>${rows([
        ["Implemented today", "No GitHub OAuth, repository analyzer, branch creation, commits, or pull-request automation is implemented in the current codebase."],
        ["Planned first", "Read-only repository analysis and deployment-readiness guidance."],
        ["Later", "Authenticated repository access, proposed changes, diffs, branches, commits, and pull requests with explicit authorization."]
      ])}</section>
      <section><h2>Security approach</h2><p>Future implementation should separate repository analysis from deployment execution, protect credentials, request least-privilege permissions, avoid arbitrary code execution during analysis, and record audit events for consequential actions.</p></section>
    `),
  },
  {
    href: "technology-stack.html",
    nav: "Stack",
    title: "ClueStack Technology Stack",
    description: "Technology roles in the ClueStack implementation.",
    content: page("Technology Stack", "ClueStack combines a web app, API, CLI, local runtime, and observability stack.", `
      <section><h2>Implementation roles</h2>${rows([
        ["React and Vite", "Frontend application and build tooling for the dashboard."],
        ["FastAPI", "Backend API, service layer, deployment orchestration, and observability endpoints."],
        ["Python CLI", "Installable developer-facing diagnostics and lifecycle commands."],
        ["PostgreSQL", "Durable project, deployment, report, and audit persistence when DATABASE_URL is configured."],
        ["JSON repositories", "Local file-backed persistence fallback for project/report/deployment/audit state where configured."],
        ["Redis", "Short-TTL cache for repeated Prometheus dashboard queries. It does not replace Prometheus."],
        ["Prometheus", "Metrics source for dashboard service metrics."],
        ["Loki", "Log storage queried by the backend logs endpoint."],
        ["Grafana Alloy", "Telemetry/log collection and forwarding with Kubernetes labels."],
        ["Alertmanager", "Alert source for the backend alerts endpoint."],
        ["Grafana", "Native observability dashboard available in the local stack."],
        ["Docker Compose", "Local multi-service development runtime."],
        ["Kubernetes and Kustomize", "Runtime manifests for backend, frontend, PostgreSQL, Redis, HPA, Services, and app ConfigMaps."],
        ["Terraform", "Foundation resources such as namespace, RBAC, ServiceAccount, ResourceQuota, and backend runtime ConfigMap."],
        ["Helm", "Used for applicable observability components such as Loki and Grafana Alloy."]
      ])}</section>
      <section><h2>Architecture map</h2>${code(`User -> Web dashboard / CLI\nWeb dashboard -> Nginx /api proxy -> FastAPI\nCLI -> local Docker, Minikube, Terraform, Kustomize, kubectl\nFastAPI -> PostgreSQL or JSON repositories\nFastAPI -> Redis cache -> Prometheus queries\nFastAPI -> Kubernetes API for workloads\nFastAPI -> Loki for logs\nFastAPI -> Alertmanager for alerts\nGrafana remains available as a native observability UI`)}</section>
      <section><h2>Related pages</h2>${docLinks([
        ["architecture.html", "Architecture", "Layered architecture and data ownership."],
        ["observability.html", "Observability", "Metrics, logs, alerts, dashboards, and telemetry fallback states."],
        ["configuration.html", "Configuration", "Environment variables and local service URLs."]
      ])}</section>
    `),
  },
  {
    href: "docs.html",
    nav: "Docs",
    title: "ClueStack Documentation",
    description: "Documentation hub for installing, running, configuring, and troubleshooting ClueStack.",
    content: `
      <section class="page-hero"><div class="container"><span class="eyebrow">Documentation</span><h1>Run and understand ClueStack locally.</h1><p class="lead">Practical guides for setup, configuration, deployments, observability, troubleshooting, and product direction.</p></div></section>
      <section class="section docs-overview"><div class="container">
        ${docLinks([
          ["docs-quick-start.html", "Quick Start", "Fastest path through prerequisites, local install, doctor, start, status, and stop."],
          ["installation.html", "Installation", "Pre-PyPI local install and wheel-based install from a checkout."],
          ["configuration.html", "Configuration", "Environment variables, defaults, optional values, service URLs, and secret handling."],
          ["deployments.html", "Deployments", "Deployment API behavior, execution modes, records, reports, and current limits."],
          ["observability.html", "Observability", "Metrics, logs, alerts, Grafana, and troubleshooting unavailable telemetry."],
          ["docs-troubleshooting.html", "Troubleshooting", "Symptoms, likely causes, safe diagnostics, and cleanup guidance."],
          ["architecture.html", "Architecture", "System layers, ownership, data flows, persistence, and security boundaries."],
          ["roadmap.html", "Roadmap", "Next-version direction and planned GitHub Repository Mode."]
        ])}
      </div></section>
    `,
  },
  {
    href: "docs-quick-start.html",
    title: "ClueStack Quick Start",
    description: "Quick start commands for Docker Compose, CLI, and local Kubernetes workflows.",
    content: page("Quick Start", "Start with diagnostics, then choose Docker Compose or the Minikube-oriented CLI path.", `
      <section><h2>Prerequisites</h2><ul><li>Docker Desktop or Docker Engine with Compose for Docker Compose.</li><li>Python 3.11+, Minikube, kubectl, Terraform, and Helm for local Kubernetes.</li><li>Node.js and npm only for frontend or website development.</li></ul></section>
      <section><h2>Install from checkout</h2>${code(`python -m pip install -e .\ninfrawatch --version\ninfrawatch doctor`)}</section>
      <section><h2>Docker Compose path</h2><p>Use Compose when you want a local multi-service stack without validating the Kubernetes workflow.</p>${code(`Copy-Item .env.example .env\n.\\scripts\\start-local.ps1 -Detached\n.\\scripts\\doctor.ps1`)}</section>
      <section><h2>Local Kubernetes path</h2><p>The CLI path checks prerequisites, reconciles Terraform foundation resources, applies Kustomize runtime resources, and reports readiness.</p>${code(`infrawatch doctor\ninfrawatch start\ninfrawatch status\ninfrawatch stop`)}</section>
      <section><h2>Verify access</h2>${rows([
        ["Dashboard", "http://localhost:3000"],
        ["FastAPI docs", "http://localhost:8000/docs"],
        ["Grafana", "http://localhost:3001"],
        ["Prometheus", "http://localhost:9090"],
        ["Loki", "http://localhost:3100"],
        ["Alertmanager", "http://localhost:9093"]
      ])}</section>
    `),
  },
  {
    href: "installation.html",
    title: "ClueStack Installation",
    description: "ClueStack local installation paths before PyPI publication.",
    content: page("Installation", "ClueStack v0.3.0 is documented as a local checkout or local wheel install, not a PyPI install.", `
      <section><h2>Editable local install</h2>${code(`git clone https://github.com/ParthrChandurkar/InfraWatch.git\ncd InfraWatch\npython -m pip install -e .\ninfrawatch --help`)}</section>
      <section><h2>Local wheel validation</h2>${code(`python -m pip install build\npython -m build\npython -m venv C:\\Temp\\infrawatch-wheel-test\nC:\\Temp\\infrawatch-wheel-test\\Scripts\\python.exe -m pip install .\\dist\\infrawatch-0.3.0-py3-none-any.whl\nC:\\Temp\\infrawatch-wheel-test\\Scripts\\infrawatch.exe --version`)}</section>
      <section><h2>Packaged runtime resources</h2><p>The Python package includes runtime Terraform, Kubernetes, monitoring, logging, and helper resources so the CLI can materialize local resources outside a repository checkout.</p></section>
    `),
  },
  {
    href: "configuration.html",
    title: "ClueStack Configuration",
    description: "Environment variables and local configuration used by ClueStack.",
    content: page("Configuration", "Configuration is environment-backed. Do not commit real secrets.", `
      <section><h2>Core settings</h2>${rows([
        ["COMPOSE_PROJECT_NAME", "Docker Compose project name."],
        ["POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB", "Local PostgreSQL credentials for the development stack."],
        ["DATABASE_URL or INFRAWATCH_DATABASE_URL", "Backend database URL. When configured, PostgreSQL-backed repositories are used."],
        ["VITE_API_BASE_URL", "Frontend API base path, typically /api behind Nginx."],
        ["INFRAWATCH_ALLOW_MOCK_OBSERVABILITY", "Allows fallback sample observability data when live sources are unavailable."],
        ["REDIS_URL / INFRAWATCH_REDIS_URL", "Redis connection string."],
        ["REDIS_CACHE_TTL / INFRAWATCH_REDIS_CACHE_TTL", "Short cache TTL for repeated Prometheus dashboard queries."]
      ])}</section>
      <section><h2>Service ports</h2>${rows([
        ["FRONTEND_PORT", "Default 3000."],
        ["BACKEND_PORT", "Default 8000."],
        ["POSTGRES_PORT", "Default 5432."],
        ["PROMETHEUS_PORT", "Default 9090."],
        ["GRAFANA_PORT", "Default 3001."],
        ["LOKI_PORT", "Default 3100."],
        ["ALLOY_PORT", "Default 12345."],
        ["ALERTMANAGER_PORT", "Default 9093."]
      ])}</section>
      <section><h2>Secret handling</h2><p>Never commit real .env files, kubeconfig files, Terraform state, private keys, DockerHub tokens, GitHub tokens, SMTP credentials, or webhook URLs.</p></section>
    `),
  },
  {
    href: "deployments.html",
    title: "ClueStack Deployments",
    description: "Current deployment workflow, records, reports, execution modes, and limitations.",
    content: page("Deployments", "Deployment support currently focuses on local workflow visibility and controlled local execution modes.", `
      <section><h2>Backend endpoints</h2>${rows([
        ["POST /deploy", "Creates a deployment attempt after request validation and can generate sanitized deployment reports."],
        ["GET /deployments", "Lists deployment records."],
        ["DELETE /deployment/{name}", "Deletes a named deployment through the backend workflow."],
        ["POST /deployment/{name}/rollback", "Rolls back a named deployment where supported by the backend logic."],
        ["GET /reports", "Lists deployment report history, optionally filtered by project or deployment."],
        ["GET /audit-logs", "Lists local audit entries."]
      ])}</section>
      <section><h2>Execution modes and limits</h2>${rows([
        ["Simulated/demo behavior", "The backend can record safe simulated deployments when Kubernetes execution is not enabled."],
        ["Kubernetes execution", "When configured, the backend can apply Kubernetes manifests with kubectl and verify rollout state."],
        ["Reports", "Deployment report persistence stores sanitized evidence and bounded error details."],
        ["Limits", "Arbitrary repository builds and GitHub onboarding are not implemented today."]
      ])}</section>
    `),
  },
  {
    href: "observability.html",
    title: "ClueStack Observability",
    description: "Metrics, logs, alerts, Grafana, and telemetry troubleshooting.",
    content: page("Observability", "ClueStack surfaces observability data from local tools when they are running and reachable.", `
      <section><h2>Backend observability endpoints</h2>${rows([
        ["GET /metrics/{service}", "Queries Prometheus for service metrics and uses Redis as a short-TTL cache."],
        ["GET /workloads/{service}", "Reads Kubernetes workload health for a service."],
        ["GET /logs/{service}", "Queries Loki logs with filters for service, pod, container, search, and time window where supported."],
        ["GET /alerts", "Reads Alertmanager alert data."],
        ["GET /healthz", "Backend health check endpoint."]
      ])}</section>
      <section><h2>Component roles</h2>${rows([
        ["Prometheus", "Metrics source of truth."],
        ["Redis", "Temporary cache for repeated Prometheus dashboard queries."],
        ["Loki", "Log storage and query backend."],
        ["Grafana Alloy", "Collects and forwards Kubernetes logs with useful labels."],
        ["Alertmanager", "Alert source for current alert visibility."],
        ["Grafana", "Native observability dashboard available in the local stack."]
      ])}</section>
      <section><h2>Troubleshooting unavailable telemetry</h2><p>If a telemetry source is down or unreachable, the dashboard should show unavailable, no-data, or mock states depending on configuration. Do not treat fallback data as proof of live Prometheus, Loki, or Alertmanager connectivity.</p></section>
    `),
  },
  {
    href: "architecture.html",
    nav: "Architecture",
    title: "ClueStack Architecture",
    description: "Architecture and component responsibilities for ClueStack.",
    content: page("Architecture", "ClueStack is layered around two user interfaces, a FastAPI backend, persistence repositories, local deployment tooling, and observability backends.", `
      <section><h2>System layers</h2>${rows([
        ["Interfaces", "React web app and Python CLI."],
        ["Backend API", "FastAPI routes and service layer for deployments, projects, reports, audit logs, workloads, metrics, logs, and alerts."],
        ["Persistence", "PostgreSQL when DATABASE_URL is configured; JSON-backed repositories for local fallback paths."],
        ["Deployment execution", "Docker Compose for local development; Minikube, Terraform, Kustomize, and kubectl for the Kubernetes-oriented workflow."],
        ["Observability", "Prometheus metrics, Loki logs, Grafana Alloy collection, Alertmanager alerts, and Grafana dashboards."]
      ])}</section>
      <section><h2>High-level flow</h2>${code(`User -> Web App / CLI\nWeb App -> Nginx /api proxy -> FastAPI\nCLI -> local tools and packaged runtime resources\nFastAPI -> repositories: PostgreSQL or JSON files\nFastAPI -> Kubernetes API for workloads\nFastAPI -> Redis -> Prometheus for metrics\nFastAPI -> Loki for logs\nFastAPI -> Alertmanager for alerts`)}</section>
      <section><h2>Ownership boundaries</h2><p>Terraform owns foundation resources such as namespace, RBAC, ServiceAccount, ResourceQuota, and backend runtime ConfigMap. Kustomize owns runtime resources such as backend, frontend, PostgreSQL, Redis, HPA, Services, and app ConfigMaps. Loki and Grafana Alloy are installed through Helm where applicable.</p></section>
      <section><h2>Security boundaries</h2><p>Current local workflows do not implement user accounts, OAuth, multi-tenancy, or hosted SaaS isolation. Future GitHub and provider integrations must add least-privilege permissions, protected credentials, explicit approvals, and audit records before sensitive operations.</p></section>
    `),
  },
  {
    href: "docs-troubleshooting.html",
    title: "ClueStack Troubleshooting",
    description: "Troubleshooting and safe cleanup guidance for ClueStack.",
    content: page("Troubleshooting", "Use read-only diagnosis first. Avoid destructive cleanup unless you intentionally want to remove local state.", `
      <section><h2>Symptoms and safe checks</h2>${rows([
        ["Docker daemon unreachable", "Start Docker Desktop or Docker Engine, then rerun doctor. Compose and Minikube Docker-driver workflows need Docker."],
        ["Kubernetes API unreachable", "Check kubectl context and minikube status before rerunning startup commands."],
        ["Terraform plan fails", "Confirm the Kubernetes API is reachable and the configured context points to the intended local cluster."],
        ["Telemetry unavailable", "Check Prometheus, Loki, Alertmanager, and Grafana readiness. Mock data is not live telemetry."],
        ["Frontend/API unreachable", "Check local ports, Compose services, Kubernetes services, and backend /healthz."]
      ])}</section>
      <section><h2>Useful commands</h2>${code(`infrawatch doctor\ninfrawatch status\nkubectl config current-context\nminikube status\ndocker compose ps`)}</section>
      <section><h2>Safe cleanup</h2><p>The normal stop path preserves Minikube, Terraform foundation resources, PostgreSQL PVC data, and generated secrets needed for restart.</p>${code(`infrawatch stop`)}</section>
    `),
  },
  {
    href: "roadmap.html",
    nav: "Roadmap",
    title: "ClueStack Roadmap",
    description: "Directional roadmap and current project status for ClueStack.",
    content: page("Roadmap", "The roadmap is directional, not a fixed delivery schedule.", `
      <section><h2>Capability roadmap</h2>${rows([
        ["Core reliability", "Deployment/report consistency, clearer errors, stronger tests, fresh-machine validation, and safer startup/stop behavior."],
        ["Unified web app and CLI", "Shared project selection, deployment history, status, reports, and audit records."],
        ["GitHub Repository Mode", "Read-only repository analysis first, then secure authenticated repository access when ready."],
        ["Deployment guidance", "Identify requirements, supported targets, telemetry limits, and rollback availability by target."],
        ["Reviewed repository changes", "Future proposed edits, diffs, branches, commits, and pull requests with explicit authorization."],
        ["Security and multi-user readiness", "Authentication, authorization, credential handling, and isolation only if multi-user operation is pursued."]
      ])}</section>
      <section><h2>Status labels</h2>${rows([
        ["Implemented", "Local-first CLI, backend APIs, dashboard, persistence, reports, metrics, logs, alerts, Docker Compose, and Minikube-oriented workflow."],
        ["In progress", "Project/deployment/report model expansion and shared web/CLI state."],
        ["Planned", "Repository Mode, reviewed Git writes, provider adapters, and stronger security boundaries."]
      ])}</section>
    `),
  },
  {
    href: "releases.html",
    title: "ClueStack Releases",
    description: "ClueStack release and version history.",
    content: page("Releases", "Release statements here are limited to verified repository evidence.", `
      <section><h2>Latest verified tag</h2><p>The local repository contains the tag <strong>v0.3.0</strong>. That developer preview was published under the project's former InfraWatch name. The Python package metadata in this checkout declares version <strong>0.3.0</strong>.</p><p><a class="button" href="https://github.com/ParthrChandurkar/InfraWatch/releases/tag/v0.3.0">Open GitHub release</a></p></section>
      <section><h2>Version scope</h2><p>v0.3.0 is a developer preview for local-first usage. It is not presented as a stable production release, hosted SaaS release, or PyPI publication.</p></section>
    `),
  },
  {
    href: "community.html",
    nav: "Contributing",
    title: "ClueStack Community",
    description: "Contribution, governance, license, and security resources for ClueStack.",
    content: page("Contributing", "Contribution paths are repository-based. No private maintainer email, sponsor program, or community size is invented here.", `
      <section><h2>Contributing</h2><p>Contributors should use focused pull requests, include validation results, and update documentation when behavior changes.</p><p><a href="https://github.com/ParthrChandurkar/InfraWatch/blob/main/CONTRIBUTING.md">Read CONTRIBUTING.md</a></p></section>
      <section><h2>Maintainers</h2><p>The local maintainer file lists Parth Chandurkar as maintainer. See <a href="https://github.com/ParthrChandurkar/InfraWatch/blob/main/MAINTAINERS.md">MAINTAINERS.md</a>.</p></section>
      <section><h2>Security</h2><p>Use GitHub private vulnerability reporting for security vulnerabilities when available. Do not publish exploitable details, tokens, kubeconfig contents, or private logs in public issues.</p></section>
      <section><h2>License</h2><p>ClueStack is licensed under the Apache License 2.0. Review the repository <a href="https://github.com/ParthrChandurkar/InfraWatch/blob/main/LICENSE">LICENSE</a> for the full license text.</p></section>
    `),
  },
  {
    href: "brand.html",
    title: "ClueStack Brand",
    description: "ClueStack logo and brand design notes.",
    content: page("Brand note", "ClueStack uses an investigative lens and connected signals as its visual identity.", `
      <section><h2>Logo usage</h2><p>The compact mark combines a magnifying lens, signal trace, connected nodes, and a restrained detective-hat cue. The navigation, favicon, homepage hero, dashboard, and footer reuse the same SVG identity.</p></section>
      <section><h2>Palette</h2><div class="badge-list"><span class="badge">Midnight navy #07111f</span><span class="badge">Panel blue #132842</span><span class="badge">Signal teal #5eead4</span><span class="badge">Trace blue #22a6d5</span><span class="badge">Evidence amber #ffbd4a</span></div></section>
    `),
  },
  {
    href: "404.html",
    title: "Page not found - ClueStack",
    description: "The requested ClueStack website page was not found.",
    content: `<section class="page-hero"><div class="container"><span class="eyebrow">404</span><h1>Page not found.</h1><p class="lead">Return to the home page or documentation hub.</p><div class="actions"><a class="button primary" href="index.html">Home</a><a class="button" href="docs.html">Documentation</a></div></div></section>`,
  },
];

function page(title, intro, body) {
  return `
    <section class="page-hero"><div class="container"><span class="eyebrow">ClueStack</span><h1>${title}</h1><p class="lead">${intro}</p></div></section>
    <div class="container page-grid">
      <aside class="side-nav" aria-label="Page sections">
        <a href="product.html">Product</a>
        <a href="web-app.html">Web App</a>
        <a href="cli.html">CLI</a>
        <a href="github-integration.html">GitHub Mode</a>
        <a href="technology-stack.html">Stack</a>
        <a href="docs.html">Docs hub</a>
        <a href="docs-quick-start.html">Quick Start</a>
        <a href="architecture.html">Architecture</a>
        <a href="observability.html">Observability</a>
      </aside>
      <article class="content-panel">${body}</article>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
