import type {
  AuditLogEntry,
  AlertsResponse,
  DeployPayload,
  DeploymentRecord,
  DeploymentReportRecord,
  LogsResponse,
  ProjectCreate,
  ProjectRecord,
  ServiceMetrics,
  WorkloadHealth,
} from "./types";

interface DemoState {
  projects: ProjectRecord[];
  deployments: DeploymentRecord[];
  reports: DeploymentReportRecord[];
  auditLogs: AuditLogEntry[];
}

const STORAGE_KEY = "infrawatch-public-sandbox-v2";
const LEGACY_STORAGE_KEY = "infrawatch-public-sandbox-v1";
const DEFAULT_PROJECT_ID = "00000000-0000-4000-8000-000000000001";

function minutesAgo(minutes: number) {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

function defaultLocalProject(): ProjectRecord {
  const createdAt = minutesAgo(1_440);
  return {
    id: DEFAULT_PROJECT_ID,
    name: "Default Local",
    slug: "default-local",
    mode: "local",
    source_type: "manual",
    repository_url: null,
    default_namespace: "infrawatch",
    description: "Browser-local project for the ClueStack demo workflow.",
    created_at: createdAt,
    updated_at: createdAt,
    archived_at: null,
  };
}

function createInitialState(): DemoState {
  const project = defaultLocalProject();
  const deployments: DeploymentRecord[] = [
    {
      id: "00000000-0000-4000-8000-000000000101",
      name: "checkout-api",
      project_id: project.id,
      image: "ghcr.io/infrawatch/checkout-api:2.4.1",
      namespace: "infrawatch",
      replicas: 3,
      port: 8080,
      status: "Simulated",
      url: null,
      commit_sha: "7b2f9ad",
      message: "Browser simulation recorded; no Kubernetes workload was started.",
      ready_replicas: null,
      available_replicas: null,
      observed_generation: null,
      last_failure: null,
      created_at: minutesAgo(180),
      updated_at: minutesAgo(8),
    },
    {
      id: "00000000-0000-4000-8000-000000000102",
      name: "catalog-api",
      project_id: project.id,
      image: "ghcr.io/infrawatch/catalog-api:1.9.0",
      namespace: "infrawatch",
      replicas: 2,
      port: 8080,
      status: "Simulated",
      url: null,
      commit_sha: "95ce112",
      message: "Browser simulation recorded; no Kubernetes workload was started.",
      ready_replicas: null,
      available_replicas: null,
      observed_generation: null,
      last_failure: null,
      created_at: minutesAgo(720),
      updated_at: minutesAgo(34),
    },
    {
      id: "00000000-0000-4000-8000-000000000103",
      name: "worker-queue",
      project_id: project.id,
      image: "ghcr.io/infrawatch/worker-queue:0.8.3",
      namespace: "infrawatch",
      replicas: 1,
      port: 9091,
      status: "Simulated",
      url: null,
      commit_sha: "c41a903",
      message: "Browser simulation recorded; no Kubernetes workload was started.",
      ready_replicas: null,
      available_replicas: null,
      observed_generation: null,
      last_failure: null,
      created_at: minutesAgo(24),
      updated_at: minutesAgo(3),
    },
  ];

  return {
    projects: [project],
    deployments,
    reports: deployments.map((deployment, index) => ({
      id: `00000000-0000-4000-8000-00000000020${index + 1}`,
      project_id: project.id,
      deployment_id: deployment.id,
      operation: "deploy",
      status: "success",
      summary: "Browser simulation recorded; no Kubernetes workload was started.",
      target: "browser_sandbox",
      commit_sha: deployment.commit_sha ?? null,
      started_at: deployment.created_at,
      completed_at: deployment.updated_at,
      duration_ms: 1_250 + index * 320,
      evidence: {
        deployment_name: deployment.name,
        execution_mode: "browser-sandbox",
        replicas: deployment.replicas,
        status: "Simulated",
        ready_replicas: null,
        available_replicas: null,
      },
      errors: [],
      created_at: deployment.updated_at,
    })),
    auditLogs: [
      demoAudit("deployment.simulated", "checkout-api", "success", "Browser simulation recorded; no workload was started.", 8),
      demoAudit("deployment.simulated", "catalog-api", "success", "Browser simulation recorded; no workload was started.", 34),
      demoAudit("deployment.simulated", "worker-queue", "success", "Browser simulation recorded; no workload was started.", 3),
    ],
  };
}

function demoAudit(action: string, service: string, status: string, message: string, minutes: number): AuditLogEntry {
  return {
    id: `${service}-${action}-${minutes}`,
    action,
    service,
    actor: "public-demo",
    status,
    message,
    metadata: { mode: "browser-sandbox" },
    created_at: minutesAgo(minutes),
  };
}

function normalizeState(state: Partial<DemoState>): DemoState {
  const defaultProject = defaultLocalProject();
  const projects = Array.isArray(state.projects) && state.projects.length
    ? state.projects
    : [defaultProject];
  const fallbackProjectId = projects.find((project) => project.slug === "default-local")?.id
    ?? projects[0].id;
  const deployments = Array.isArray(state.deployments) ? state.deployments : [];
  const reports = Array.isArray(state.reports) ? state.reports : [];

  return {
    projects,
    deployments: deployments.map((deployment) => {
      const wasPresentedAsLive = deployment.status === "Running" || deployment.status === "Pending";
      return {
        ...deployment,
        id: deployment.id ?? randomId(),
        project_id: deployment.project_id ?? fallbackProjectId,
        status: wasPresentedAsLive ? "Simulated" : deployment.status,
        url: null,
        ready_replicas: null,
        available_replicas: null,
        observed_generation: null,
        last_failure: deployment.last_failure ?? null,
        message: wasPresentedAsLive
          ? "Browser simulation recorded; no Kubernetes workload was started."
          : deployment.message,
      };
    }),
    reports: reports.map((report) => report.target === "browser_sandbox" && report.operation === "deploy"
      ? {
          ...report,
          summary: "Browser simulation recorded; no Kubernetes workload was started.",
          evidence: {
            ...report.evidence,
            status: "Simulated",
            ready_replicas: null,
            available_replicas: null,
          },
        }
      : report),
    auditLogs: Array.isArray(state.auditLogs) ? state.auditLogs : [],
  };
}

function parseStoredState(value: string | null): Partial<DemoState> | null {
  if (!value) {
    return null;
  }
  try {
    const parsed = JSON.parse(value);
    if (
      parsed
      && typeof parsed === "object"
      && Array.isArray(parsed.deployments)
      && Array.isArray(parsed.auditLogs)
    ) {
      return parsed as Partial<DemoState>;
    }
    return null;
  } catch {
    return null;
  }
}

function readState(): DemoState {
  try {
    const current = parseStoredState(window.localStorage.getItem(STORAGE_KEY));
    if (current) {
      return normalizeState(current);
    }

    const legacy = parseStoredState(window.localStorage.getItem(LEGACY_STORAGE_KEY));
    if (legacy) {
      const migrated = normalizeState(legacy);
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(migrated));
      } catch {
        return migrated;
      }
      return migrated;
    }

    return createInitialState();
  } catch {
    return createInitialState();
  }
}

