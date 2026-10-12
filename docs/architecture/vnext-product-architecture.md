# InfraWatch vNext Product Architecture

## 1. Purpose and product contract

InfraWatch vNext is intended to be a full web application plus CLI for students and developers who want to understand, deploy, and observe applications through one accessible interface.

InfraWatch should provide two first-class workflows:

1. **Local Mode** — inspect, deploy, and observe supported local applications and local environments, including Docker and Minikube where appropriate.
2. **GitHub Repository Mode** — connect a repository, inspect its files, analyze its stack, check readiness, review changes, and guide deployment to supported targets.

Both workflows should share a consistent project, deployment, report, and audit-event model where the underlying operation supports it. The web app and CLI should use consistent APIs and state rather than becoming separate products.

This document distinguishes:

- capabilities verified in the current repository;
- planned vNext capabilities;
- unresolved owner decisions.

Planned capabilities are not implemented unless explicitly listed as current verified architecture.

## 2. Current verified architecture

### 2.1 Web frontend

Current implementation:

- React and TypeScript frontend in `frontend/src`.
- Main dashboard in `frontend/src/App.tsx`.
- API client in `frontend/src/api.ts`.
- browser-only demo store in `frontend/src/demoStore.ts`.
- styling in `frontend/src/styles.css`.

The current web UI is a single-page dashboard with anchor navigation:

- Overview
- Deploy
- Workloads
- Observability
- Alerts
- Logs

Verified current UI capabilities:

- service sidebar;
- browser Demo Mode labels;
- deployment status summary cards;
- deploy form;
- workload/deployment table;
- metrics charts;
- logs panel with search, pod filter, and time-window controls;
- alerts panel with filters;
- audit trail.

Browser Demo Mode is implemented with `VITE_DEMO_MODE=true`. In that mode the frontend uses browser `localStorage` and does not require Docker, Kubernetes, Minikube, a backend, PostgreSQL, Redis, Prometheus, Loki, Alloy, or Alertmanager.

### 2.2 Backend API

Current implementation:

- FastAPI app in `backend/app/main.py`.
- routes in `backend/app/api.py`.
- configuration in `backend/app/config.py`.
- persistence in `backend/app/repository.py`.
- deployment orchestration in `backend/app/services/deployments.py`.
- observability clients in `backend/app/services/observability.py`.

Verified current endpoints:

- `GET /`
- `GET /healthz`
- `GET /metrics`
- `GET /internal/metrics`
- `POST /deploy`
- `GET /deployments`
- `GET /audit-logs`
- `GET /metrics/{service}`
- `GET /workloads/{service}`
- `GET /logs/{service}`
- `GET /alerts`
- `DELETE /deployment/{name}`
- `POST /deployment/{name}/rollback`

Current backend deployment modes:

- manifest simulation when Kubernetes execution is disabled;
- Kubernetes execution through `kubectl` when enabled.

Current rollback is Kubernetes-specific through `kubectl rollout undo`. Browser Demo Mode rollback is simulated.

### 2.3 CLI

Current implementation:

- CLI entry point in `infrawatch/cli.py`.
- diagnostics and status checks in `infrawatch/checks.py`.
- startup flow in `infrawatch/start.py`.
- stop flow in `infrawatch/stop.py`.
- packaged runtime resource handling in `infrawatch/runtime_resources.py`.

Verified current commands:

- `infrawatch doctor`
- `infrawatch start`
- `infrawatch status`
- `infrawatch stop`

The CLI currently orchestrates local tools and resource definitions. It does not replace Docker, Terraform, Kustomize, kubectl, Helm, or Kubernetes.

### 2.4 Persistence

Current implementation:

- PostgreSQL persistence when `DATABASE_URL` or `INFRAWATCH_DATABASE_URL` is configured.
- JSON-file fallback when no database URL is configured.
- deployment records and audit logs stored through repository interfaces in `backend/app/repository.py`.
- PostgreSQL tables created with `CREATE TABLE IF NOT EXISTS`.

There is no formal schema migration framework today.

### 2.5 Observability

Current implementation:

- Prometheus is the metrics source of truth.
- Loki is the log source of truth.
- Alertmanager is the alert source of truth.
- Grafana Alloy collects and labels logs for Loki.
- Grafana provides dashboards and visualization.
- Redis is a temporary cache for repeated Prometheus dashboard metric responses.

Current backend behavior:

