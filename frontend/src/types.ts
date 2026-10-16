// Shared TypeScript types for ClueStack API responses.
export type DeploymentStatus =
  | "Running"
  | "Simulated"
  | "Failed"
  | "Pending"
  | "Deploying"
  | "Deleting"
  | "Deleted";
export type ProjectMode = "local" | "github_repository";
export type ProjectSourceType = "manual" | "github_public_readonly";
export type DeploymentReportOperation = "deploy" | "delete" | "rollback" | "status_check" | "repository_analysis";
export type DeploymentReportStatus = "success" | "failure" | "partial" | "skipped";

export interface ProjectCreate {
  name: string;
  slug?: string;
  mode?: ProjectMode;
  source_type?: ProjectSourceType;
  repository_url?: string | null;
  default_namespace?: string;
  description?: string | null;
}

export interface ProjectRecord {
  id: string;
  name: string;
  slug: string;
  mode: ProjectMode;
  source_type: ProjectSourceType;
  repository_url: string | null;
  default_namespace: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface DeploymentRecord {
  id: string | null;
  name: string;
  project_id: string | null;
  image: string;
  namespace: string;
  replicas: number;
  port: number;
  status: DeploymentStatus;
  url: string | null;
  commit_sha: string | null;
  ready_replicas: number | null;
  available_replicas: number | null;
  observed_generation: number | null;
  last_failure: string | null;
  message: string;
  created_at: string;
  updated_at: string;
}

export interface DeployPayload {
  name: string;
  project_id?: string;
  image: string;
  namespace?: string;
  replicas: number;
  port: number;
  environment: Record<string, string>;
  labels?: Record<string, string>;
  commit_sha?: string;
}

export interface DeploymentReportError {
  code: string | null;
  message: string;
}

export interface DeploymentReportRecord {
  id: string;
  project_id: string | null;
  deployment_id: string | null;
  operation: DeploymentReportOperation;
  status: DeploymentReportStatus;
  summary: string;
  target: string;
  commit_sha: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  evidence: Record<string, unknown>;
  errors: DeploymentReportError[];
  created_at: string;
}

export interface MetricPoint {
  timestamp: number;
  value: number;
}

export interface ServiceMetrics {
  service: string;
  cpu_cores: MetricPoint[];
  memory_megabytes: MetricPoint[];
  request_rate: MetricPoint[];
  error_rate: MetricPoint[];
  source: string;
}

export interface PodHealth {
  name: string;
  phase: string;
  ready: boolean;
  restart_count: number;
  reason?: string;
  message?: string;
}

export interface WorkloadHealth {
  service: string;
  namespace: string;
  desired_replicas: number;
  updated_replicas: number | null;
  ready_replicas: number | null;
  available_replicas: number | null;
  unavailable_replicas: number | null;
  observed_generation?: number | null;
  pods: PodHealth[];
  source: string;
}

export interface LogLine {
  timestamp: string;
  line: string;
  namespace?: string;
  pod?: string;
  container?: string;
  labels?: Record<string, string>;
}

export interface LogsResponse {
  service: string;
  lines: LogLine[];
  source: string;
}

export interface AlertSummary {
  fingerprint: string;
  status: string;
  alertname: string;
  severity?: string;
  instance?: string;
  service?: string;
  namespace?: string;
  pod?: string;
  starts_at?: string;
  summary?: string;
  description?: string;
}

export interface AlertsResponse {
  source: string;
  alerts: AlertSummary[];
}

export interface AuditLogEntry {
  id: string;
  action: string;
  service?: string;
  actor: string;
  status: string;
  message: string;
  metadata: Record<string, unknown>;
  created_at: string;
}