function writeState(state: DemoState) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function randomId() {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  const segment = () => Math.floor(Math.random() * 0xffff).toString(16).padStart(4, "0");
  return `${segment()}${segment()}-${segment()}-4${segment().slice(1)}-8${segment().slice(1)}-${segment()}${segment()}${segment()}`;
}

function slugify(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);
}

export function listDemoProjects(): ProjectRecord[] {
  return [...readState().projects].sort(
    (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
  );
}

export function getDemoDefaultLocalProject(): ProjectRecord {
  const state = readState();
  const existing = state.projects.find((project) => project.slug === "default-local");
  if (existing) {
    return existing;
  }
  const project = defaultLocalProject();
  state.projects.unshift(project);
  writeState(state);
  return project;
}

export function createDemoProject(payload: ProjectCreate): ProjectRecord {
  if ((payload.mode ?? "local") !== "local" || (payload.source_type ?? "manual") !== "manual") {
    throw new Error("Browser Demo Mode currently supports local projects only.");
  }
  const state = readState();
  const slug = payload.slug?.trim() || slugify(payload.name);
  if (!/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(slug) || slug.length < 2 || slug.length > 63) {
    throw new Error("Project slug must use 2-63 lowercase letters, numbers, or hyphens.");
  }
  if (state.projects.some((project) => project.slug === slug)) {
    throw new Error("Project slug already exists");
  }
  const now = new Date().toISOString();
  const project: ProjectRecord = {
    id: randomId(),
    name: payload.name.trim().replace(/\s+/g, " "),
    slug,
    mode: "local",
    source_type: "manual",
    repository_url: null,
    default_namespace: payload.default_namespace ?? "infrawatch",
    description: payload.description ?? null,
    created_at: now,
    updated_at: now,
    archived_at: null,
  };
  state.projects.push(project);
  writeState(state);
  return project;
}

export function listDemoDeployments(projectId?: string): DeploymentRecord[] {
  const deployments = readState().deployments.filter(
    (deployment) => projectId === undefined || deployment.project_id === projectId,
  );
  return deployments.sort(
    (left, right) => new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
  );
}

export function listDemoReports(projectId: string): DeploymentReportRecord[] {
  return readState().reports
    .filter((report) => report.project_id === projectId)
    .sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime());
}