- live metrics return `source="prometheus"`;
- live logs return `source="loki"`;
- live alerts return `source="alertmanager"`;
- metrics fallback can return `source="mock"` when fallback is enabled;
- Redis failures behave like cache misses and should not take down InfraWatch.

### 2.6 Local infrastructure ownership

Current ownership boundary:

- Terraform owns local Kubernetes foundation resources:
  - namespace;
  - backend ServiceAccount;
  - ClusterRole;
  - ClusterRoleBinding;
  - namespace Role;
  - namespace RoleBinding;
  - ResourceQuota;
  - backend runtime ConfigMap.
- Kustomize owns application resources:
  - backend;
  - frontend;
  - PostgreSQL;
  - Redis;
  - Services;
  - HPA;
  - app ConfigMaps;
  - manifest-managed observability components.
- Helm is used for Loki and Grafana Alloy.
- Docker Compose owns the local Compose development stack.

## 3. Product modes

### 3.1 Local Mode

Local Mode helps users inspect, deploy, and observe supported applications and local environments.

Local Mode may use:

- browser-only Demo Mode;
- Docker Compose;
- local Docker resources;
- Minikube;
- Kubernetes;
- Terraform;
- Kustomize;
- Helm;
- Prometheus;
- Loki;
- Grafana Alloy;
- Alertmanager;
- Grafana;
- PostgreSQL;
- Redis.

Local Mode should support both web app and CLI entry points where appropriate.

Examples:

- CLI checks prerequisites with `infrawatch doctor`.
- CLI starts a supported local Kubernetes workflow with `infrawatch start`.
- web app displays local workloads, deployments, health, metrics, logs, alerts, and reports.
- web app requests target confirmation before changing local resources.

Local Mode does not require a GitHub repository.

### 3.2 GitHub Repository Mode

GitHub Repository Mode starts from a repository.

The first capability must be read-only repository analysis:

- connect a public GitHub repository;
- inspect files safely;
- detect stack evidence;
- assess readiness;
- identify missing prerequisites;
- recommend supported next actions.

Repository Mode should later support controlled repository operations, subject to authentication, authorization, and explicit approval:

- browse repository files;
- propose file edits;
- review diffs;
- create a branch;
- commit approved changes;
- open a pull request;
- guide deployment to supported targets.

Repository Mode must not assume every repository is deployable. It must not execute repository code during analysis. Private repository support is deferred until permissions, credentials, and secret handling are designed.

### 3.3 Shared behavior across modes

Local Mode and GitHub Repository Mode should share:

- project records;
- deployment records;
- deployment reports;
- audit events;
- integration status;
- supported target capability matrices;
- source labels for metrics, logs, and alerts;
- honest unavailable states.

The web app and CLI should use consistent backend APIs and state models where the same operation is supported in both interfaces.

## 4. First-class interfaces

### 4.1 Web application

The web app is the primary visual interface.

It should provide a polished, detailed dashboard inspired by the owner-provided Headlamp-style reference and the existing dark InfraWatch dashboard direction.

The web app should support:

- project browsing;
- repository connection and analysis;
- local resource overview;
- deployment actions where supported;
- workload/resource status;
- observability views;
- deployment reports;
- integration setup;
- settings.

Web actions that change files, Git state, local resources, deployments, provider resources, or rollback state must require clear user approval.

### 4.2 CLI

The CLI remains a first-class interface for local workflows, automation-friendly checks, and scripted setup.

The CLI should support:

- environment diagnostics;
- local setup guidance;
- Local Mode start/status/stop;
- read-only project/status/report inspection where appropriate;
- repository analysis commands where appropriate;
- safe cleanup commands with explicit scope.

The CLI should not become a separate product. It should share the same product language, state model, and capability boundaries as the web app.

## 5. Proposed web navigation

The vNext web app should use clear top-level navigation.

### 5.1 Projects

Purpose:

- list known projects;
- show project health summary;
- show latest deployment;
- show repository/target association;
- show unresolved issues.

### 5.2 Repositories

Purpose:

- connect a GitHub repository;
- browse repository files;
- show detected languages/frameworks/manifests;
- show readiness report;
- show proposed file edits;
- show diff review;
- create branch/commit/pull request only after authorization and explicit approval.

Initial implementation should be read-only analysis only.

### 5.3 Workloads / Resources

Purpose:

- show local Docker/Kubernetes resources where supported;
- show workloads, pods, services, deployments, HPA, and resource status where available;
- provide safe supported actions with target confirmation.

