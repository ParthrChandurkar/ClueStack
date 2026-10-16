# ClueStack Roadmap

ClueStack is an early local-first Kubernetes deployment and observability project. This roadmap describes the intended direction of the project without promising specific dates, releases, or delivery timelines.

## Current status

ClueStack v0.3.0 is a Developer Preview.

The current project focuses on local Kubernetes deployment and observability for lightweight local application testing. It includes:

- an installable Python CLI with `doctor`, `start`, `status`, and `stop`;
- Docker Compose and Minikube-oriented local workflows;
- Terraform-managed Kubernetes foundation resources;
- Kustomize-managed application resources;
- a React dashboard backed by a FastAPI API;
- PostgreSQL persistence and Redis-backed short-TTL metrics caching;
- Prometheus-backed metrics, Loki-backed logs through Grafana Alloy labels, and Alertmanager-backed alert visibility;
- packaged runtime resources for local CLI usage outside the repository checkout;
- demo/mock behavior that is intended for local exploration and must remain clearly labeled.

These are current capabilities. The sections below describe future direction and should not be read as completed functionality.

## Near term

Near-term work should prioritize documentation, reliability, and repeatability:

- improve first-time setup and troubleshooting documentation;
- document the architecture, supported environments, and current limitations more clearly;
- improve error handling and diagnostic clarity where real issues justify it;
- improve repeatable validation and test coverage for CLI, backend, frontend, Docker Compose, Kubernetes manifests, and packaging;
- document how demo/mock data differs from live Prometheus, Loki, and Alertmanager telemetry;
- keep local shutdown and cleanup behavior safe, explicit, and easy to understand.

## Next

The next product direction to explore is a limited, explicit user-workload workflow.

Possible scope:

- support deploying a user-provided container image or Kubernetes manifests into the local ClueStack workflow;
- define safe input validation before applying resources;
- preserve clear namespace boundaries and ownership rules;
- provide predictable cleanup behavior;
- report rollout, health, metrics, logs, and alert failures clearly.

This does not imply arbitrary repository builds, automatic GitHub onboarding, or a hosted build system. Those capabilities are not part of the current product and should not be promised without separate design and validation.

## Later / exploratory

Later work should stay grounded in tested local use cases and user feedback:

- explore broader local Kubernetes compatibility only after testing beyond the current Docker Desktop and Minikube-oriented workflow;
- evaluate integrations with existing developer tools instead of trying to replace them;
- improve dashboard workflows where they help users understand local deployments faster;
- consider additional observability views only when the data source, fallback behavior, and user value are clear;
- add features only when they fit the project's maintainable local-first scope.

## Non-goals

ClueStack is not currently intended to be:

- a hosted SaaS platform;
- a multi-tenant control plane;
- a production cluster management system;
- a replacement for Kubernetes, Prometheus, Grafana, Loki, Alertmanager, Headlamp, K9s, Tilt, Skaffold, or similar tools;
- a promise of support for every Kubernetes distribution or operating system;
- an automatic build-and-deploy system for arbitrary source repositories.

## How priorities change

This roadmap is directional. It is not a fixed release schedule.

Priorities may change based on maintainability, issue reports, contributor feedback, validation results, and the project's local-first scope. Users and contributors can share feedback through the repository's existing issue templates, pull request process, and security reporting guidance.