export function deployDemoService(payload: DeployPayload): { deployment: DeploymentRecord } {
  const state = readState();
  const projectId = payload.project_id ?? DEFAULT_PROJECT_ID;
  if (!state.projects.some((project) => project.id === projectId && project.mode === "local")) {
    throw new Error("Local deployment project was not found.");
  }
  const now = new Date().toISOString();
  const existing = state.deployments.find((deployment) => deployment.name === payload.name);
  const deployment: DeploymentRecord = {
    id: existing?.id ?? randomId(),
    name: payload.name,
    project_id: projectId,
    image: payload.image,
    namespace: payload.namespace ?? "infrawatch",
    replicas: payload.replicas,
    port: payload.port,
    status: "Simulated",
    url: null,
    commit_sha: payload.commit_sha ?? null,
    message: "Browser simulation recorded; no Kubernetes workload was started.",
    ready_replicas: null,
    available_replicas: null,
    observed_generation: null,
    last_failure: null,
    created_at: existing?.created_at ?? now,
    updated_at: now,
  };

  state.deployments = [deployment, ...state.deployments.filter((item) => item.name !== payload.name)];
  state.reports.unshift({
    id: randomId(),
    project_id: projectId,
    deployment_id: deployment.id,
    operation: "deploy",
    status: "success",
    summary: "Browser simulation recorded; no Kubernetes workload was started.",
    target: "browser_sandbox",
    commit_sha: deployment.commit_sha ?? null,
    started_at: now,
    completed_at: now,
    duration_ms: 0,
    evidence: {
      deployment_name: deployment.name,
      namespace: deployment.namespace,
      replicas: deployment.replicas,
      execution_mode: "browser-sandbox",
      status: "Simulated",
      ready_replicas: null,
      available_replicas: null,
    },
    errors: [],
    created_at: now,
  });
  state.auditLogs.unshift({
    id: randomId(),
    action: "deployment.simulated",
    service: payload.name,
    actor: "visitor",
    status: "success",
    message: `${payload.image} was recorded with ${payload.replicas} requested replica${payload.replicas === 1 ? "" : "s"}; no workload was started.`,
    metadata: { port: payload.port, mode: "browser-sandbox", project_id: projectId },
    created_at: now,
  });
  writeState(state);
  return { deployment };
}

export function deleteDemoDeployment(name: string): { status: string; name: string } {
  const state = readState();
  state.deployments = state.deployments.filter((deployment) => deployment.name !== name);
  state.auditLogs.unshift({
    id: randomId(),
    action: "deployment.deleted",
    service: name,
    actor: "visitor",
    status: "success",
    message: "Deployment removed from your private browser sandbox.",
    metadata: { mode: "browser-sandbox" },
    created_at: new Date().toISOString(),
  });
  writeState(state);
  return { status: "deleted", name };
}