InfraWatch should not claim to replace Headlamp or kubectl. It should surface a focused view for InfraWatch-managed or InfraWatch-observed applications.

### 5.4 Deployments

Purpose:

- show deployment history;
- show build/deploy status;
- show target/environment;
- show deployed URL when available;
- show image/version/commit;
- show rollback availability and result.

### 5.5 Observability

Purpose:

- show health checks;
- show metrics source and query window;
- show resource usage;
- show unavailable telemetry explicitly.

### 5.6 Logs

Purpose:

- show logs from Loki or provider-specific log integrations;
- support filters where source supports them;
- show unavailable states clearly.

### 5.7 Alerts

Purpose:

- show Alertmanager alerts or provider-specific alert signals;
- show severity, status, affected service, and evidence;
- show empty and unavailable states distinctly.

### 5.8 Reports

Purpose:

- show deployment reports;
- compare deployments;
- show health, metrics, logs, alerts, and incident evidence;
- show unavailable data explicitly;
- export reports only after redaction rules are defined.

### 5.9 Integrations

Purpose:

- show Docker, Minikube, Kubernetes, Prometheus, Loki, Alertmanager, Grafana, GitHub, and provider-adapter status;
- show setup requirements;
- guide connection and readiness checks.

### 5.10 Settings

Purpose:

- manage local preferences;
- manage credential references without exposing values;
- manage retention policies;
- manage supported targets;
- manage cleanup behavior.

## 6. Repository onboarding and repository operations

### 6.1 Initial read-only analysis

The first Repository Mode capability must be read-only.

Flow:

1. User provides a GitHub repository URL.
2. InfraWatch validates the URL.
3. InfraWatch confirms the repository is public for the initial version.
4. InfraWatch performs bounded file inspection.
5. InfraWatch detects evidence.
6. InfraWatch produces a readiness report.
7. InfraWatch recommends supported next steps.

Read-only analysis must not:

- run repository scripts;
- install dependencies;
- build images;
- deploy workloads;
- print secret-like values;
- write to Git;
- create branches, commits, or pull requests.

### 6.2 Evidence to detect

Repository analysis should inspect evidence such as:

- language files;
- package manifests;
- lockfiles;
- Dockerfile;
- Compose files;
- Kubernetes manifests;
- Helm charts;
- Terraform files;
- frontend build configuration;
- backend service configuration;
- health endpoint hints;
- exposed ports;
- deployment documentation.

The scanner should record evidence paths, confidence, and missing prerequisites.

### 6.3 Proposed file editing and Git writes

Future Repository Mode may propose file edits, but edits must follow strict boundaries:

- show proposed changes before applying;
- show a diff;
- require explicit approval;
- create a new branch rather than writing to the default branch;
- commit only approved changes;
- open a pull request only after explicit approval;
- record an audit event for each consequential action.

These capabilities require authentication, authorization, repository permissions, and safe credential handling. They are not part of the first read-only repository analysis increment.

## 7. Deployment contract

InfraWatch should start with a limited deployment contract. It should not begin with arbitrary repository builds.

### 7.1 Container-image-first deployment

This should be the first deployment contract.

Supported input:

- image reference;
- service name;
- port;
- replicas;
- optional environment variables;
- optional health-check path;
- optional resource requests and limits.

Validation:

- service name shape;
- image reference shape;
- replica bounds;
- port bounds;
- environment variable shape and redaction rules;
- resource request/limit bounds;
- namespace ownership;
- target capability check;
- denial of privileged or host-level settings.

This contract maps well to the current backend deployment service.

### 7.2 Constrained Dockerfile-based workflow

Dockerfile-based deployment should be a later workflow.

It requires:

- safe build isolation;
- timeout;
- resource limits;
- build log redaction;
- image tag policy;
- cleanup policy;
- explicit approval before build and deploy.

Unsupported initially:

- arbitrary repository script execution;
- private repository builds;
- unbounded monorepo builds;
- deployment of generated manifests without review;
- automatic builds without approval.

### 7.3 Local resource-management actions

Local Mode may support resource-management actions only when scoped and confirmed.

Examples:

- deploy supported local workload;
- delete an InfraWatch-managed workload;
- rollback a Kubernetes Deployment where rollout history supports it;
- stop InfraWatch-managed app runtime resources;
- install or remove observability only after confirmation.

