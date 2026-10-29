// Thin API wrapper around the ClueStack backend REST endpoints.
import type {
  AuditLogEntry,
  AlertsResponse,
  ContainerBuildEligibilityPayload,
  ContainerBuildEligibilityResult,
  ContainerBuildLogsResponse,
  ContainerBuildRecord,
  DeployPayload,
  DeploymentRecord,
  DeploymentReportRecord,
  LogsResponse,
  LocalRepositoryAnalysisPayload,
  ProjectCreate,
  ProjectRecord,
  RepositoryAnalysisResult,
  RepositoryReadinessResult,
  ServiceMetrics,
  SourceSnapshotRecord,
  SourceSnapshotVerification,
  WorkloadHealth,
} from "./types";
import {
  deleteDemoDeployment,
  createDemoProject,
  deployDemoService,
  getDemoDefaultLocalProject,
  getDemoAlerts,
  getDemoLogs,
  getDemoMetrics,
  getDemoWorkloadHealth,
  listDemoAuditLogs,
  listDemoDeployments,
  listDemoProjects,
  listDemoReports,
  resetDemoState,
  rollbackDemoDeployment,
} from "./demoStore";

const configuredApiUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const isLocalBrowser = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const demoModeRequested = import.meta.env.VITE_DEMO_MODE === "true";
const API_BASE_URL = demoModeRequested ? "" : configuredApiUrl || (isLocalBrowser ? "http://localhost:8000" : "");

export const isDemoMode = API_BASE_URL === "";
export const apiMode = isDemoMode ? "browser" : isLocalBrowser ? "local" : "configured";

type FallbackListener = (active: boolean) => void;

let fallbackActive = isDemoMode;
const fallbackListeners = new Set<FallbackListener>();

class ApiResponseError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

function setFallbackActive(active: boolean) {
  if (fallbackActive === active) {
    return;
  }
  fallbackActive = active;
  fallbackListeners.forEach((listener) => listener(active));
}

export function subscribeToApiFallback(listener: FallbackListener) {
  fallbackListeners.add(listener);
  listener(fallbackActive);
  return () => {
    fallbackListeners.delete(listener);
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = typeof body.detail === "string"
      ? body.detail
      : Array.isArray(body.detail)
        ? body.detail
            .map((item: unknown) => (
              typeof item === "object" && item !== null && "msg" in item && typeof item.msg === "string"
                ? item.msg
                : ""
            ))
            .filter(Boolean)
            .join("; ")
        : response.statusText;
    throw new ApiResponseError(detail || response.statusText, response.status);
  }

  return response.json() as Promise<T>;
}

async function withDemoFallback<T>(apiCall: () => Promise<T>, demoCall: () => T): Promise<T> {
  if (isDemoMode) {
    return demoCall();
  }

  try {
    const result = await apiCall();
    setFallbackActive(false);
    return result;
  } catch (error) {
    throw error;
  }
}

export function listDeployments(): Promise<DeploymentRecord[]> {
  return withDemoFallback(
    () => request<DeploymentRecord[]>("/deployments"),
    () => listDemoDeployments(),
  );
}

export function listProjects(): Promise<ProjectRecord[]> {
  return withDemoFallback(
    () => request<ProjectRecord[]>("/projects"),
    () => listDemoProjects(),
  );
}