export function rollbackDemoDeployment(name: string): DeploymentRecord {
  const state = readState();
  const existing = state.deployments.find((deployment) => deployment.name === name);
  if (!existing) {
    throw new Error("Deployment not found");
  }
  const now = new Date().toISOString();
  const deployment: DeploymentRecord = {
    ...existing,
    status: "Simulated",
    message: "Rollback was simulated in the private browser sandbox. No Kubernetes command was executed.",
    updated_at: now,
  };
  state.deployments = [deployment, ...state.deployments.filter((item) => item.name !== name)];
  state.auditLogs.unshift({
    id: randomId(),
    action: "deployment.rollback_simulated",
    service: name,
    actor: "visitor",
    status: "success",
    message: "Rollback was simulated in the private browser sandbox.",
    metadata: { mode: "browser-sandbox", project_id: deployment.project_id },
    created_at: now,
  });
  writeState(state);
  return deployment;
}

export function getDemoMetrics(service: string): ServiceMetrics {
  const seed = service.split("").reduce((total, char) => total + char.charCodeAt(0), 0);
  const now = Math.floor(Date.now() / 1000);
  const points = Array.from({ length: 12 }, (_, index) => ({
    timestamp: now - (11 - index) * 60,
    wave: Math.sin((index + seed) / 2.2),
    drift: Math.cos((index + seed) / 3.5),
  }));
  return {
    service,
    cpu_cores: points.map((point) => ({ timestamp: point.timestamp, value: 0.24 + Math.abs(point.wave) * 0.19 })),
    memory_megabytes: points.map((point) => ({ timestamp: point.timestamp, value: 220 + Math.abs(point.drift) * 96 + (seed % 37) })),
    request_rate: points.map((point) => ({ timestamp: point.timestamp, value: 18 + Math.abs(point.wave) * 28 + (seed % 9) })),
    error_rate: points.map((point) => ({ timestamp: point.timestamp, value: Math.abs(point.drift) * 0.08 })),
    source: "browser sandbox",
  };
}

export function getDemoLogs(service: string, search = ""): LogsResponse {
  const lines = [
    { timestamp: minutesAgo(2), line: `[info] ${service} simulated health sample`, pod: `${service}-demo-1`, container: "app" },
    {
      timestamp: minutesAgo(5),
      line: "[info] simulated request sample for the configured port",
      pod: `${service}-demo-1`,
      container: "app",
    },
    {
      timestamp: minutesAgo(9),
      line: "[info] browser-only deployment sample recorded by ClueStack",
      pod: `${service}-demo-2`,
      container: "app",
    },
  ];
  const normalizedSearch = search.trim().toLowerCase();
  return {
    service,
    source: "browser sandbox",
    lines: normalizedSearch
      ? lines.filter((entry) => entry.line.toLowerCase().includes(normalizedSearch))
      : lines,
  };
}

export function getDemoAlerts(): AlertsResponse {
  const pendingDeployment = listDemoDeployments().find((deployment) =>
    ["Pending", "Deploying", "Failed"].includes(deployment.status),
  );
  return {
    source: "browser sandbox",
    alerts: pendingDeployment
      ? [
          {
            fingerprint: `browser-${pendingDeployment.name}-pending`,
            status: "firing",
            alertname: "DemoRolloutPending",
            severity: "warning",
            service: pendingDeployment.name,
            namespace: pendingDeployment.namespace,
            pod: `${pendingDeployment.name}-demo-1`,
            starts_at: pendingDeployment.updated_at,
            summary: "Demo rollout health signal",
            description: "This is simulated browser-sandbox alert data, not an Alertmanager alert.",
          },
        ]
      : [],
  };
}

export function getDemoWorkloadHealth(service: string): WorkloadHealth {
  const deployment = listDemoDeployments().find((item) => item.name === service);
  const desired = deployment?.replicas ?? 0;
  return {
    service,
    namespace: deployment?.namespace ?? "infrawatch",
    desired_replicas: desired,
    updated_replicas: null,
    ready_replicas: null,
    available_replicas: null,
    unavailable_replicas: null,
    observed_generation: null,
    pods: [],
    source: "browser simulation",
  };
}

export function listDemoAuditLogs(limit: number): AuditLogEntry[] {
  return readState().auditLogs.slice(0, limit);
}

export function resetDemoState() {
  writeState(createInitialState());
}