Every action must show:

- target;
- namespace/environment;
- affected resources;
- whether data is preserved or deleted;
- whether rollback is available.

## 8. Shared project, deployment, report, and audit model

Both the web app and CLI should use a shared state model.

### 8.1 Project

Represents an application or repository tracked by InfraWatch.

Fields:

- id;
- name;
- mode: Local Mode, GitHub Repository Mode, or both;
- repository URL, when available;
- repository visibility;
- detected stack summary;
- default target;
- created timestamp;
- updated timestamp;
- status.

### 8.2 Repository analysis

Represents one read-only repository scan.

Fields:

- id;
- project id;
- repository URL;
- commit SHA or branch reference;
- scan started/completed timestamps;
- detected languages;
- detected frameworks;
- detected manifests;
- readiness result;
- confidence score;
- unsupported reasons;
- missing prerequisites;
- evidence references.

### 8.3 Deployment target

Represents where an application may run.

Fields:

- id;
- project id;
- target type: local Docker, local Kubernetes, provider adapter, or other explicit target;
- environment name;
- namespace or provider project id;
- capability matrix;
- credential reference, if required;
- status.

### 8.4 Deployment

Represents one deployment attempt.

Fields:

- id;
- project id;
- target id;
- repository URL, if available;
- commit SHA, if available;
- branch name, if available;
- image reference;
- image digest, if available;
- config or manifest revision;
- deployment status;
- build status, if applicable;
- started timestamp;
- completed timestamp;
- deployed URL, if available;
- failure reason;
- rollback capability;
- rollback status.

### 8.5 Deployment report

Represents collected evidence around a deployment.

Fields:

- id;
- deployment id;
- build result;
- deployment result;
- health-check results;
- metric snapshot references;
- log query references;
- alert references;
- before/after windows;
- incident observations;
- unavailable data list;
- generated timestamp.

### 8.6 Audit event

Represents a user or system action.

Fields:

- id;
- actor;
- interface: web app or CLI;
- action;
- target;
- before/after references where safe;
- status;
- timestamp;
- redacted metadata.

Audit events should be recorded for repository writes, deployment actions, rollback actions, cleanup, credential changes, and integration changes.

## 9. Persistence and data sources

PostgreSQL should remain the initial durable internal database recommendation.

Reasons:

- the current backend already supports PostgreSQL;
- project, deployment, report, and audit records are structured and relational;
- adding another database is not justified by the current requirements.

JSON fallback may remain for tests, demo mode, and lightweight local usage, but should not be the primary vNext durable history store.

Required additions:

- schema migration framework;
- schema versioning;
- retention policy;
- report compaction policy;
- audit retention configuration;
- local backup/restore guidance.

Source-of-truth boundaries:

- PostgreSQL: InfraWatch project, deployment, report, and audit records.
- Prometheus: metrics source of truth.
- Loki: logs source of truth.
- Alertmanager: alert source of truth where available.
- Redis: temporary cache only.
- Grafana: visualization, not InfraWatch API source of truth.

Unavailable data must be represented explicitly rather than inferred.

## 10. Observability and installation

### 10.1 Existing local responsibilities

Docker Compose:

- runs local backend, frontend, PostgreSQL, Redis, Prometheus, Alertmanager, Grafana, Loki, and Alloy.

Terraform:

- owns local Kubernetes foundation resources and RBAC.

Kustomize:

- owns application workloads and manifest-managed observability components.

Helm:

- owns Loki and Grafana Alloy installation.

CLI:

- checks prerequisites;
- starts local workflows;
- applies Terraform and Kustomize;
- starts observability when requested;
- checks status;
- stops runtime resources safely.

### 10.2 Guided setup

The web app and CLI should both support guided setup where appropriate.

Guided setup should:

1. detect prerequisites;
2. show missing tools;
3. identify current mode;
4. explain required actions;
5. request approval before infrastructure changes;
6. verify readiness after each step;
7. show failures clearly;
8. avoid destructive cleanup by default.

The setup flow must not destroy clusters, PVCs, Terraform state, provider resources, branches, commits, or pull requests without explicit user approval.

## 11. Hosting and provider integrations

Provider integrations should be incremental and adapter-based.

Each provider adapter must declare a capability matrix:

- can deploy;
- can read deployment status;
- can provide deployed URL;
- can read health checks;
- can read metrics;
- can read logs;
- can read alerts;
- can rollback;
- requires credentials;
- supports public repositories;
- supports private repositories;
- has free-tier limitations.