export function createProject(payload: ProjectCreate): Promise<ProjectRecord> {
  return withDemoFallback(
    () => request<ProjectRecord>("/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
    () => createDemoProject(payload),
  );
}

export function getDefaultLocalProject(): Promise<ProjectRecord> {
  return withDemoFallback(
    () => request<ProjectRecord>("/projects/default-local"),
    () => getDemoDefaultLocalProject(),
  );
}

export function listProjectDeployments(projectId: string): Promise<DeploymentRecord[]> {
  return withDemoFallback(
    () => request<DeploymentRecord[]>(`/projects/${projectId}/deployments`),
    () => listDemoDeployments(projectId),
  );
}

export function listProjectReports(projectId: string): Promise<DeploymentReportRecord[]> {
  return withDemoFallback(
    () => request<DeploymentReportRecord[]>(`/projects/${projectId}/reports`),
    () => listDemoReports(projectId),
  );
}

export function analyzeRepository(projectId: string, branch?: string): Promise<RepositoryAnalysisResult> {
  if (isDemoMode) {
    return Promise.reject(new Error("Public GitHub analysis is unavailable in browser Demo Mode."));
  }
  return request<RepositoryAnalysisResult>(`/projects/${projectId}/repository-analysis`, {
    method: "POST",
    body: JSON.stringify({ branch: branch?.trim() || null }),
  });
}

export function analyzeLocalRepository(
  projectId: string,
  payload: LocalRepositoryAnalysisPayload,
): Promise<RepositoryAnalysisResult> {
  if (isDemoMode) {
    return Promise.reject(new Error("Local repository analysis is unavailable in browser Demo Mode."));
  }
  return request<RepositoryAnalysisResult>(`/projects/${projectId}/local-repository-analysis`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function assessRepositoryReadiness(
  projectId: string,
  analysisReportId?: string,
): Promise<RepositoryReadinessResult> {
  if (isDemoMode) {
    return Promise.reject(new Error("Repository readiness assessment is unavailable in browser Demo Mode."));
  }
  return request<RepositoryReadinessResult>(`/projects/${projectId}/repository-readiness`, {
    method: "POST",
    body: JSON.stringify({ analysis_report_id: analysisReportId || null }),
  });
}

export function listSourceSnapshots(projectId: string): Promise<SourceSnapshotRecord[]> {
  if (isDemoMode) {
    return Promise.reject(new Error("Source snapshots are unavailable in browser Demo Mode."));
  }
  return request<SourceSnapshotRecord[]>(`/projects/${projectId}/source-snapshots`);
}

export function createSourceSnapshot(
  projectId: string,
  payload: {
    reference?: string | null;
    analysis_report_id?: string | null;
    source_label?: string | null;
    submission_complete?: boolean;
    files?: LocalRepositoryAnalysisPayload["files"];
  },
): Promise<SourceSnapshotRecord> {
  if (isDemoMode) {
    return Promise.reject(new Error("Source snapshots are unavailable in browser Demo Mode."));
  }
  return request<SourceSnapshotRecord>(`/projects/${projectId}/source-snapshots`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function verifySourceSnapshot(projectId: string, snapshotId: string): Promise<SourceSnapshotVerification> {
  if (isDemoMode) {
    return Promise.reject(new Error("Source snapshot verification is unavailable in browser Demo Mode."));
  }
  return request<SourceSnapshotVerification>(`/projects/${projectId}/source-snapshots/${snapshotId}/verify`, {
    method: "POST",
  });
}

export function deleteSourceSnapshot(projectId: string, snapshotId: string): Promise<SourceSnapshotRecord> {
  if (isDemoMode) {
    return Promise.reject(new Error("Source snapshot deletion is unavailable in browser Demo Mode."));
  }
  return request<SourceSnapshotRecord>(`/projects/${projectId}/source-snapshots/${snapshotId}`, {
    method: "DELETE",
  });
}

export function validateContainerBuild(
  projectId: string,
  payload: ContainerBuildEligibilityPayload,
): Promise<ContainerBuildEligibilityResult> {
  if (isDemoMode) {
    return Promise.reject(new Error("Container builds are unavailable in browser Demo Mode."));
  }
  return request<ContainerBuildEligibilityResult>(`/projects/${projectId}/container-build-eligibility`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function createContainerBuild(
  projectId: string,
  payload: ContainerBuildEligibilityPayload & { approved: boolean },
): Promise<ContainerBuildRecord> {
  if (isDemoMode) {
    return Promise.reject(new Error("Container builds are unavailable in browser Demo Mode."));
  }
  return request<ContainerBuildRecord>(`/projects/${projectId}/container-builds`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getContainerBuildLogs(projectId: string, buildId: string): Promise<ContainerBuildLogsResponse> {
  if (isDemoMode) {
    return Promise.reject(new Error("Container build logs are unavailable in browser Demo Mode."));
  }
  return request<ContainerBuildLogsResponse>(`/projects/${projectId}/container-builds/${buildId}/logs`);
}

export function deployService(payload: DeployPayload): Promise<{ deployment: DeploymentRecord }> {
  return withDemoFallback(
    () => request<{ deployment: DeploymentRecord }>("/deploy", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
    () => deployDemoService(payload),
  );
}

export function deleteDeployment(name: string): Promise<{ status: string; name: string }> {
  return withDemoFallback(
    () => request<{ status: string; name: string }>(`/deployment/${name}`, { method: "DELETE" }),
    () => deleteDemoDeployment(name),
  );
}

export function rollbackDeployment(name: string): Promise<DeploymentRecord> {
  return withDemoFallback(
    () => request<DeploymentRecord>(`/deployment/${name}/rollback`, { method: "POST" }),
    () => rollbackDemoDeployment(name),
  );
}

export function getMetrics(service: string): Promise<ServiceMetrics> {
  return withDemoFallback(
    () => request<ServiceMetrics>(`/metrics/${service}`),
    () => getDemoMetrics(service),
  );
}

export function getWorkloadHealth(service: string): Promise<WorkloadHealth> {
  return withDemoFallback(
    () => request<WorkloadHealth>(`/workloads/${service}`),
    () => getDemoWorkloadHealth(service),
  );
}

export function getLogs(
  service: string,
  options: { search?: string; pod?: string; container?: string; minutes?: number; limit?: number } = {},
): Promise<LogsResponse> {
  const params = new URLSearchParams();
  if (options.search?.trim()) {
    params.set("search", options.search.trim());
  }
  if (options.pod) {
    params.set("pod", options.pod);
  }
  if (options.container) {
    params.set("container", options.container);
  }
  if (options.minutes) {
    params.set("minutes", String(options.minutes));
  }
  if (options.limit) {
    params.set("limit", String(options.limit));
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return withDemoFallback(
    () => request<LogsResponse>(`/logs/${service}${suffix}`),
    () => getDemoLogs(service, options.search),
  );
}

export function getAlerts(): Promise<AlertsResponse> {
  return withDemoFallback(
    () => request<AlertsResponse>("/alerts"),
    () => getDemoAlerts(),
  );
}

export function listAuditLogs(limit = 50): Promise<AuditLogEntry[]> {
  return withDemoFallback(
    () => request<AuditLogEntry[]>(`/audit-logs?limit=${limit}`),
    () => listDemoAuditLogs(limit),
  );
}

export function resetSandbox() {
  resetDemoState();
}
