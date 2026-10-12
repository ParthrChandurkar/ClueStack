# InfraWatch Scope

This document explains what InfraWatch is intended to do today, what it does not do, and where its boundaries are as an early developer-preview project.

## Purpose

InfraWatch is a local-first Kubernetes deployment and observability tool for lightweight local application testing.

It is designed to help users run a small local stack, inspect deployment state, and understand workload health, metrics, logs, and alerts without manually wiring every component from scratch.

## Intended users

InfraWatch is currently aimed at:

- developers testing lightweight containerized services locally;
- students learning Kubernetes and observability workflows;
- early SRE learners practicing rollout, health, metrics, logs, and alert scenarios;
- contributors who want a small local-first project for experimenting with Kubernetes automation and dashboard behavior.

The project is not currently designed for platform teams running production multi-tenant infrastructure.

## Current capabilities

InfraWatch v0.3.0 Developer Preview currently includes:

- an installable Python CLI with `infrawatch doctor`, `infrawatch start`, `infrawatch status`, and `infrawatch stop`;
- Docker Compose and Minikube-oriented local workflows;
- Terraform-managed Kubernetes foundation resources;
- Kustomize-managed application resources;
- a FastAPI backend for deployment operations and observability APIs;
- a React dashboard for deployments, workload health, metrics, logs, alerts, and audit events;
- PostgreSQL-backed persistence and Redis-backed short-TTL caching for repeated Prometheus metrics queries;
- Prometheus-backed service metrics;
- Loki-backed Kubernetes logs through Grafana Alloy labels;
- Alertmanager-backed alert visibility;
- packaged runtime resources for local CLI use outside the repository checkout;
- demo/mock behavior for first-time local exploration.

## Data-source transparency

InfraWatch must keep demo data and live telemetry clearly separated.

Demo or mock data is useful for exploring the UI without a fully running Kubernetes observability stack. It should not be presented as real cluster telemetry.

Live observability data comes from configured local components such as Prometheus, Loki, Grafana Alloy, Alertmanager, Kubernetes APIs, PostgreSQL, and Redis. When live sources are unavailable, the UI and backend should make that state clear instead of silently implying that fake data is real.

## Operating model

InfraWatch integrates existing Kubernetes and observability components. It does not replace them.

The project uses tools and systems such as Docker, Docker Compose, Minikube, kubectl, Terraform, Kustomize, Prometheus, Grafana, Loki, Grafana Alloy, Alertmanager, PostgreSQL, and Redis. InfraWatch's role is to provide a local workflow and dashboard around those components for a focused lightweight testing use case.

## Limitations

InfraWatch is a developer-preview project.

Current limitations include:

- no user authentication;
- no multi-tenant authorization model;
- no hosted SaaS control plane;
- no automatic onboarding or building of arbitrary Git repositories;
- no production Ingress or TLS setup by default;
- no claim of production cluster management support;
- no claim of universal Kubernetes distribution or operating system compatibility;
- primary validation around local Docker Desktop and Minikube-oriented workflows.

Users should treat InfraWatch as a local development, testing, and learning tool rather than a production platform.

## Non-goals

InfraWatch does not currently aim to replace:

- Kubernetes;
- kubectl;
- Terraform;
- Kustomize;
- Helm;
- Prometheus;
- Grafana;
- Loki;
- Alertmanager;
- Grafana Alloy;
- Headlamp;
- K9s;
- Tilt;
- Skaffold;
- Garden;
- DevSpace.

InfraWatch also does not currently aim to provide:

- a hosted multi-user service;
- production-grade access control;
- a general-purpose Kubernetes dashboard;
- a full inner-loop application development system;
- automatic build-and-deploy workflows for arbitrary source repositories.

## Future direction

One possible future direction is a limited user-workload workflow where a user can provide a container image or Kubernetes manifests and inspect the resulting rollout, health, metrics, logs, and alerts through InfraWatch.

That capability is not implemented as a general product promise today. Before implementing it, the project should define safe input validation, namespace boundaries, cleanup behavior, ownership rules, and failure handling.

The project should prefer small, testable improvements over broad platform claims.

## Feedback and contributions

Feedback and contributions should use the repository's existing contribution paths:

- issue templates for bugs and feature requests;
- pull requests with clear test results and focused changes;
- the security reporting process documented in `SECURITY.md` for vulnerabilities.

Please do not share credentials, tokens, kubeconfig contents, private logs, or sensitive application data in public issues or pull requests.