Vercel and other hosting providers should be treated as separate integrations. InfraWatch must not assume that a hosted provider:

- behaves like Kubernetes;
- exposes Prometheus metrics;
- exposes Loki-compatible logs;
- supports Alertmanager-style alerts;
- supports rollback;
- exposes telemetry on a free tier;
- allows automated deployments without provider-specific setup.

Provider support should remain honest: unavailable metrics, logs, alerts, health checks, and rollback must be shown as unavailable rather than simulated.

## 12. Security boundaries

### 12.1 Authentication and authorization

Current InfraWatch has no user authentication or authorization model.

Before private repositories, file edits, Git writes, remote deployments, provider integrations, or shared usage are supported, vNext needs:

- local user/session model or explicit single-user mode;
- credential ownership model;
- permission checks;
- audit events;
- confirmation for consequential actions.

### 12.2 Credentials and secrets

Requirements:

- never store raw tokens in frontend state;
- never print tokens in logs or reports;
- store credentials only in an approved local secret store or explicitly documented secure local configuration;
- separate application secrets from InfraWatch system credentials;
- redact secret-like fields;
- support credential deletion;
- record credential changes as audit events without storing values.

### 12.3 Repository permissions

Read-only repository analysis should require no write permission.

File edits, branch creation, commits, and pull requests require:

- authentication;
- explicit repository permission checks;
- branch target confirmation;
- diff review;
- explicit approval;
- audit event recording.

### 12.4 Deployment and rollback permissions

Deployment and rollback require:

- target capability check;
- target confirmation;
- resource impact summary;
- credential or RBAC validation;
- rollback availability check;
- explicit approval for consequential changes.

Rollback must not be presented as available when the target does not support it.

### 12.5 Safe repository inspection and builds

Repository inspection must not execute code.

Before any build workflow exists, InfraWatch needs:

- isolated build environment;
- timeout;
- resource limits;
- network policy;
- redacted logs;
- artifact cleanup;
- explicit approval.

## 13. Dashboard design direction

The vNext dashboard should use the current dark InfraWatch visual direction and the owner-provided Headlamp-style reference as inspiration. This is a design direction, not evidence that the planned features already exist.

The same dashboard should support Local Mode and GitHub Repository Mode. Panels may be unavailable depending on mode, target, and integration capability.

Major dashboard sections:

### 13.1 Project summary

Show:

- project name;
- mode;
- repository identity, when available;
- current target;
- deployed URL, when available;
- latest deployment status;
- unresolved issues.

### 13.2 Repository

Show:

- repository URL;
- branch;
- commit SHA;
- detected stack;
- readiness result;
- file browser;
- proposed changes;
- diff review;
- pull request status, when supported.

### 13.3 Resources / workloads

Show:

- local Docker or Kubernetes resources where supported;
- service status;
- pods or provider equivalent;
- readiness;
- restart count where available;
- unsupported/unavailable states.

### 13.4 Deployments

Show:

- deployment timeline;
- build status;
- deployment status;
- deployed URL;
- target;
- environment;
- rollback capability and status.

### 13.5 Observability

Show:

- health checks;
- metrics;
- resource usage;
- source labels;
- query windows;
- unavailable telemetry.

### 13.6 Logs and alerts

Show:

- logs with source label;
- filters supported by the source;
- alerts with source label;
- provider-specific limitations;
- empty and unavailable states.

### 13.7 Reports

Show:

- deployment report;
- before/after comparison;
- health evidence;
- metric evidence;
- log references;
- alert references;
- incident history;
- unavailable data list.

### 13.8 Integrations and settings

Show:

- local tool readiness;
- GitHub connection status;
- provider adapter status;
- observability setup status;
- credential references without values;
- retention settings;
- cleanup settings.

## 14. Relationship to established tools

InfraWatch should complement established tools. It should not claim to replace:

- Kubernetes;
- kubectl;
- Headlamp;
- Helm;
- Skaffold;
- DevSpace;
- Grafana;
- Prometheus;
- Loki;
- Alertmanager;
- Terraform;
- Kustomize;
- Docker.

InfraWatch should integrate with these tools where useful and provide a focused learning and workflow layer around supported deployments and observability evidence.

Competitive analysis and formal external project application strategy are intentionally deferred.

## 15. Mapping to existing code

The vNext plan should reuse:

- current dashboard shell and visual language from `frontend/src/App.tsx` and `frontend/src/styles.css`;
- API client patterns from `frontend/src/api.ts`;
- browser Demo Mode pattern from `frontend/src/demoStore.ts`;
- FastAPI route structure from `backend/app/api.py`;
- deployment schemas from `backend/app/schemas.py`;
- repository abstraction from `backend/app/repository.py`;
- deployment service boundary from `backend/app/services/deployments.py`;
- observability clients from `backend/app/services/observability.py`;
- CLI orchestration style from `infrawatch/start.py`, `infrawatch/stop.py`, and `infrawatch/checks.py`;
- packaged runtime resources from `infrawatch/runtime_resources.py`;
- Terraform, Kustomize, and Helm ownership boundaries from `terraform/`, `k8s/`, `logging/`, and `monitoring/`.

New work required:

- explicit project model;
- repository analysis model;
- deployment target model;
- deployment report model;
- audit-event expansion;
- web navigation beyond the current single dashboard;
- GitHub repository browsing and analysis;
- security model for credentials and write operations;
- provider adapter model.

## 16. Future delivery roadmap

This section describes future product direction. It is not a statement that every capability below is implemented today.

### Product contract, data model, and API boundaries

Deliver:

- finalized Local Mode and GitHub Repository Mode contracts;
- shared project/deployment/report/audit model;
- capability matrix model;
- migration strategy;
- retention policy.

### Dashboard foundation and persistent history

Deliver:

- project list/detail views;
- deployment history;
- report detail view;
- integration status views;
- PostgreSQL-backed history.

### Read-only GitHub repository analysis

Deliver:

- public repository URL validation;
- bounded file inspection;
- stack detection;
- readiness report;
- unsupported/missing prerequisite output.

### Safe Local Mode workload onboarding

Deliver:

- container-image-first deployment flow;
- target confirmation;
- namespace/resource boundaries;
- cleanup behavior;
- deployment report generation.

### Repository Mode review workflow

Deliver:

- repository browser;
- proposed file-edit model;
- diff review;
- branch/commit/pull request design behind explicit authorization and approval.

Implementation may remain read-only until security prerequisites are complete.

### Deployment-observability correlation

Deliver:

- metric windows;
- log references;
- alert references;
- health checks;
- before/after deployment comparison;
- incident observations.

### Provider-specific hosting integration

Deliver:

- one adapter at a time;
- explicit capability matrix;
- credential handling;
- deployment status sync;
- telemetry only where available;
- rollback only where supported.

### Optional automation and rollback

Deliver only where safe:

- scheduled checks;
- approved automation;
- target-specific rollback workflows;
- recovery reports.

## 17. Important unresolved decisions

1. Should vNext remain single-user local only, or introduce user accounts/sessions?
2. What local secret store should be used for GitHub and provider credentials?
3. Should container-image-first remain the first deployment workflow?
4. When should Dockerfile-based builds be introduced?
5. What limits should apply to repository size, file count, and scan duration?
6. Which GitHub permissions are acceptable for repository browsing, branches, commits, and pull requests?
7. Which provider adapter should be evaluated first?
8. How much raw log data should InfraWatch persist versus reference?
9. How should report export work without leaking sensitive data?
10. Which operating systems and local Kubernetes runtimes should be officially supported?

## 18. Non-goals for the first vNext increment

The first vNext increment should not attempt:

- arbitrary repository builds;
- private repository access;
- unapproved file edits;
- unapproved Git writes;
- unapproved deployments;
- multi-tenant hosted service behavior;
- universal hosting support;
- production cluster management;
- provider-agnostic rollback claims;
- silent fallback from live telemetry to fake telemetry;
- storing or displaying secrets.

## 19. Summary

InfraWatch already has a local-first foundation: a React dashboard, FastAPI backend, Python CLI, Docker Compose stack, Minikube-oriented Kubernetes resources, Terraform foundation, Kustomize app resources, Helm-based Loki/Alloy path, PostgreSQL/JSON persistence, Redis metrics cache, Prometheus metrics, Loki logs, and Alertmanager alerts.

vNext should evolve that foundation into a full web app plus CLI with two explicit workflows: Local Mode and GitHub Repository Mode. Both workflows should use shared project, deployment, report, and audit models; preserve honest telemetry source labeling; require approval for consequential actions; and integrate with established tools rather than claiming to replace them.
