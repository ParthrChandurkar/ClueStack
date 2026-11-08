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
export type DeploymentReportOperation =
  | "deploy"
  | "delete"
  | "rollback"
  | "status_check"
  | "repository_analysis"
  | "repository_readiness"
  | "container_build";
export type DeploymentReportStatus = "success" | "failure" | "partial" | "skipped";
export type RepositoryFindingStatus = "detected" | "missing" | "unknown" | "unsupported";
export type RepositoryRetrievalStatus = "complete" | "partial" | "failed";
export type RepositoryAnalysisSource = "github_public" | "local_upload";

export type SourceSnapshotStatus = "creating" | "available" | "failed" | "expired" | "deleted";

export interface SourceSnapshotIdentity {
  repository_url?: string | null;
  owner?: string | null;
  repository?: string | null;
  commit_sha?: string | null;
  resolved_reference?: string | null;
  submission_id?: string | null;
  submission_complete?: boolean | null;
}

export interface SourceSnapshotRecord {
  id: string;
  project_id: string;
  source: RepositoryAnalysisSource;
  source_identity: SourceSnapshotIdentity;
  analysis_report_id?: string | null;
  created_at: string;
  expires_at?: string | null;
  format_version: number;
  included_files: Array<{ path: string; size: number; sha256: string }>;
  exclusions: Array<{ reason: string; count: number }>;
  file_count: number;
  total_content_size: number;
  integrity_hash?: string | null;
  status: SourceSnapshotStatus;
  integrity_verified: boolean;
  storage_kind: string;
  read_only_requested: boolean;
  error?: string | null;
}

export interface SourceSnapshotVerification {
  snapshot_id: string;
  valid: boolean;
  status: SourceSnapshotStatus;
  integrity_hash?: string | null;
  verified_at: string;
}

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

export interface RepositoryAnalysisFinding {
  category: string;
  status: RepositoryFindingStatus;
  values: string[];
  evidence_paths: string[];
  detail: string;
}

export interface RepositoryAnalysisSignal {
  id: string;
  evidence_paths: string[];
  detail: string;
}

export interface RepositoryAnalysisResult {
  project_id: string;
  source: RepositoryAnalysisSource;
  source_label: string;
  repository_url: string | null;
  owner: string | null;
  repository: string | null;
  branch: string | null;
  commit_sha: string | null;
  retrieval_status: RepositoryRetrievalStatus;
  tree_truncated: boolean;
  tree_entries_seen: number;
  files_retrieved: number;
  content_bytes_retrieved: number;
  findings: RepositoryAnalysisFinding[];
  readiness_signals: RepositoryAnalysisSignal[];
  missing_prerequisites: string[];
  warnings: string[];
  errors: string[];
  report_id: string;
  analyzed_at: string;
}

export type RepositoryReadinessStatus = "ready" | "partially_ready" | "not_ready" | "unknown";
export type RepositoryReadinessSeverity = "info" | "warning" | "error";

export interface RepositoryReadinessFinding {
  id: string;
  category: string;
  severity: RepositoryReadinessSeverity;
  description: string;
  evidence_paths: string[];
  required: boolean;
}

export interface RepositoryReadinessDimension {
  id: "containerization" | "kubernetes" | "infrastructure";
  status: RepositoryReadinessStatus;
  explanation: string;
  findings: RepositoryReadinessFinding[];
  evidence_paths: string[];
  missing_prerequisites: string[];
  warnings: string[];
  unknown_conditions: string[];
}

export interface RepositoryReadinessResult {
  project_id: string;
  analysis_source: RepositoryAnalysisSource;
  analysis_report_id: string;
  analysis_commit_sha: string | null;
  overall_status: RepositoryReadinessStatus;
  dimensions: RepositoryReadinessDimension[];
  report_id: string;
  assessed_at: string;
}

export interface LocalRepositoryAnalysisFile {
  path: string;
  content: string;
}

export interface LocalRepositoryAnalysisPayload {
  source_label: string;
  submission_complete: boolean;
  files: LocalRepositoryAnalysisFile[];
}

export type ContainerBuildStatus =
  | "pending"
  | "validating"
  | "queued"
  | "building"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "rejected";
export type ContainerBuildFindingStatus = "passed" | "warning" | "blocker";

export interface ContainerBuildFinding {
  code: string;
  status: ContainerBuildFindingStatus;
  message: string;
  evidence_paths: string[];
}

export interface ContainerBuildEligibilityPayload {
  analysis_report_id: string;
  readiness_report_id: string;
  source_snapshot_id: string | null;
  dockerfile_path: string;
  context_path: string;
  image_name: string;
  image_tag: string;
}

export interface ContainerBuildEligibilityResult extends ContainerBuildEligibilityPayload {
  project_id: string;
  eligible: boolean;
  source: RepositoryAnalysisSource | null;
  findings: ContainerBuildFinding[];
  execution_available: boolean;
  worker_availability: "available" | "unavailable";
  network_policy: "disabled";
}

export interface ContainerBuildRecord {
  id: string;
  project_id: string;
  status: ContainerBuildStatus;
  source: RepositoryAnalysisSource | null;
  source_snapshot_id: string | null;
  analysis_report_id: string;
  readiness_report_id: string;
  dockerfile_path: string;
  context_path: string;
  image_name: string;
  image_tag: string;
  image_digest: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  exit_status: number | null;
  failure_reason: string | null;
  worker_job_id: string | null;
  result_validation: "passed" | "failed" | null;
  cleanup_status: "succeeded" | "failed" | null;
  report_id: string;
}

export interface ContainerBuildLogsResponse {
  build_id: string;
  status: ContainerBuildStatus;
  logs: string;
  truncated: boolean;
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

export interface KubernetesContextList {
  enabled: boolean;
  contexts: string[];
  current_context: string | null;
  message: string | null;
}

export interface KubernetesNodeCondition {
  type: string;
  status: string;
  reason: string | null;
  message: string | null;
  last_transition_time: string | null;
}

export interface KubernetesNodeOverview {
  name: string;
  roles: string[];
  kubernetes_version: string | null;
  ready: boolean;
  conditions: KubernetesNodeCondition[];
}

export interface KubernetesNamespacePodSummary {
  namespace: string;
  total: number;
  running: number;
  pending: number;
  succeeded: number;
  failed: number;
  unknown: number;
}

export interface KubernetesPodSummary {
  total: number;
  running: number;
  pending: number;
  succeeded: number;
  failed: number;
  unknown: number;
  namespaces: KubernetesNamespacePodSummary[];
}

export interface KubernetesResourceUsage {
  available: boolean;
  cpu_cores: number | null;
  memory_bytes: number | null;
  nodes_reporting: number;
  message: string | null;
}

export interface KubernetesClusterOverview {
  context: string;
  connection_status: "connected";
  server_version: string | null;
  api_server: string | null;
  cluster_identity: string | null;
  total_nodes: number;
  ready_nodes: number;
  not_ready_nodes: number;
  nodes: KubernetesNodeOverview[];
  pods: KubernetesPodSummary;
  resource_usage: KubernetesResourceUsage;
  refreshed_at: string;
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
