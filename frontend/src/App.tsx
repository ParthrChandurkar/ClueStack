// Main ClueStack dashboard experience.
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock,
  Cpu,
  ExternalLink,
  FileCode2,
  Gauge,
  GitBranch,
  HardDrive,
  Layers,
  RefreshCw,
  RotateCcw,
  Rocket,
  Server,
  ShieldCheck,
  Terminal,
  Trash2,
  Zap,
} from "lucide-react";
import { ChangeEvent, FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  apiMode,
  analyzeLocalRepository,
  analyzeRepository,
  assessRepositoryReadiness,
  createContainerBuild,
  createSourceSnapshot,
  createProject,
  deleteDeployment,
  deleteSourceSnapshot,
  deployService,
  getDefaultLocalProject,
  getContainerBuildLogs,
  getAlerts,
  getLogs,
  getMetrics,
  getKubernetesClusterOverview,
  getWorkloadHealth,
  isDemoMode,
  listAuditLogs,
  listProjectDeployments,
  listProjectReports,
  listProjects,
  listKubernetesContexts,
  listSourceSnapshots,
  resetSandbox,
  rollbackDeployment,
  subscribeToApiFallback,
  validateContainerBuild,
  verifySourceSnapshot,
} from "./api";
import type {
  AuditLogEntry,
  AlertSummary,
  AlertsResponse,
  ContainerBuildEligibilityPayload,
  ContainerBuildEligibilityResult,
  ContainerBuildRecord,
  DeploymentRecord,
  DeploymentReportRecord,
  DeploymentStatus,
  LogsResponse,
  LocalRepositoryAnalysisFile,
  KubernetesClusterOverview,
  KubernetesContextList,
  ProjectRecord,
  RepositoryAnalysisResult,
  RepositoryReadinessResult,
  ServiceMetrics,
  SourceSnapshotRecord,
  WorkloadHealth,
} from "./types";
import clueStackMark from "./assets/cluestack-mark.svg";
import {
  isCurrentRepositoryRequest,
  repositoryAnalysisViewState,
  repositoryReadinessViewState,
  repositoryRemoteAnalysisAllowed,
} from "./repositoryAnalysisState";
import {
  buildLocalProjectCreate,
  EMPTY_GITHUB_PROJECT_FORM,
  registerGitHubProject,
  validateGitHubProjectRegistration,
} from "./githubProjectRegistration";
import { summarizeLocalSelection, summarizeLocalSnapshotSelection } from "./localRepositorySelection";
import { snapshotBuildCandidate } from "./sourceSnapshotState";
import { canRefreshCluster, clusterOverviewViewState } from "./clusterOverviewState";

const STATUS_THEME: Record<DeploymentStatus, { color: string; label: string; icon: ReactNode }> = {
  Running: { color: "#3ddc97", label: "Healthy", icon: <CheckCircle2 size={14} /> },
  Simulated: { color: "#f5b942", label: "Manifest generated", icon: <FileCode2 size={14} /> },
  Pending: { color: "#f5b942", label: "Pending", icon: <Clock size={14} /> },
  Deploying: { color: "#4da3ff", label: "Deploying", icon: <RefreshCw size={14} /> },
  Failed: { color: "#ff5f6d", label: "Failed", icon: <AlertTriangle size={14} /> },
  Deleting: { color: "#9aa4b2", label: "Deleting", icon: <RefreshCw size={14} /> },
  Deleted: { color: "#9aa4b2", label: "Deleted", icon: <Trash2 size={14} /> },
};

const EMPTY_METRICS: ServiceMetrics = {
  service: "",
  cpu_cores: [],
  memory_megabytes: [],
  request_rate: [],
  error_rate: [],
  source: "empty",
};

const EMPTY_LOGS: LogsResponse = { service: "", lines: [], source: "empty" };
const EMPTY_ALERTS: AlertsResponse = { source: "empty", alerts: [] };
const REPOSITORY_URL = "https://github.com/ParthrChandurkar/ClueStack";
const LOCAL_TOOLS = [
  { label: "Grafana", href: "http://localhost:3001", icon: <BarChart3 size={16} /> },
  { label: "Prometheus", href: "http://localhost:9090", icon: <Activity size={16} /> },
  { label: "Alloy", href: "http://localhost:12345", icon: <Gauge size={16} /> },
  { label: "API Docs", href: "http://localhost:8000/docs", icon: <Terminal size={16} /> },
];

function App() {
  const isLocalApi = apiMode === "local";
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [deployments, setDeployments] = useState<DeploymentRecord[]>([]);
  const [reports, setReports] = useState<DeploymentReportRecord[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [metrics, setMetrics] = useState<ServiceMetrics>(EMPTY_METRICS);
  const [workload, setWorkload] = useState<WorkloadHealth | null>(null);
  const [logs, setLogs] = useState<LogsResponse>(EMPTY_LOGS);
  const [alerts, setAlerts] = useState<AlertsResponse>(EMPTY_ALERTS);
  const [kubernetesContexts, setKubernetesContexts] = useState<KubernetesContextList | null>(null);
  const [selectedKubernetesContext, setSelectedKubernetesContext] = useState("");
  const [clusterOverview, setClusterOverview] = useState<KubernetesClusterOverview | null>(null);
  const [clusterOverviewError, setClusterOverviewError] = useState("");
  const [isClusterOverviewLoading, setIsClusterOverviewLoading] = useState(false);
  const [auditLogs, setAuditLogs] = useState<AuditLogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isObservabilityLoading, setIsObservabilityLoading] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);
  const [isCreatingProject, setIsCreatingProject] = useState(false);
  const [usingApiFallback, setUsingApiFallback] = useState(isDemoMode);
  const [error, setError] = useState("");
  const [projectError, setProjectError] = useState("");
  const [reportError, setReportError] = useState("");
  const [observabilityError, setObservabilityError] = useState("");
  const [logError, setLogError] = useState("");
  const [alertError, setAlertError] = useState("");
  const [logSearch, setLogSearch] = useState("");
  const [logPodFilter, setLogPodFilter] = useState("");
  const [logWindowMinutes, setLogWindowMinutes] = useState(15);
  const [alertSeverityFilter, setAlertSeverityFilter] = useState("");
  const [alertServiceFilter, setAlertServiceFilter] = useState("");
  const observabilityRequest = useRef(0);
  const observabilitySelection = useRef(0);
  const projectDataRequest = useRef(0);
  const selectedProjectIdRef = useRef("");
  const selectedDeploymentNameRef = useRef("");
  const selectedRepositoryProjectIdRef = useRef("");
  const repositoryAnalysisRequest = useRef(0);
  const localRepositoryAnalysisRequest = useRef(0);
  const containerBuildRequest = useRef(0);
  const sourceSnapshotRequest = useRef(0);
  const clusterOverviewRequest = useRef(0);
  const localFolderInputRef = useRef<HTMLInputElement | null>(null);
  const [form, setForm] = useState({
    name: "catalog-api",
    image: "docker.io/example/catalog-api:latest",
    replicas: 2,
    port: 8080,
  });
  const [projectForm, setProjectForm] = useState({ name: "", slug: "" });
  const [githubProjectForm, setGitHubProjectForm] = useState(EMPTY_GITHUB_PROJECT_FORM);
  const [isRegisteringGitHubProject, setIsRegisteringGitHubProject] = useState(false);
  const [githubProjectError, setGitHubProjectError] = useState("");
  const [githubProjectSuccess, setGitHubProjectSuccess] = useState("");
  const [selectedRepositoryProjectId, setSelectedRepositoryProjectId] = useState("");
  const [repositoryBranch, setRepositoryBranch] = useState("");
  const [repositoryAnalysis, setRepositoryAnalysis] = useState<RepositoryAnalysisResult | null>(null);
  const [repositoryAnalysisError, setRepositoryAnalysisError] = useState("");
  const [isAnalyzingRepository, setIsAnalyzingRepository] = useState(false);
  const [repositoryReadiness, setRepositoryReadiness] = useState<RepositoryReadinessResult | null>(null);
  const [repositoryReadinessError, setRepositoryReadinessError] = useState("");
  const [isAssessingReadiness, setIsAssessingReadiness] = useState(false);
  const [localRepositoryFiles, setLocalRepositoryFiles] = useState<File[]>([]);
  const [localSnapshotFiles, setLocalSnapshotFiles] = useState<File[]>([]);
  const [localSnapshotSelectionError, setLocalSnapshotSelectionError] = useState("");
  const [localFolderLabel, setLocalFolderLabel] = useState("");
  const [localSelectionSummary, setLocalSelectionSummary] = useState("");
  const [localRepositoryAnalysis, setLocalRepositoryAnalysis] = useState<RepositoryAnalysisResult | null>(null);
  const [localRepositoryAnalysisError, setLocalRepositoryAnalysisError] = useState("");
  const [isAnalyzingLocalRepository, setIsAnalyzingLocalRepository] = useState(false);
  const [localAnalysisProgress, setLocalAnalysisProgress] = useState(0);
  const [localRepositoryReadiness, setLocalRepositoryReadiness] = useState<RepositoryReadinessResult | null>(null);
  const [localRepositoryReadinessError, setLocalRepositoryReadinessError] = useState("");
  const [isAssessingLocalReadiness, setIsAssessingLocalReadiness] = useState(false);
  const [buildProjectId, setBuildProjectId] = useState("");
  const [buildReports, setBuildReports] = useState<DeploymentReportRecord[]>([]);
  const [buildEligibility, setBuildEligibility] = useState<ContainerBuildEligibilityResult | null>(null);
  const [containerBuild, setContainerBuild] = useState<ContainerBuildRecord | null>(null);
  const [containerBuildLogs, setContainerBuildLogs] = useState("");
  const [containerBuildError, setContainerBuildError] = useState("");
  const [isValidatingBuild, setIsValidatingBuild] = useState(false);
  const [isBuildingContainer, setIsBuildingContainer] = useState(false);
  const [buildApproved, setBuildApproved] = useState(false);
  const [buildForm, setBuildForm] = useState({ imageName: "example/cluestack-app", imageTag: "local", dockerfilePath: "Dockerfile" });
  const [sourceSnapshots, setSourceSnapshots] = useState<SourceSnapshotRecord[]>([]);
  const [sourceSnapshotError, setSourceSnapshotError] = useState("");
  const [isCreatingSnapshot, setIsCreatingSnapshot] = useState(false);

  const localProjects = projects.filter((project) => project.mode === "local" && !project.archived_at);
  const repositoryProjects = projects.filter((project) => project.mode === "github_repository" && !project.archived_at);
  const selectedRepositoryProject = repositoryProjects.find(
    (project) => project.id === selectedRepositoryProjectId,
  ) ?? repositoryProjects[0] ?? null;
  const selectedProject = localProjects.find((project) => project.id === selectedProjectId) ?? null;
  const selectedDeployment = deployments.find((item) => item.name === selected) ?? deployments[0] ?? null;
  const isBrowserFallback = isDemoMode || usingApiFallback;
  const analysisViewState = repositoryAnalysisViewState(
    isAnalyzingRepository,
    repositoryAnalysis,
    repositoryAnalysisError,
  );
  const readinessViewState = repositoryReadinessViewState(
    isAssessingReadiness,
    repositoryReadiness,
    repositoryReadinessError,
  );
  const localAnalysisViewState = repositoryAnalysisViewState(
    isAnalyzingLocalRepository,
    localRepositoryAnalysis,
    localRepositoryAnalysisError,
  );
  const localReadinessViewState = repositoryReadinessViewState(
    isAssessingLocalReadiness,
    localRepositoryReadiness,
    localRepositoryReadinessError,
  );
  const remoteAnalysisAllowed = repositoryRemoteAnalysisAllowed(isDemoMode);
  const clusterViewState = clusterOverviewViewState(
    isClusterOverviewLoading,
    kubernetesContexts,
    clusterOverview,
    clusterOverviewError,
  );

  useEffect(() => subscribeToApiFallback(setUsingApiFallback), []);

  useEffect(() => {
    let isMounted = true;
    clusterOverviewRequest.current += 1;
    setIsClusterOverviewLoading(true);
    setClusterOverviewError("");
    listKubernetesContexts()
      .then((result) => {
        if (!isMounted) return;
        setKubernetesContexts(result);
        setSelectedKubernetesContext((current) => result.contexts.includes(current) ? current : "");
        setClusterOverview(null);
      })
      .catch((reason) => {
        if (!isMounted) return;
        setKubernetesContexts(null);
        setClusterOverview(null);
        setClusterOverviewError(reason instanceof Error ? reason.message : "Kubernetes contexts are unavailable");
      })
      .finally(() => isMounted && setIsClusterOverviewLoading(false));
    return () => {
      isMounted = false;
    };
  }, [isBrowserFallback]);

  useEffect(() => {
    selectedProjectIdRef.current = selectedProjectId;
  }, [selectedProjectId]);

  const selectDeployment = useCallback((name: string) => {
    if (selectedDeploymentNameRef.current !== name) {
      selectedDeploymentNameRef.current = name;
      observabilitySelection.current += 1;
      observabilityRequest.current += 1;
    }
    setSelected(name);
  }, []);

  const selectProject = useCallback((projectId: string) => {
    if (selectedProjectIdRef.current !== projectId) {
      selectedProjectIdRef.current = projectId;
      selectedDeploymentNameRef.current = "";
      observabilitySelection.current += 1;
      observabilityRequest.current += 1;
      localRepositoryAnalysisRequest.current += 1;
      setSelected("");
      setLocalRepositoryAnalysis(null);
      setLocalRepositoryAnalysisError("");
      setIsAnalyzingLocalRepository(false);
      setLocalRepositoryReadiness(null);
      setLocalRepositoryReadinessError("");
      setIsAssessingLocalReadiness(false);
      setLocalRepositoryFiles([]);
      setLocalFolderLabel("");
      setLocalSelectionSummary("");
      setLocalAnalysisProgress(0);
      if (localFolderInputRef.current) {
        localFolderInputRef.current.value = "";
      }
    }
    setSelectedProjectId(projectId);
  }, []);

  const selectRepositoryProject = useCallback((projectId: string) => {
    if (selectedRepositoryProjectIdRef.current !== projectId) {
      selectedRepositoryProjectIdRef.current = projectId;
      repositoryAnalysisRequest.current += 1;
      setRepositoryAnalysis(null);
      setRepositoryAnalysisError("");
      setIsAnalyzingRepository(false);
      setRepositoryReadiness(null);
      setRepositoryReadinessError("");
      setIsAssessingReadiness(false);
    }
    setSelectedRepositoryProjectId(projectId);
  }, []);

  const summary = useMemo(() => {
    return deployments.reduce(
      (acc, item) => {
        const status = isManifestSimulation(item) ? "Simulated" : item.status;
        acc[status] = (acc[status] ?? 0) + 1;
        acc.requestedReplicas += item.replicas;
        if (status === "Running") {
          acc.activeReplicas += item.available_replicas ?? 0;
        }
        return acc;
      },
      {
        Running: 0,
        Simulated: 0,
        Failed: 0,
        Pending: 0,
        Deploying: 0,
        Deleting: 0,
        Deleted: 0,
        requestedReplicas: 0,
        activeReplicas: 0,
      } as Record<DeploymentStatus, number> & { requestedReplicas: number; activeReplicas: number },
    );
  }, [deployments]);

  const refreshProjects = useCallback(async (preferredProjectId?: string, selectPreferred = false) => {
    const defaultProject = await getDefaultLocalProject();
    const items = await listProjects();
    const selectable = items.filter((project) => project.mode === "local" && !project.archived_at);
    const desiredProjectId = selectPreferred
      ? preferredProjectId
      : selectedProjectIdRef.current || preferredProjectId;
    const nextProject = selectable.find((project) => project.id === desiredProjectId)
      ?? selectable.find((project) => project.id === defaultProject.id)
      ?? defaultProject;
    setProjects(items.some((project) => project.id === defaultProject.id) ? items : [defaultProject, ...items]);
    const repositoryItems = items.filter((project) => project.mode === "github_repository" && !project.archived_at);
    const currentRepositoryProjectId = selectedRepositoryProjectIdRef.current;
    selectRepositoryProject(
      repositoryItems.some((project) => project.id === currentRepositoryProjectId)
        ? currentRepositoryProjectId
        : repositoryItems[0]?.id ?? "",
    );
    selectProject(nextProject.id);
    setProjectError("");
    return nextProject;
  }, [selectProject, selectRepositoryProject]);

  const refreshDeployments = useCallback(async (projectId: string, requestId: number) => {
    const items = await listProjectDeployments(projectId);
    if (requestId !== projectDataRequest.current) {
      return;
    }
    setDeployments(items);
    const nextSelected = items.find((item) => item.name === selectedDeploymentNameRef.current)?.name
      ?? items[0]?.name
      ?? "";
    selectDeployment(nextSelected);
  }, [selectDeployment]);

  const refreshReports = useCallback(async (projectId: string, requestId: number) => {
    try {
      const items = await listProjectReports(projectId);
      if (requestId !== projectDataRequest.current) {
        return;
      }
      setReports(items);
      setReportError("");
    } catch (reason) {
      if (requestId !== projectDataRequest.current) {
        return;
      }
      setReports([]);
      setReportError(reason instanceof Error ? reason.message : "Report history is unavailable");
    }
  }, []);

  const refreshProjectData = useCallback(async (projectId: string) => {
    const requestId = ++projectDataRequest.current;
    await Promise.all([
      refreshDeployments(projectId, requestId),
      refreshReports(projectId, requestId),
    ]);
  }, [refreshDeployments, refreshReports]);

  const refreshAuditLogs = useCallback(async () => {
    const entries = await listAuditLogs(30);
    setAuditLogs(entries);
  }, []);

  const refreshAlerts = useCallback(async () => {
    try {
      const response = await getAlerts();
      setAlerts(response);
      setAlertError("");
    } catch (reason) {
      setAlerts({ source: "unavailable", alerts: [] });
      setAlertError(reason instanceof Error ? reason.message : "Alertmanager is unavailable");
    }
  }, []);

  const refreshObservability = useCallback(async (
    serviceName: string,
    requestId: number,
    logOptions: { search?: string; pod?: string; minutes?: number } = {},
  ) => {
    const [metricResult, logResult, workloadResult] = await Promise.allSettled([
      getMetrics(serviceName),
      getLogs(serviceName, { ...logOptions, limit: 100 }),
      getWorkloadHealth(serviceName),
    ]);

    if (requestId !== observabilityRequest.current) {
      return false;
    }

    const failures: string[] = [];
    if (metricResult.status === "fulfilled") {
      setMetrics(metricResult.value);
    } else {
      failures.push("metrics");
    }
    if (logResult.status === "fulfilled") {
      setLogs(logResult.value);
      setLogError("");
    } else {
      setLogs({ service: serviceName, lines: [], source: "unavailable" });
      setLogError(logResult.reason instanceof Error ? logResult.reason.message : "Loki is unavailable");
    }
    if (workloadResult.status === "fulfilled") {
      setWorkload(workloadResult.value);
    } else {
      failures.push("workload health");
    }
    if (failures.length) {
      throw new Error(`Unable to refresh ${failures.join(" and ")}`);
    }
    return true;
  }, []);

  useEffect(() => {
    let isMounted = true;
    Promise.all([refreshProjects(), refreshAuditLogs(), refreshAlerts()])
      .catch((reason: Error) => {
        if (isMounted) {
          setProjectError(reason.message);
          setError("Unable to load projects from ClueStack.");
          setIsLoading(false);
        }
      });
    return () => {
      isMounted = false;
    };
  }, [refreshAlerts, refreshAuditLogs, refreshProjects]);

  useEffect(() => {
    if (!selectedProjectId) {
      return;
    }
    let isMounted = true;
    setIsLoading(true);
    setDeployments([]);
    setReports([]);
    setReportError("");
    selectDeployment("");
    refreshProjectData(selectedProjectId)
      .catch((reason: Error) => isMounted && setError(reason.message))
      .finally(() => isMounted && setIsLoading(false));
    return () => {
      isMounted = false;
    };
  }, [refreshProjectData, selectDeployment, selectedProjectId]);

  useEffect(() => {
    const serviceName = selectedDeployment?.name;
    if (!serviceName) {
      setMetrics(EMPTY_METRICS);
      setLogs(EMPTY_LOGS);
      setWorkload(null);
      setObservabilityError("");
      setLogError("");
      setIsObservabilityLoading(false);
      void refreshAlerts();
      return;
    }

    let isMounted = true;
    let requestInFlight = false;
    const load = async () => {
      if (requestInFlight) {
        return;
      }
      requestInFlight = true;
      const requestId = ++observabilityRequest.current;
      setIsObservabilityLoading(true);
      try {
        const applied = await refreshObservability(serviceName, requestId, {
          search: logSearch,
          pod: logPodFilter,
          minutes: logWindowMinutes,
        });
        if (!applied) {
          return;
        }
        await refreshAlerts();
        if (isMounted && requestId === observabilityRequest.current) {
          setObservabilityError("");
        }
      } catch (reason) {
        if (isMounted && requestId === observabilityRequest.current) {
          setObservabilityError(reason instanceof Error ? reason.message : "Observability refresh failed");
        }
      } finally {
        requestInFlight = false;
        if (isMounted && requestId === observabilityRequest.current) {
          setIsObservabilityLoading(false);
        }
      }
    };

    load();
    const timer = window.setInterval(load, 7000);
    return () => {
      isMounted = false;
      observabilityRequest.current += 1;
      window.clearInterval(timer);
    };
  }, [logPodFilter, logSearch, logWindowMinutes, refreshAlerts, refreshObservability, selectedDeployment?.name]);

  async function handleRefresh() {
    const selectionId = observabilitySelection.current;
    setError("");
    try {
      const project = await refreshProjects(selectedProjectId);
      if (selectionId !== observabilitySelection.current) {
        return;
      }
      await refreshProjectData(project.id);
      if (selectionId !== observabilitySelection.current) {
        return;
      }
      await refreshAuditLogs();
      if (selectionId !== observabilitySelection.current) {
        return;
      }
      await refreshAlerts();
      if (selectionId !== observabilitySelection.current) {
        return;
      }
      const serviceName = selectedDeploymentNameRef.current;
      if (serviceName) {
        const requestId = ++observabilityRequest.current;
        setIsObservabilityLoading(true);
        try {
          await refreshObservability(serviceName, requestId, {
            search: logSearch,
            pod: logPodFilter,
            minutes: logWindowMinutes,
          });
        } finally {
          if (requestId === observabilityRequest.current) {
            setIsObservabilityLoading(false);
          }
        }
      }
    } catch (reason) {
      if (selectionId === observabilitySelection.current) {
        setError(reason instanceof Error ? reason.message : "Refresh failed");
      }
    }
  }

  async function handleDeploy(event: FormEvent) {
    event.preventDefault();
    if (!selectedProject) {
      setError("Select a local project before deploying a service.");
      return;
    }
    setIsDeploying(true);
    setError("");
    let deploymentCompleted = false;
    try {
      const response = await deployService({
        name: form.name.trim(),
        project_id: selectedProject.id,
        image: form.image.trim(),
        replicas: form.replicas,
        port: form.port,
        environment: { ENVIRONMENT: "production" },
      });
      deploymentCompleted = true;
      if (selectedProjectIdRef.current === selectedProject.id) {
        await refreshProjectData(selectedProject.id);
      }
      await refreshAuditLogs();
      if (selectedProjectIdRef.current === selectedProject.id) {
        selectDeployment(response.deployment.name);
      }
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "Unknown error";
      setError(deploymentCompleted
        ? `Deployment request succeeded, but refreshing the dashboard failed: ${detail}`
        : detail);
    } finally {
      setIsDeploying(false);
    }
  }

  async function handleCreateProject(event: FormEvent) {
    event.preventDefault();
    setIsCreatingProject(true);
    setProjectError("");
    try {
      const created = await createProject(buildLocalProjectCreate(projectForm.name, projectForm.slug));
      setProjectForm({ name: "", slug: "" });
      await refreshProjects(created.id, true);
    } catch (reason) {
      setProjectError(reason instanceof Error ? reason.message : "Project creation failed");
    } finally {
      setIsCreatingProject(false);
    }
  }

  async function handleRegisterGitHubProject(event: FormEvent) {
    event.preventDefault();
    if (isRegisteringGitHubProject) {
      return;
    }
    setGitHubProjectError("");
    setGitHubProjectSuccess("");
    if (isBrowserFallback) {
      setGitHubProjectError("GitHub repository registration requires the ClueStack backend and is unavailable in browser Demo Mode.");
      return;
    }
    const validationErrors = validateGitHubProjectRegistration(githubProjectForm);
    if (Object.keys(validationErrors).length > 0) {
      setGitHubProjectError(Object.values(validationErrors).join(" "));
      return;
    }

    setIsRegisteringGitHubProject(true);
    let createdProject: ProjectRecord | null = null;
    try {
      createdProject = await registerGitHubProject(
        githubProjectForm,
        async (payload) => {
          createdProject = await createProject(payload);
          return createdProject;
        },
        async (created) => {
          await refreshProjects();
          selectRepositoryProject(created.id);
        },
      );
      setGitHubProjectForm(EMPTY_GITHUB_PROJECT_FORM);
      setGitHubProjectSuccess(`${createdProject.name} is registered and ready for read-only repository analysis.`);
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "Repository registration failed";
      setGitHubProjectError(createdProject
        ? `Repository registration succeeded, but refreshing the project list failed: ${detail}`
        : detail);
    } finally {
      setIsRegisteringGitHubProject(false);
    }
  }

  async function handleClusterOverviewRefresh() {
    if (!canRefreshCluster(
      selectedKubernetesContext,
      isClusterOverviewLoading,
      Boolean(kubernetesContexts?.enabled),
    )) {
      return;
    }
    const requestedContext = selectedKubernetesContext;
    const requestId = ++clusterOverviewRequest.current;
    setIsClusterOverviewLoading(true);
    setClusterOverviewError("");
    try {
      const result = await getKubernetesClusterOverview(requestedContext);
      if (requestId === clusterOverviewRequest.current) {
        setClusterOverview(result);
      }
    } catch (reason) {
      if (requestId === clusterOverviewRequest.current) {
        setClusterOverview(null);
        setClusterOverviewError(reason instanceof Error ? reason.message : "Cluster overview is unavailable");
      }
    } finally {
      if (requestId === clusterOverviewRequest.current) {
        setIsClusterOverviewLoading(false);
      }
    }
  }

  function handleLocalFolderSelection(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.currentTarget.files ?? []);
    const candidates = files.map((file) => ({ path: file.webkitRelativePath || file.name, size: file.size }));
    const summary = summarizeLocalSelection(candidates);
    const snapshotSummary = summarizeLocalSnapshotSelection(candidates);
    const acceptedPaths = new Set(summary.accepted.map((item) => item.path));
    const snapshotPaths = new Set(snapshotSummary.accepted.map((item) => item.path));
    const rootLabel = (candidates[0]?.path.split("/")[0] || "Selected local folder").slice(0, 80);
    localRepositoryAnalysisRequest.current += 1;
    setIsAnalyzingLocalRepository(false);
    setIsAssessingLocalReadiness(false);
    setLocalAnalysisProgress(0);
    setLocalFolderLabel(rootLabel);
    setLocalRepositoryFiles(files.filter((file) => acceptedPaths.has(file.webkitRelativePath || file.name)));
    setLocalSnapshotFiles(files.filter((file) => snapshotPaths.has(file.webkitRelativePath || file.name)));
    setLocalSnapshotSelectionError(snapshotSummary.error);
    setLocalSelectionSummary(
      summary.error
        || `${summary.accepted.length} recognized file(s), ${summary.totalBytes.toLocaleString()} bytes; ${summary.ignoredCount} unsupported or excluded file(s).`,
    );
    setLocalRepositoryAnalysis(null);
    setLocalRepositoryAnalysisError(summary.error);
    setLocalRepositoryReadiness(null);
    setLocalRepositoryReadinessError("");
  }

  async function handleLocalRepositoryAnalysis(event: FormEvent) {
    event.preventDefault();
    if (!selectedProject) {
      setLocalRepositoryAnalysisError("Select an existing local project first.");
      return;
    }
    if (!localFolderLabel || localRepositoryAnalysisError) {
      setLocalRepositoryAnalysisError(localRepositoryAnalysisError || "Select a local project folder first.");
      return;
    }
    setIsAnalyzingLocalRepository(true);
    setLocalAnalysisProgress(0);
    setLocalRepositoryAnalysis(null);
    setLocalRepositoryAnalysisError("");
    setLocalRepositoryReadiness(null);
    setLocalRepositoryReadinessError("");
    const requestId = ++localRepositoryAnalysisRequest.current;
    const projectId = selectedProject.id;
    try {
      const files: LocalRepositoryAnalysisFile[] = [];
      for (const file of localRepositoryFiles) {
        files.push({
          path: repositoryRelativePath(file.webkitRelativePath || file.name),
          content: await file.text(),
        });
        if (isCurrentRepositoryRequest(requestId, localRepositoryAnalysisRequest.current)) {
          setLocalAnalysisProgress(files.length);
        }
      }
      if (!isCurrentRepositoryRequest(requestId, localRepositoryAnalysisRequest.current)) {
        return;
      }
      const result = await analyzeLocalRepository(projectId, {
        source_label: localFolderLabel,
        submission_complete: true,
        files,
      });
      if (!isCurrentRepositoryRequest(requestId, localRepositoryAnalysisRequest.current)) {
        return;
      }
      setLocalRepositoryAnalysis(result);
      void refreshProjectData(projectId).catch((reason) => {
        if (selectedProjectIdRef.current === projectId) {
          setReportError(reason instanceof Error ? reason.message : "Report history refresh failed");
        }
      });
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, localRepositoryAnalysisRequest.current)) {
        setLocalRepositoryAnalysisError(reason instanceof Error ? reason.message : "Local repository analysis failed");
      }
    } finally {
      if (isCurrentRepositoryRequest(requestId, localRepositoryAnalysisRequest.current)) {
        setIsAnalyzingLocalRepository(false);
      }
    }
  }

  async function handleLocalReadinessAssessment() {
    if (!selectedProject) {
      setLocalRepositoryReadinessError("Select an existing local project first.");
      return;
    }
    setIsAssessingLocalReadiness(true);
    setLocalRepositoryReadiness(null);
    setLocalRepositoryReadinessError("");
    const requestId = ++localRepositoryAnalysisRequest.current;
    const projectId = selectedProject.id;
    try {
      const result = await assessRepositoryReadiness(projectId, localRepositoryAnalysis?.report_id);
      if (isCurrentRepositoryRequest(requestId, localRepositoryAnalysisRequest.current)) {
        setLocalRepositoryReadiness(result);
      }
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, localRepositoryAnalysisRequest.current)) {
        setLocalRepositoryReadinessError(reason instanceof Error ? reason.message : "Readiness assessment failed");
      }
    } finally {
      if (isCurrentRepositoryRequest(requestId, localRepositoryAnalysisRequest.current)) {
        setIsAssessingLocalReadiness(false);
      }
    }
  }

  async function loadBuildEvidence(projectId: string) {
    const requestId = ++containerBuildRequest.current;
    const snapshotRequestId = ++sourceSnapshotRequest.current;
    setBuildProjectId(projectId);
    setBuildEligibility(null);
    setContainerBuild(null);
    setContainerBuildLogs("");
    setContainerBuildError("");
    setBuildApproved(false);
    setSourceSnapshots([]);
    setSourceSnapshotError("");
    try {
      const [items, snapshots] = await Promise.all([
        listProjectReports(projectId),
        isDemoMode ? Promise.resolve([]) : listSourceSnapshots(projectId),
      ]);
      if (isCurrentRepositoryRequest(requestId, containerBuildRequest.current)) {
        setBuildReports(items);
      }
      if (isCurrentRepositoryRequest(snapshotRequestId, sourceSnapshotRequest.current)) {
        setSourceSnapshots(snapshots);
      }
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, containerBuildRequest.current)) {
        setContainerBuildError(reason instanceof Error ? reason.message : "Build evidence could not be loaded");
      }
    }
  }

  async function handleCreateSourceSnapshot() {
    const project = projects.find((item) => item.id === buildProjectId);
    const analysis = buildReports.find((report) => report.operation === "repository_analysis" && ["success", "partial"].includes(report.status));
    if (!project || !analysis) {
      setSourceSnapshotError("Select a project with repository analysis evidence first.");
      return;
    }
    if (project.mode === "local" && (localSnapshotSelectionError || localSnapshotFiles.length === 0)) {
      setSourceSnapshotError(localSnapshotSelectionError || "Choose the local project folder before creating a snapshot.");
      return;
    }
    const requestId = ++sourceSnapshotRequest.current;
    setIsCreatingSnapshot(true);
    setSourceSnapshotError("");
    try {
      const files: LocalRepositoryAnalysisFile[] = [];
      if (project.mode === "local") {
        for (const file of localSnapshotFiles) {
          files.push({
            path: repositoryRelativePath(file.webkitRelativePath || file.name),
            content: await file.text(),
          });
          if (!isCurrentRepositoryRequest(requestId, sourceSnapshotRequest.current)) return;
        }
      }
      await createSourceSnapshot(project.id, {
        reference: project.mode === "github_repository" ? repositoryBranch.trim() || null : null,
        analysis_report_id: analysis.id,
        source_label: project.mode === "local" ? localFolderLabel : null,
        submission_complete: false,
        files,
      });
      if (isCurrentRepositoryRequest(requestId, sourceSnapshotRequest.current)) {
        setSourceSnapshots(await listSourceSnapshots(project.id));
        setBuildEligibility(null);
      }
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, sourceSnapshotRequest.current)) {
        setSourceSnapshotError(reason instanceof Error ? reason.message : "Source snapshot creation failed");
      }
    } finally {
      if (isCurrentRepositoryRequest(requestId, sourceSnapshotRequest.current)) setIsCreatingSnapshot(false);
    }
  }

  async function handleVerifySourceSnapshot(snapshotId: string) {
    const requestId = ++sourceSnapshotRequest.current;
    setSourceSnapshotError("");
    try {
      await verifySourceSnapshot(buildProjectId, snapshotId);
      const snapshots = await listSourceSnapshots(buildProjectId);
      if (isCurrentRepositoryRequest(requestId, sourceSnapshotRequest.current)) setSourceSnapshots(snapshots);
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, sourceSnapshotRequest.current)) {
        setSourceSnapshotError(reason instanceof Error ? reason.message : "Snapshot verification failed");
      }
    }
  }

  async function handleDeleteSourceSnapshot(snapshotId: string) {
    if (!window.confirm("Delete this source snapshot? Published source bytes will be removed.")) return;
    const requestId = ++sourceSnapshotRequest.current;
    setSourceSnapshotError("");
    try {
      await deleteSourceSnapshot(buildProjectId, snapshotId);
      const snapshots = await listSourceSnapshots(buildProjectId);
      if (isCurrentRepositoryRequest(requestId, sourceSnapshotRequest.current)) {
        setSourceSnapshots(snapshots);
        setBuildEligibility(null);
      }
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, sourceSnapshotRequest.current)) {
        setSourceSnapshotError(reason instanceof Error ? reason.message : "Snapshot deletion failed");
      }
    }
  }

  function currentBuildPayload(): ContainerBuildEligibilityPayload | null {
    const analysis = buildReports.find((report) => report.operation === "repository_analysis" && ["success", "partial"].includes(report.status));
    const readiness = buildReports.find((report) => report.operation === "repository_readiness" && report.status === "success");
    const snapshot = snapshotBuildCandidate(sourceSnapshots, analysis?.id);
    if (!analysis || !readiness || !snapshot) {
      return null;
    }
    return {
      analysis_report_id: analysis.id,
      readiness_report_id: readiness.id,
      source_snapshot_id: snapshot.id,
      dockerfile_path: buildForm.dockerfilePath,
      context_path: ".",
      image_name: buildForm.imageName,
      image_tag: buildForm.imageTag,
    };
  }

  async function handleBuildValidation() {
    const payload = currentBuildPayload();
    if (!buildProjectId || !payload) {
      setContainerBuildError("Run repository analysis and readiness assessment for the selected project first.");
      return;
    }
    const requestId = ++containerBuildRequest.current;
    setIsValidatingBuild(true);
    setBuildEligibility(null);
    setContainerBuildError("");
    try {
      const result = await validateContainerBuild(buildProjectId, payload);
      if (isCurrentRepositoryRequest(requestId, containerBuildRequest.current)) {
        setBuildEligibility(result);
      }
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, containerBuildRequest.current)) {
        setContainerBuildError(reason instanceof Error ? reason.message : "Build validation failed");
      }
    } finally {
      if (isCurrentRepositoryRequest(requestId, containerBuildRequest.current)) {
        setIsValidatingBuild(false);
      }
    }
  }

  async function handleContainerBuild() {
    const payload = currentBuildPayload();
    if (!buildProjectId || !payload || !buildApproved) {
      setContainerBuildError("Validate the build and provide explicit approval before submitting it.");
      return;
    }
    const requestId = ++containerBuildRequest.current;
    setIsBuildingContainer(true);
    setContainerBuild(null);
    setContainerBuildLogs("");
    setContainerBuildError("");
    try {
      const result = await createContainerBuild(buildProjectId, { ...payload, approved: true });
      if (!isCurrentRepositoryRequest(requestId, containerBuildRequest.current)) {
        return;
      }
      setContainerBuild(result);
      const logs = await getContainerBuildLogs(buildProjectId, result.id);
      if (isCurrentRepositoryRequest(requestId, containerBuildRequest.current)) {
        setContainerBuildLogs(logs.logs);
      }
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, containerBuildRequest.current)) {
        setContainerBuildError(reason instanceof Error ? reason.message : "Container build failed");
      }
    } finally {
      if (isCurrentRepositoryRequest(requestId, containerBuildRequest.current)) {
        setIsBuildingContainer(false);
      }
    }
  }

  async function handleRepositoryAnalysis(event: FormEvent) {
    event.preventDefault();
    if (!selectedRepositoryProject) {
      setRepositoryAnalysisError("Select an existing public GitHub repository project first.");
      return;
    }
    setIsAnalyzingRepository(true);
    setRepositoryAnalysis(null);
    setRepositoryAnalysisError("");
    setRepositoryReadiness(null);
    setRepositoryReadinessError("");
    setIsAssessingReadiness(false);
    const requestId = ++repositoryAnalysisRequest.current;
    try {
      const result = await analyzeRepository(selectedRepositoryProject.id, repositoryBranch);
      if (!isCurrentRepositoryRequest(requestId, repositoryAnalysisRequest.current)) {
        return;
      }
      setRepositoryAnalysis(result);
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, repositoryAnalysisRequest.current)) {
        setRepositoryAnalysisError(reason instanceof Error ? reason.message : "Repository analysis failed");
      }
    } finally {
      if (isCurrentRepositoryRequest(requestId, repositoryAnalysisRequest.current)) {
        setIsAnalyzingRepository(false);
      }
    }
  }

  async function handleReadinessAssessment() {
    if (!selectedRepositoryProject) {
      setRepositoryReadinessError("Select an existing public GitHub repository project first.");
      return;
    }
    setIsAssessingReadiness(true);
    setIsAnalyzingRepository(false);
    setRepositoryReadiness(null);
    setRepositoryReadinessError("");
    const requestId = ++repositoryAnalysisRequest.current;
    try {
      const result = await assessRepositoryReadiness(
        selectedRepositoryProject.id,
        repositoryAnalysis?.report_id,
      );
      if (!isCurrentRepositoryRequest(requestId, repositoryAnalysisRequest.current)) {
        return;
      }
      setRepositoryReadiness(result);
    } catch (reason) {
      if (isCurrentRepositoryRequest(requestId, repositoryAnalysisRequest.current)) {
        setRepositoryReadinessError(reason instanceof Error ? reason.message : "Readiness assessment failed");
      }
    } finally {
      if (isCurrentRepositoryRequest(requestId, repositoryAnalysisRequest.current)) {
        setIsAssessingReadiness(false);
      }
    }
  }

  async function handleDelete(name: string) {
    if (!window.confirm(`Remove ${name} from ${isBrowserFallback ? "your browser sandbox" : "ClueStack"}?`)) {
      return;
    }
    setError("");
    let deletionCompleted = false;
    try {
      await deleteDeployment(name);
      deletionCompleted = true;
      if (selectedProject && selectedProjectIdRef.current === selectedProject.id) {
        await refreshProjectData(selectedProject.id);
      }
      await refreshAuditLogs();
      if (selectedProject && selectedProjectIdRef.current === selectedProject.id) {
        selectDeployment("");
      }
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "Unknown error";
      setError(deletionCompleted
        ? `Delete request succeeded, but refreshing the dashboard failed: ${detail}`
        : detail);
    }
  }

  async function handleRollback(name: string) {
    if (!window.confirm(`Rollback ${name} to the previous Kubernetes revision?`)) {
      return;
    }
    setError("");
    let rollbackCompleted = false;
    try {
      await rollbackDeployment(name);
      rollbackCompleted = true;
      if (selectedProject && selectedProjectIdRef.current === selectedProject.id) {
        await refreshProjectData(selectedProject.id);
      }
      await refreshAuditLogs();
      if (selectedProject && selectedProjectIdRef.current === selectedProject.id) {
        selectDeployment(name);
      }
    } catch (reason) {
      const detail = reason instanceof Error ? reason.message : "Unknown error";
      setError(rollbackCompleted
        ? `Rollback request succeeded, but refreshing the dashboard failed: ${detail}`
        : detail);
    }
  }

  async function handleResetSandbox() {
    resetSandbox();
    setError("");
    setIsLoading(true);
    try {
      const project = await refreshProjects();
      await Promise.all([
        refreshProjectData(project.id),
        refreshAuditLogs(),
        refreshAlerts(),
      ]);
    } finally {
      setIsLoading(false);
    }
  }

  const liveChartData = useMemo(
    () =>
      metrics.cpu_cores.map((point, index) => ({
        time: new Date(point.timestamp * 1000).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
        cpu: point.value,
        memory: metrics.memory_megabytes[index]?.value ?? 0,
        requests: metrics.request_rate[index]?.value ?? 0,
        errors: metrics.error_rate[index]?.value ?? 0,
      })),
    [metrics],
  );
  const chartData = liveChartData;
  const hasMetricData = chartData.length > 0;
  const telemetryMode = liveChartData.length
    ? sourceLabel(metrics.source)
    : selectedDeployment
        ? "No live data"
        : "Waiting";

  const telemetry = useMemo(() => {
    const latest = chartData[chartData.length - 1];
    const latestErrorRate = Number(latest?.errors ?? 0);
    const healthScore = Math.max(
      0,
      Math.min(
        100,
        Math.round(100 - summary.Failed * 20 - (summary.Pending + summary.Deploying) * 6 - latestErrorRate * 12),
      ),
    );

    return {
      cpu: Number(latest?.cpu ?? 0),
      memory: Number(latest?.memory ?? 0),
      requests: Number(latest?.requests ?? 0),
      errors: latestErrorRate,
      healthScore,
      peakMemory: maxValue(chartData.map((item) => Number(item.memory))),
      avgCpu: averageValue(chartData.map((item) => Number(item.cpu))),
    };
  }, [chartData, summary.Deploying, summary.Failed, summary.Pending]);

  const selectedIsSimulation = selectedDeployment ? isManifestSimulation(selectedDeployment) : false;
  const selectedStatus: DeploymentStatus = selectedIsSimulation
    ? "Simulated"
    : selectedDeployment?.status ?? "Pending";
  const hasObservedWorkload = workload?.source === "kubernetes";
  const workloadDesired = workload?.desired_replicas ?? selectedDeployment?.replicas ?? 0;
  const workloadReady = hasObservedWorkload ? workload.ready_replicas : null;
  const workloadAvailable = hasObservedWorkload ? workload.available_replicas : null;
  const workloadUnavailable = hasObservedWorkload ? workload.unavailable_replicas : null;
  const logPodOptions = useMemo(() => {
    const names = new Set<string>();
    workload?.pods.forEach((pod) => names.add(pod.name));
    logs.lines.forEach((entry) => entry.pod && names.add(entry.pod));
    return Array.from(names).sort();
  }, [logs.lines, workload?.pods]);
  const alertSeverityOptions = useMemo(
    () => Array.from(new Set(alerts.alerts.map((alert) => alert.severity).filter(Boolean))).sort() as string[],
    [alerts.alerts],
  );
  const alertServiceOptions = useMemo(
    () => Array.from(new Set(alerts.alerts.map((alert) => alert.service).filter(Boolean))).sort() as string[],
    [alerts.alerts],
  );
  const visibleAlerts = useMemo(
    () =>
      alerts.alerts.filter((alert) => {
        const severityMatches = !alertSeverityFilter || alert.severity === alertSeverityFilter;
        const serviceMatches = !alertServiceFilter || alert.service === alertServiceFilter;
        return severityMatches && serviceMatches;
      }),
    [alertServiceFilter, alertSeverityFilter, alerts.alerts],
  );
  const firingAlerts = alerts.alerts.filter((alert) => alert.status === "firing").length;
  const alertSourceLabel = alertError ? "Unavailable" : sourceLabel(alerts.source);

  const hasVerifiedHealth = selectedStatus === "Running" && hasObservedWorkload && metrics.source === "prometheus";
  const displayedHealthScore = hasVerifiedHealth ? telemetry.healthScore : null;
  const healthTone = displayedHealthScore === null
    ? "attention"
    : displayedHealthScore >= 90
      ? "excellent"
      : displayedHealthScore >= 70
        ? "steady"
        : "attention";
  const activeRolloutCount = summary.Pending + summary.Deploying;
  const signalCount = [
    summary.Failed === 0,
    activeRolloutCount === 0,
    telemetry.errors < 0.2,
    firingAlerts === 0,
    hasVerifiedHealth,
  ].filter(Boolean).length;

  const pipelineSteps = [
    { icon: <GitBranch size={17} />, label: "GitHub", value: "Repository link", state: "ready" },
    { icon: <Rocket size={17} />, label: "Delivery", value: "Local workflow", state: "ready" },
    { icon: <Layers size={17} />, label: "Deployments", value: isBrowserFallback ? "Manifest simulation" : "Backend records", state: "ready" },
    { icon: <Server size={17} />, label: "Runtime", value: isBrowserFallback ? "Browser sandbox" : isLocalApi ? "Local API" : "Configured API", state: "live" },
    { icon: <Gauge size={17} />, label: "Telemetry", value: telemetryMode, state: "live" },
  ];

  const platformSignals = [
    {
      icon: <Server size={18} />,
      label: "Local Kubernetes",
      value: isBrowserFallback ? "Simulated" : workload?.source === "kubernetes" ? "Verified" : "Not verified",
      detail: isBrowserFallback
        ? "Safe browser sandbox"
        : workload?.source === "kubernetes"
          ? "Workload state was read from Kubernetes"
          : "No live Kubernetes workload evidence is available",
      tone: isBrowserFallback || workload?.source !== "kubernetes" ? "warn" : "ok",
    },
    {
      icon: <Activity size={18} />,
      label: "Prometheus",
      value: isBrowserFallback ? "Mock metrics" : sourceLabel(metrics.source),
      detail: "CPU, memory, request rate, and error-rate charts",
      tone: "ok",
    },
    {
      icon: <Terminal size={18} />,
      label: "Alloy → Loki",
      value: isBrowserFallback ? "Mock logs" : sourceLabel(logs.source),
      detail: "Grafana Alloy forwards app logs into Loki",
      tone: logs.source === "empty" && !isBrowserFallback ? "warn" : "ok",
    },
    {
      icon: <AlertTriangle size={18} />,
      label: "Alertmanager",
      value: alertError ? "Unavailable" : `${sourceLabel(alerts.source)} · ${firingAlerts} firing`,
      detail: "Prometheus alert rules surface through Alertmanager",
      tone: alertError || firingAlerts ? "warn" : "ok",
    },
    {
      icon: <BarChart3 size={18} />,
      label: "Grafana",
      value: "Optional tool",
      detail: "Open Grafana separately when it is configured and reachable",
      tone: "blue",
    },
    {
      icon: <ShieldCheck size={18} />,
      label: "Audit trail",
      value: `${auditLogs.length} events`,
      detail: "Deployment, rollback, and delete actions are tracked",
      tone: auditLogs.length ? "ok" : "blue",
    },
  ];

  const flowSteps = [
    {
      icon: <Rocket size={18} />,
      title: "Add app",
      body: "Enter image, port, replicas, and a service name.",
    },
    {
      icon: <Layers size={18} />,
      title: "Deploy locally",
      body: "ClueStack creates Kubernetes Deployment and Service manifests.",
    },
    {
      icon: <Activity size={18} />,
      title: "Observe",
      body: "Prometheus handles metrics; Alloy forwards logs into Loki.",
    },
    {
      icon: <RotateCcw size={18} />,
      title: "Recover",
      body: "Use audit history, rollback, and delete actions during testing.",
    },
  ];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><img src={clueStackMark} alt="" /></span>
          <div>
            <strong>ClueStack</strong>
            <small>Every signal tells a story.</small>
          </div>
        </div>

        <div className="environment-card">
          <div>
            <span>Environment</span>
            <strong>{isBrowserFallback ? "Browser Demo Mode" : isLocalApi ? "Local API" : "Configured API"}</strong>
          </div>
          <span className="pulse-dot" />
        </div>

        <nav className="sidebar-nav" aria-label="Dashboard sections">
          <a href="#projects">Projects</a>
          <a href="#repository-analysis">Repository Analysis</a>
          <a href="#kubernetes-cluster">Kubernetes</a>
          <a href="#overview">Overview</a>
          <a href="#deploy">Deploy</a>
          <a href="#workloads">Workloads</a>
          <a href="#reports">Reports</a>
          <a href="#observability">Observability</a>
          <a href="#alerts">Alerts</a>
          <a href="#logs">Logs</a>
        </nav>

        <button className="refresh-button" type="button" onClick={handleRefresh} title="Refresh dashboard">
          <RefreshCw size={16} />
          Refresh
        </button>

        {isBrowserFallback && (
          <button className="reset-button" type="button" onClick={handleResetSandbox} title="Restore sample services">
            <RotateCcw size={16} />
            Reset sandbox
          </button>
        )}

        <nav className="service-list" aria-label="Deployed services">
          <div className="sidebar-label">Service Fleet</div>
          {deployments.map((deployment) => (
            <button
              className={`service-item ${deployment.name === selectedDeployment?.name ? "active" : ""}`}
              key={deployment.name}
              onClick={() => selectDeployment(deployment.name)}
              type="button"
            >
              <span className="status-dot" style={{ background: STATUS_THEME[deployment.status].color }} />
              <span>
                <strong>{deployment.name}</strong>
                <small>{deployment.namespace} / {deployment.replicas} replicas</small>
              </span>
              <StatusPill status={deployment.status} />
            </button>
          ))}
          {!deployments.length && !isLoading && (
            <div className="empty-service">
              <Server size={18} />
              <span>No active services</span>
            </div>
          )}
        </nav>
      </aside>

      <main className="main-panel">
        <header className="command-header">
          <div>
            <span className="eyebrow">Local Kubernetes Console</span>
            <h1>CloudWatch-style visibility for local apps.</h1>
            <p>Deploy lightweight services to local Kubernetes, watch rollout health, inspect metrics, stream logs, and keep audit history in one polished control plane.</p>
          </div>
          <div className="header-actions" aria-label="External operations tools">
            {!isLocalApi ? (
              <>
                <a href={REPOSITORY_URL} target="_blank" rel="noreferrer">
                  <GitBranch size={16} />
                  GitHub
                  <ExternalLink size={14} />
                </a>
                <a href={`${REPOSITORY_URL}/blob/main/docs/ARCHITECTURE.md`} target="_blank" rel="noreferrer">
                  <GitBranch size={16} />
                  Architecture
                  <ExternalLink size={14} />
                </a>
                <a href={`${REPOSITORY_URL}#main-api-endpoints`} target="_blank" rel="noreferrer">
                  <Terminal size={16} />
                  API Contract
                  <ExternalLink size={14} />
                </a>
              </>
            ) : (
              <>
                {LOCAL_TOOLS.map((tool) => (
                  <a href={tool.href} target="_blank" rel="noreferrer" key={tool.label}>
                    {tool.icon}
                    {tool.label}
                    <ExternalLink size={14} />
                  </a>
                ))}
              </>
            )}
          </div>
        </header>

        {isDemoMode && <DemoModeBanner />}

        {error && <div className="error-banner">{error}</div>}
        {observabilityError && <div className="error-banner">Observability unavailable: {observabilityError}</div>}

        <section className="panel project-panel" id="projects" aria-label="Project workspace">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Project Workspace</span>
              <h2>Manage local and repository projects</h2>
              <p>Scope local deployment history or register a public GitHub repository for read-only analysis.</p>
            </div>
            <span className="data-source">{isBrowserFallback ? "Browser-local demo data" : "ClueStack persistence"}</span>
          </div>
          <div className="project-workspace-grid">
            <div className="project-selector-card">
              <label>
                Active local project
                <select
                  aria-label="Active local project"
                  value={selectedProjectId}
                  disabled={!localProjects.length}
                  onChange={(event) => {
                    projectDataRequest.current += 1;
                    setDeployments([]);
                    setReports([]);
                    setReportError("");
                    selectProject(event.target.value);
                    setError("");
                  }}
                >
                  {localProjects.map((project) => (
                    <option value={project.id} key={project.id}>{project.name}</option>
                  ))}
                </select>
              </label>
              <div className="project-summary">
                <strong>{selectedProject?.name ?? "No local project available"}</strong>
                <span>{selectedProject ? `${selectedProject.slug} · ${selectedProject.default_namespace}` : "Project data has not loaded."}</span>
                <code>{selectedProject ? shortIdentifier(selectedProject.id) : "—"}</code>
              </div>
              {repositoryProjects.length > 0 && (
                <p className="project-boundary-note">
                  {repositoryProjects.length} repository-mode project{repositoryProjects.length === 1 ? " is" : "s are"} available for read-only source analysis below. Analysis never executes repository code.
                </p>
              )}
            </div>
            <form className="project-create-form" onSubmit={handleCreateProject}>
              <div>
                <strong>Create local project</strong>
                <span>Use a project to keep deployment and report history grouped.</span>
              </div>
              <label>
                Project name
                <input
                  aria-label="Project name"
                  required
                  minLength={2}
                  maxLength={80}
                  value={projectForm.name}
                  onChange={(event) => setProjectForm({ ...projectForm, name: event.target.value })}
                  placeholder="Payments Lab"
                />
              </label>
              <label>
                Slug <span>(optional)</span>
                <input
                  aria-label="Project slug"
                  minLength={2}
                  maxLength={63}
                  pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?"
                  value={projectForm.slug}
                  onChange={(event) => setProjectForm({ ...projectForm, slug: event.target.value })}
                  placeholder="payments-lab"
                />
              </label>
              <button type="submit" disabled={isCreatingProject}>
                {isCreatingProject ? "Creating…" : "Create project"}
              </button>
            </form>
            <form className="project-create-form github-project-form" onSubmit={handleRegisterGitHubProject}>
              <div>
                <strong>Register public GitHub repository</strong>
                <span>Stores repository metadata for read-only analysis. No credentials or private repository access.</span>
              </div>
              <label>
                Project name
                <input
                  aria-label="GitHub project name"
                  required
                  minLength={2}
                  maxLength={80}
                  value={githubProjectForm.name}
                  onChange={(event) => {
                    setGitHubProjectForm({ ...githubProjectForm, name: event.target.value });
                    setGitHubProjectError("");
                    setGitHubProjectSuccess("");
                  }}
                  placeholder="Catalog Repository"
                />
              </label>
              <label>
                Repository URL
                <input
                  aria-label="GitHub repository URL"
                  type="url"
                  required
                  maxLength={255}
                  value={githubProjectForm.repositoryUrl}
                  onChange={(event) => {
                    setGitHubProjectForm({ ...githubProjectForm, repositoryUrl: event.target.value });
                    setGitHubProjectError("");
                    setGitHubProjectSuccess("");
                  }}
                  placeholder="https://github.com/owner/repository"
                />
              </label>
              <label>
                Description <span>(optional)</span>
                <input
                  aria-label="GitHub project description"
                  maxLength={500}
                  value={githubProjectForm.description}
                  onChange={(event) => {
                    setGitHubProjectForm({ ...githubProjectForm, description: event.target.value });
                    setGitHubProjectError("");
                    setGitHubProjectSuccess("");
                  }}
                  placeholder="Public service repository"
                />
              </label>
              <label>
                Default namespace
                <input
                  aria-label="GitHub project default namespace"
                  required
                  minLength={2}
                  maxLength={63}
                  pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?"
                  value={githubProjectForm.defaultNamespace}
                  onChange={(event) => {
                    setGitHubProjectForm({ ...githubProjectForm, defaultNamespace: event.target.value });
                    setGitHubProjectError("");
                    setGitHubProjectSuccess("");
                  }}
                />
              </label>
              <button type="submit" disabled={isBrowserFallback || isRegisteringGitHubProject}>
                {isRegisteringGitHubProject ? "Registering…" : "Register repository"}
              </button>
            </form>
          </div>
          {projectError && <div className="inline-error">Project data unavailable: {projectError}</div>}
          {githubProjectError && <div className="inline-error" role="alert">Repository registration failed: {githubProjectError}</div>}
          {githubProjectSuccess && <div className="inline-success" role="status">{githubProjectSuccess}</div>}
        </section>

        <section className="panel cluster-overview-panel" id="kubernetes-cluster" aria-label="Kubernetes Cluster Overview">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Kubernetes</span>
              <h2><Server size={19} /> Cluster Overview</h2>
              <p>Read-only cluster inventory for an explicitly selected kubeconfig context.</p>
            </div>
            <span className="data-source">
              {clusterOverview ? `Live · ${clusterOverview.context}` : "No cluster selected"}
            </span>
          </div>

          <div className="cluster-toolbar">
            <label>
              Kubernetes context
              <select
                aria-label="Kubernetes context"
                value={selectedKubernetesContext}
                disabled={!kubernetesContexts?.enabled || isClusterOverviewLoading}
                onChange={(event) => {
                  clusterOverviewRequest.current += 1;
                  setSelectedKubernetesContext(event.target.value);
                  setClusterOverview(null);
                  setClusterOverviewError("");
                  setIsClusterOverviewLoading(false);
                }}
              >
                <option value="">Select a context</option>
                {kubernetesContexts?.contexts.map((context) => (
                  <option value={context} key={context}>{context}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={handleClusterOverviewRefresh}
              disabled={!canRefreshCluster(
                selectedKubernetesContext,
                isClusterOverviewLoading,
                Boolean(kubernetesContexts?.enabled),
              )}
            >
              <RefreshCw size={16} />
              {isClusterOverviewLoading ? "Refreshing…" : "Refresh cluster"}
            </button>
            <div className="cluster-context-note">
              <strong>Selected:</strong> {selectedKubernetesContext || "None"}
              {kubernetesContexts?.current_context && (
                <span>Current kubectl context: {kubernetesContexts.current_context}</span>
              )}
            </div>
          </div>

          {clusterViewState === "loading" && (
            <div className="cluster-state" role="status">Loading Kubernetes context information…</div>
          )}
          {clusterViewState === "disabled" && (
            <div className="cluster-state warning-state">
              {kubernetesContexts?.message || "Kubernetes access is disabled. Enable it explicitly in backend configuration."}
            </div>
          )}
          {clusterViewState === "error" && (
            <div className="inline-error" role="alert">Cluster overview unavailable: {clusterOverviewError}</div>
          )}
          {clusterViewState === "empty" && (
            <div className="cluster-state">No safe Kubernetes contexts were found in the backend kubeconfig.</div>
          )}
          {clusterViewState === "idle" && kubernetesContexts?.enabled && (
            <div className="cluster-state">Select a context to connect. ClueStack will not change your global kubectl context.</div>
          )}

          {clusterViewState === "success" && clusterOverview && (
            <div className="cluster-overview-content">
              <div className="cluster-stat-grid">
                <ClusterStat label="Connection" value="Connected" detail={clusterOverview.api_server || "API endpoint unavailable"} />
                <ClusterStat label="Kubernetes" value={clusterOverview.server_version || "Unknown"} detail={`Context: ${clusterOverview.context}`} />
                <ClusterStat label="Nodes" value={`${clusterOverview.ready_nodes}/${clusterOverview.total_nodes} ready`} detail={`${clusterOverview.not_ready_nodes} NotReady`} />
                <ClusterStat label="Pods" value={`${clusterOverview.pods.running}/${clusterOverview.pods.total} running`} detail={`${clusterOverview.pods.pending} pending · ${clusterOverview.pods.failed} failed`} />
                <ClusterStat
                  label="CPU usage"
                  value={clusterOverview.resource_usage.available && clusterOverview.resource_usage.cpu_cores !== null
                    ? `${clusterOverview.resource_usage.cpu_cores.toFixed(3)} cores`
                    : "Unavailable"}
                  detail={clusterOverview.resource_usage.available
                    ? `${clusterOverview.resource_usage.nodes_reporting} node(s) reporting`
                    : clusterOverview.resource_usage.message || "Metrics API unavailable"}
                />
                <ClusterStat
                  label="Memory usage"
                  value={clusterOverview.resource_usage.available && clusterOverview.resource_usage.memory_bytes !== null
                    ? formatBytes(clusterOverview.resource_usage.memory_bytes)
                    : "Unavailable"}
                  detail="Aggregate node usage from metrics.k8s.io"
                />
              </div>

              <div className="cluster-table-grid">
                <div>
                  <h3>Nodes</h3>
                  <div className="table-wrap">
                    <table className="cluster-table">
                      <thead><tr><th>Node</th><th>Roles</th><th>Version</th><th>Ready</th><th>Condition</th></tr></thead>
                      <tbody>
                        {clusterOverview.nodes.map((node) => {
                          const readyCondition = node.conditions.find((condition) => condition.type === "Ready");
                          return (
                            <tr key={node.name}>
                              <td>{node.name}</td>
                              <td>{node.roles.join(", ") || "worker"}</td>
                              <td>{node.kubernetes_version || "Unknown"}</td>
                              <td><span className={`cluster-health ${node.ready ? "ready" : "not-ready"}`}>{node.ready ? "Ready" : "NotReady"}</span></td>
                              <td>{readyCondition?.reason || "Not reported"}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div>
                  <h3>Pods by namespace</h3>
                  <div className="table-wrap">
                    <table className="cluster-table">
                      <thead><tr><th>Namespace</th><th>Total</th><th>Running</th><th>Pending</th><th>Failed</th></tr></thead>
                      <tbody>
                        {clusterOverview.pods.namespaces.map((namespace) => (
                          <tr key={namespace.namespace}>
                            <td>{namespace.namespace}</td>
                            <td>{namespace.total}</td>
                            <td>{namespace.running}</td>
                            <td>{namespace.pending}</td>
                            <td>{namespace.failed}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
              <p className="cluster-refreshed">Last refreshed: {new Date(clusterOverview.refreshed_at).toLocaleString()}</p>
            </div>
          )}
        </section>

        <section className="panel repository-analysis-panel" id="local-repository-analysis" aria-label="Local repository analysis">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Local Project Analysis</span>
              <h2>Inspect a local project folder</h2>
              <p>Your browser submits only bounded, recognized text configuration. ClueStack does not receive an absolute path, archive, source tree, or credential files.</p>
            </div>
            <span className="data-source">{isDemoMode ? "Unavailable in Demo Mode" : "Browser-selected files"}</span>
          </div>
          <form className="repository-analysis-form local-analysis-form" onSubmit={handleLocalRepositoryAnalysis}>
            <label>
              Local project
              <select aria-label="Local analysis project" value={selectedProject?.id ?? ""} onChange={(event) => selectProject(event.target.value)}>
                {localProjects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}
              </select>
            </label>
            <label>
              Project folder
              <input
                aria-label="Local project folder"
                type="file"
                multiple
                ref={(node) => {
                  localFolderInputRef.current = node;
                  if (node) {
                    node.setAttribute("webkitdirectory", "");
                    node.setAttribute("directory", "");
                  }
                }}
                disabled={isDemoMode || isAnalyzingLocalRepository}
                onChange={handleLocalFolderSelection}
              />
            </label>
            <button
              type="submit"
              disabled={isDemoMode || !selectedProject || !localFolderLabel || Boolean(localRepositoryAnalysisError) || isAnalyzingLocalRepository || isAssessingLocalReadiness}
            >
              <HardDrive size={17} />
              {isAnalyzingLocalRepository ? "Analyzing…" : "Analyze selected folder"}
            </button>
          </form>
          {localFolderLabel && (
            <div className="repository-identity">
              <div>
                <strong>{localFolderLabel}</strong>
                <span>{localSelectionSummary}</span>
              </div>
              <button
                type="button"
                onClick={handleLocalReadinessAssessment}
                disabled={isDemoMode || isAssessingLocalReadiness || isAnalyzingLocalRepository || !localRepositoryAnalysis}
              >
                <ShieldCheck size={16} />
                {isAssessingLocalReadiness ? "Assessing…" : "Assess readiness"}
              </button>
            </div>
          )}
          {localRepositoryAnalysisError && <div className="inline-error">Analysis failed: {localRepositoryAnalysisError}</div>}
          {localAnalysisViewState === "loading" && (
            <div className="empty-analysis" role="status">
              Reading recognized local configuration… {localAnalysisProgress}/{localRepositoryFiles.length} files
            </div>
          )}
          {localAnalysisViewState === "success" && localRepositoryAnalysis && <AnalysisResultDetails result={localRepositoryAnalysis} />}
          {localRepositoryReadinessError && <div className="inline-error">Readiness assessment failed: {localRepositoryReadinessError}</div>}
          {localReadinessViewState === "loading" && <div className="empty-analysis" role="status">Assessing persisted local evidence…</div>}
          {localReadinessViewState === "success" && localRepositoryReadiness && <ReadinessResultDetails result={localRepositoryReadiness} />}
          {!localFolderLabel && !localRepositoryAnalysisError && (
            <div className="empty-analysis">Select a folder to inspect recognized configuration. No project code is executed.</div>
          )}
        </section>

        <section className="panel repository-analysis-panel" id="repository-analysis" aria-label="Public GitHub repository analysis">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Repository Mode</span>
              <h2>Analyze public GitHub configuration</h2>
              <p>ClueStack reads bounded configuration evidence through the public GitHub API. It does not clone, build, or execute repository code.</p>
            </div>
            <span className="data-source">{isDemoMode ? "Unavailable in Demo Mode" : "GitHub public API"}</span>
          </div>
          <form className="repository-analysis-form" onSubmit={handleRepositoryAnalysis}>
            <label>
              Repository project
              <select
                aria-label="Repository project"
                value={selectedRepositoryProject?.id ?? ""}
                disabled={!repositoryProjects.length || isAnalyzingRepository}
                onChange={(event) => {
                  selectRepositoryProject(event.target.value);
                }}
              >
                {repositoryProjects.map((project) => (
                  <option value={project.id} key={project.id}>{project.name}</option>
                ))}
              </select>
            </label>
            <label>
              Branch <span>(default branch when blank)</span>
              <input
                aria-label="Repository branch"
                maxLength={255}
                value={repositoryBranch}
                onChange={(event) => setRepositoryBranch(event.target.value)}
                placeholder="main"
              />
            </label>
            <button
              type="submit"
              disabled={!selectedRepositoryProject || isAnalyzingRepository || isAssessingReadiness || !remoteAnalysisAllowed}
            >
              <GitBranch size={17} />
              {isAnalyzingRepository ? "Analyzing…" : "Analyze repository"}
            </button>
          </form>
          {!repositoryProjects.length && (
            <div className="empty-analysis">No public GitHub repository project exists yet. Create one through the project API before analyzing it.</div>
          )}
          {selectedRepositoryProject && (
            <div className="repository-identity">
              <div>
                <strong>{selectedRepositoryProject.name}</strong>
                <a href={selectedRepositoryProject.repository_url ?? "#"} target="_blank" rel="noreferrer">
                  {selectedRepositoryProject.repository_url}
                  <ExternalLink size={13} />
                </a>
              </div>
              <button
                type="button"
                onClick={handleReadinessAssessment}
                disabled={isAssessingReadiness || isAnalyzingRepository || !remoteAnalysisAllowed}
              >
                <ShieldCheck size={16} />
                {isAssessingReadiness ? "Assessing…" : "Assess readiness"}
              </button>
            </div>
          )}
          {repositoryAnalysisError && <div className="inline-error">Analysis failed: {repositoryAnalysisError}</div>}
          {analysisViewState === "loading" && (
            <div className="empty-analysis" role="status">Retrieving bounded repository evidence from GitHub…</div>
          )}
          {repositoryAnalysis && (
            <div className="analysis-results" aria-live="polite">
              <div className="analysis-summary">
                <span className={`analysis-status ${repositoryAnalysis.retrieval_status}`}>{repositoryAnalysis.retrieval_status}</span>
                <strong>{repositoryAnalysis.source_label}</strong>
                {repositoryAnalysis.branch && repositoryAnalysis.commit_sha && (
                  <span>{repositoryAnalysis.branch} · <code>{repositoryAnalysis.commit_sha.slice(0, 12)}</code></span>
                )}
                <span>{repositoryAnalysis.files_retrieved} files · {repositoryAnalysis.content_bytes_retrieved.toLocaleString()} bytes</span>
              </div>
              <div className="analysis-findings">
                {repositoryAnalysis.findings.map((finding) => (
                  <article className="analysis-finding" key={finding.category}>
                    <div>
                      <strong>{formatOperation(finding.category)}</strong>
                      <span className={`finding-status ${finding.status}`}>{finding.status}</span>
                    </div>
                    <p>{finding.values.length ? finding.values.join(", ") : finding.detail}</p>
                    {finding.evidence_paths.length > 0 && (
                      <ul>
                        {finding.evidence_paths.map((path) => <li key={path}><code>{path}</code></li>)}
                      </ul>
                    )}
                  </article>
                ))}
              </div>
              {repositoryAnalysis.missing_prerequisites.length > 0 && (
                <div className="analysis-notes">
                  <strong>Missing prerequisites</strong>
                  <ul>{repositoryAnalysis.missing_prerequisites.map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
              )}
              {repositoryAnalysis.warnings.length > 0 && (
                <div className="analysis-notes warning">
                  <strong>Incomplete or limited evidence</strong>
                  <ul>{repositoryAnalysis.warnings.map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
              )}
              <small>Analysis report: <code>{shortIdentifier(repositoryAnalysis.report_id)}</code> · {relativeTime(repositoryAnalysis.analyzed_at)}</small>
            </div>
          )}
          {readinessViewState === "loading" && (
            <div className="empty-analysis" role="status">Assessing persisted repository evidence…</div>
          )}
          {readinessViewState === "idle" && selectedRepositoryProject && (
            <div className="empty-analysis">
              Readiness has not been assessed for this project. Run repository analysis first, then assess its persisted evidence.
            </div>
          )}
          {repositoryReadinessError && (
            <div className="inline-error">Readiness assessment failed: {repositoryReadinessError}</div>
          )}
          {repositoryReadiness && (
            <div className="readiness-results" aria-live="polite">
              <div className="readiness-heading">
                <div>
                  <span className="section-kicker">Evidence-Based Readiness</span>
                  <h3>Repository readiness</h3>
                </div>
                <span className={`readiness-status ${repositoryReadiness.overall_status}`}>
                  {formatOperation(repositoryReadiness.overall_status)}
                </span>
              </div>
              <div className="readiness-grid">
                {repositoryReadiness.dimensions.map((dimension) => (
                  <article className="readiness-card" key={dimension.id}>
                    <div>
                      <strong>{formatOperation(dimension.id)}</strong>
                      <span className={`readiness-status ${dimension.status}`}>{formatOperation(dimension.status)}</span>
                    </div>
                    <p>{dimension.explanation}</p>
                    {dimension.findings.length > 0 && (
                      <div className="readiness-findings">
                        {dimension.findings.map((finding) => (
                          <div className={`readiness-finding ${finding.severity}`} key={finding.id}>
                            <span>{finding.required ? "Required" : finding.severity}</span>
                            <p>{finding.description}</p>
                            {finding.evidence_paths.map((path) => <code key={path}>{path}</code>)}
                          </div>
                        ))}
                      </div>
                    )}
                    {dimension.missing_prerequisites.length > 0 && (
                      <ReadinessList title="Missing prerequisites" items={dimension.missing_prerequisites} />
                    )}
                    {dimension.warnings.length > 0 && (
                      <ReadinessList title="Warnings and recommendations" items={dimension.warnings} />
                    )}
                    {dimension.unknown_conditions.length > 0 && (
                      <ReadinessList title="Unknown or unverified" items={dimension.unknown_conditions} />
                    )}
                  </article>
                ))}
              </div>
              <small>
                Assessment report: <code>{shortIdentifier(repositoryReadiness.report_id)}</code>
                {" · "}Analysis report: <code>{shortIdentifier(repositoryReadiness.analysis_report_id)}</code>
                {" · "}{relativeTime(repositoryReadiness.assessed_at)}
              </small>
            </div>
          )}
        </section>

        <section className="panel repository-analysis-panel" id="container-build" aria-label="Container build engine">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Controlled Build</span>
              <h2>Container build eligibility</h2>
              <p>Builds require matching analysis and readiness evidence, an immutable source snapshot, explicit approval, and a verified isolated worker. Static readiness is never presented as build success.</p>
            </div>
            <span className="data-source">{isDemoMode ? "Unavailable in Demo Mode" : "Execution disabled unless isolated"}</span>
          </div>
          <div className="repository-analysis-form">
            <label>
              Project
              <select
                aria-label="Container build project"
                value={buildProjectId}
                disabled={isDemoMode || isValidatingBuild || isBuildingContainer}
                onChange={(event) => void loadBuildEvidence(event.target.value)}
              >
                <option value="">Select project</option>
                {projects.filter((project) => !project.archived_at).map((project) => (
                  <option value={project.id} key={project.id}>{project.name}</option>
                ))}
              </select>
            </label>
            <label>
              Image name
              <input value={buildForm.imageName} onChange={(event) => setBuildForm({ ...buildForm, imageName: event.target.value })} />
            </label>
            <label>
              Image tag
              <input value={buildForm.imageTag} onChange={(event) => setBuildForm({ ...buildForm, imageTag: event.target.value })} />
            </label>
          </div>
          <div className="snapshot-panel" aria-label="Immutable source snapshots">
            <div className="readiness-heading">
              <div>
                <span className="section-kicker">Exact Build Input</span>
                <h3>Source snapshots</h3>
              </div>
              <button
                type="button"
                disabled={isDemoMode || !buildProjectId || isCreatingSnapshot}
                onClick={() => void handleCreateSourceSnapshot()}
              >
                <HardDrive size={16} /> {isCreatingSnapshot ? "Creating…" : "Create snapshot"}
              </button>
            </div>
            {isDemoMode && <div className="empty-analysis">Demo Mode never fabricates source snapshots or integrity checks.</div>}
            {!isDemoMode && buildProjectId && sourceSnapshots.length === 0 && !sourceSnapshotError && (
              <div className="empty-analysis">No immutable source snapshot exists for this project.</div>
            )}
            {sourceSnapshotError && <div className="inline-error">Snapshot workflow: {sourceSnapshotError}</div>}
            {sourceSnapshots.length > 0 && (
              <div className="snapshot-list">
                {sourceSnapshots.map((snapshot) => (
                  <article className="snapshot-card" key={snapshot.id}>
                    <div>
                      <strong>{formatOperation(snapshot.source)} · {formatOperation(snapshot.status)}</strong>
                      <span>{snapshot.source_identity.commit_sha
                        ? `Commit ${shortIdentifier(snapshot.source_identity.commit_sha)}`
                        : `Submission ${shortIdentifier(snapshot.source_identity.submission_id ?? "")}`}</span>
                    </div>
                    <span>{snapshot.file_count} files · {snapshot.total_content_size.toLocaleString()} bytes</span>
                    <span>
                      {snapshot.exclusions.reduce((total, item) => total + item.count, 0)} excluded
                      {snapshot.source_identity.resolved_reference ? ` · ref ${snapshot.source_identity.resolved_reference}` : ""}
                    </span>
                    <span>Integrity: {snapshot.integrity_verified ? "verified" : "not verified"}</span>
                    <code>{snapshot.integrity_hash ?? "Integrity hash unavailable"}</code>
                    <small>
                      Snapshot {shortIdentifier(snapshot.id)} · created {relativeTime(snapshot.created_at)}
                      {snapshot.expires_at ? ` · expires ${relativeTime(snapshot.expires_at)}` : ""}
                    </small>
                    {snapshot.error && <div className="inline-error">{snapshot.error}</div>}
                    <div className="snapshot-actions">
                      <button type="button" disabled={snapshot.status !== "available"} onClick={() => void handleVerifySourceSnapshot(snapshot.id)}>
                        <ShieldCheck size={14} /> Verify
                      </button>
                      <button type="button" disabled={["creating", "deleted"].includes(snapshot.status)} onClick={() => void handleDeleteSourceSnapshot(snapshot.id)}>
                        <Trash2 size={14} /> Delete
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
          <div className="repository-analysis-form">
            <label>
              Dockerfile path
              <input value={buildForm.dockerfilePath} onChange={(event) => setBuildForm({ ...buildForm, dockerfilePath: event.target.value })} />
            </label>
            <button type="button" disabled={isDemoMode || !buildProjectId || isValidatingBuild || isBuildingContainer} onClick={() => void handleBuildValidation()}>
              <ShieldCheck size={16} /> {isValidatingBuild ? "Validating…" : "Validate eligibility"}
            </button>
          </div>
          {buildEligibility && (
            <div className="analysis-results" aria-live="polite">
              <div className="analysis-summary">
                <span className={`finding-status ${buildEligibility.eligible ? "detected" : "missing"}`}>
                  {buildEligibility.eligible ? "Eligible" : "Blocked"}
                </span>
                <strong>{buildEligibility.image_name}:{buildEligibility.image_tag}</strong>
                <span>{buildEligibility.source_snapshot_id ? `Snapshot ${shortIdentifier(buildEligibility.source_snapshot_id)}` : "No immutable snapshot"}</span>
                <span>Worker: {formatOperation(buildEligibility.worker_availability)}</span>
                <span>Network: {formatOperation(buildEligibility.network_policy)}</span>
              </div>
              <div className="analysis-findings">
                {buildEligibility.findings.map((finding) => (
                  <article className="analysis-finding" key={finding.code}>
                    <div><strong>{formatOperation(finding.code)}</strong><span>{finding.status}</span></div>
                    <p>{finding.message}</p>
                    {finding.evidence_paths.map((path) => <code key={path}>{path}</code>)}
                  </article>
                ))}
              </div>
              <label className="build-approval">
                <input type="checkbox" checked={buildApproved} onChange={(event) => setBuildApproved(event.target.checked)} />
                I explicitly approve this build request. Approval does not imply deployment.
              </label>
              <button type="button" disabled={!buildEligibility.eligible || !buildApproved || isBuildingContainer} onClick={() => void handleContainerBuild()}>
                <Rocket size={16} /> {isBuildingContainer ? "Building…" : "Submit approved build"}
              </button>
            </div>
          )}
          {containerBuildError && <div className="inline-error">Build workflow: {containerBuildError}</div>}
          {containerBuild && (
            <div className="analysis-results" aria-live="polite">
              <div className="analysis-summary">
                <strong>Status: {formatOperation(containerBuild.status)}</strong>
                <span>{containerBuild.image_name}:{containerBuild.image_tag}</span>
                {containerBuild.image_digest && <code>{containerBuild.image_digest}</code>}
                <span>{formatDuration(containerBuild.duration_ms)}</span>
                {containerBuild.result_validation && <span>Result validation: {formatOperation(containerBuild.result_validation)}</span>}
                {containerBuild.cleanup_status && <span>Cleanup: {formatOperation(containerBuild.cleanup_status)}</span>}
              </div>
              {containerBuild.failure_reason && <div className="inline-error">{containerBuild.failure_reason}</div>}
              {containerBuildLogs && <pre className="build-logs">{containerBuildLogs}</pre>}
              <small>Build report: <code>{shortIdentifier(containerBuild.report_id)}</code>. No deployment was performed.</small>
            </div>
          )}
          {!buildEligibility && !containerBuildError && (
            <div className="empty-analysis">Select a project and validate its persisted analysis/readiness evidence before requesting a build.</div>
          )}
        </section>

        <section className={`hero-console ${healthTone}`} id="overview" aria-label="ClueStack overview">
          <div className="hero-copy">
            <span className="section-kicker">Mission Control</span>
            <h2>{selectedDeployment ? `${selectedDeployment.name} is under watch` : "Start with a lightweight container image"}</h2>
            <p>
              ClueStack turns your local Kubernetes lab into a clean deployment and observability workspace for
              testing apps before they move anywhere near cloud infrastructure.
            </p>
            <div className="hero-badges" aria-label="Platform capabilities">
              <span>☸ Local Kubernetes</span>
              <span>📈 Prometheus metrics</span>
              <span>📜 Alloy + Loki logs</span>
              <span>🧾 Audit trail</span>
            </div>
          </div>
          <div className="hero-score-card">
            <div
              className="score-ring"
              style={{ background: `conic-gradient(#3ddc97 ${(displayedHealthScore ?? 0) * 3.6}deg, #26313d 0deg)` }}
              aria-label={displayedHealthScore === null ? "Workload health not verified" : `Health score ${displayedHealthScore}%`}
            >
              <span>{displayedHealthScore === null ? "—" : `${displayedHealthScore}%`}</span>
            </div>
            <strong>Local health score</strong>
            <small>{displayedHealthScore === null ? "Requires live Kubernetes and Prometheus data" : `${signalCount}/5 platform checks healthy`}</small>
          </div>
          <div className="hero-mini-grid">
            <MiniStat label="Services" value={deployments.length} />
            <MiniStat label="Requested replicas" value={summary.requestedReplicas} />
            <MiniStat label="Rollouts" value={activeRolloutCount} />
            <MiniStat label="Alerts" value={firingAlerts} />
          </div>
        </section>

        <section className="pipeline-strip" aria-label="Delivery pipeline">
          {pipelineSteps.map((step) => (
            <article className={`pipeline-step ${step.state}`} key={step.label}>
              <div className="pipeline-icon">{step.icon}</div>
              <div>
                <span>{step.label}</span>
                <strong>{step.value}</strong>
              </div>
            </article>
          ))}
        </section>

        <section className="status-grid" aria-label="Deployment status summary">
          <SummaryCard icon={<ShieldCheck size={20} />} label="Health score" value={displayedHealthScore === null ? "Not verified" : `${displayedHealthScore}%`} tone="green" />
          <SummaryCard icon={<Server size={20} />} label="Running services" value={summary.Running} tone="blue" />
          <SummaryCard icon={<Layers size={20} />} label="Observed replicas" value={summary.activeReplicas} tone="violet" />
          <SummaryCard icon={<AlertTriangle size={20} />} label="Firing alerts" value={firingAlerts} tone="red" />
        </section>

        <section className="stack-panel" aria-label="ClueStack platform stack">
          <div className="panel-heading">
            <div>
              <span className="section-kicker">Stack Health</span>
              <h2>What the user sees in one dashboard</h2>
              <p>React stays as the main product UI. Grafana, Prometheus, Loki, and Alloy remain supporting tools for deeper inspection.</p>
            </div>
          </div>
          <div className="stack-grid">
            {platformSignals.map((signal) => (
              <article className={`stack-card ${signal.tone}`} key={signal.label}>
                <div className="stack-icon">{signal.icon}</div>
                <div>
                  <span>{signal.label}</span>
                  <strong>{signal.value}</strong>
                  <p>{signal.detail}</p>
                </div>
              </article>
            ))}
          </div>
        </section>

        <div className="dashboard-grid">
          <section className="panel service-overview">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Selected Service</span>
                <h2>{selectedDeployment?.name ?? "No service selected"}</h2>
                <p>{selectedDeployment?.message ?? "Deploy a service to populate the command center."}</p>
              </div>
              {selectedDeployment && <StatusPill status={selectedStatus} size="large" />}
            </div>

            <div className="service-facts">
              <Fact icon={<Layers size={17} />} label="Image" value={selectedDeployment?.image ?? "Waiting for deployment"} />
              <Fact icon={<GitBranch size={17} />} label="Project" value={selectedProject?.name ?? "Not selected"} />
              <Fact icon={<ShieldCheck size={17} />} label="Deployment ID" value={shortIdentifier(selectedDeployment?.id)} />
              <Fact icon={<Server size={17} />} label="Namespace" value={selectedDeployment?.namespace ?? "infrawatch"} />
              <Fact icon={<Zap size={17} />} label="Requested replicas" value={String(workloadDesired)} />
              <Fact
                icon={<CheckCircle2 size={17} />}
                label="Ready pods"
                value={workloadReady === null ? "Not observed" : `${workloadReady}/${workloadDesired}`}
              />
              <Fact icon={<Activity size={17} />} label="Available replicas" value={workloadAvailable === null ? "Not observed" : String(workloadAvailable)} />
              <Fact icon={<AlertTriangle size={17} />} label="Unavailable replicas" value={workloadUnavailable === null ? "Not observed" : String(workloadUnavailable)} />
              <Fact icon={<Clock size={17} />} label="Updated" value={selectedDeployment ? relativeTime(selectedDeployment.updated_at) : "Not available"} />
            </div>
            <div className="workload-health-strip" aria-label="Kubernetes workload health">
              <span>Workload source: {workload ? sourceLabel(workload.source) : "Waiting"}</span>
              <span>Updated: {hasObservedWorkload ? workload.updated_replicas ?? "Not observed" : "Not observed"}</span>
              <span>Observed generation: {hasObservedWorkload ? workload.observed_generation ?? "n/a" : "Not observed"}</span>
            </div>
            {workload?.pods.length ? (
              <div className="pod-health-list" aria-label="Pod health">
                {workload.pods.map((pod) => (
                  <div className={`pod-health ${pod.ready ? "ready" : "problem"}`} key={pod.name}>
                    <strong>{pod.name}</strong>
                    <span>{pod.phase} / {pod.ready ? "Ready" : pod.reason ?? "Not ready"}</span>
                    <small>{pod.restart_count} restart{pod.restart_count === 1 ? "" : "s"}</small>
                  </div>
                ))}
              </div>
            ) : selectedDeployment ? (
              <div className="telemetry-notice">
                {selectedIsSimulation
                  ? "No pod health was observed because this record represents manifest simulation only."
                  : "Pod-level details are not available yet for this workload."}
              </div>
            ) : null}
            {selectedDeployment?.last_failure && (
              <div className="rollout-failure">
                <AlertTriangle size={16} />
                <span>{selectedDeployment.last_failure}</span>
              </div>
            )}
          </section>

          <section className="panel deploy-panel" id="deploy">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Release Control</span>
                <h2>Deploy Service</h2>
              </div>
              <span className="data-source">{selectedProject?.name ?? "Select a project"}</span>
            </div>

            <form className="deploy-form" onSubmit={handleDeploy}>
              {isDemoMode && (
                <p className="sandbox-note">
                  Try any valid service name and container image. Changes are private to this device in browser Demo Mode.
                </p>
              )}
              <label>
                Service name
                <input
                  aria-label="Service name"
                  required
                  minLength={2}
                  maxLength={63}
                  pattern="[a-z0-9]([-a-z0-9]*[a-z0-9])?"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                  placeholder="service-name"
                />
              </label>
              <label>
                Container image
                <input
                  aria-label="Container image"
                  required
                  minLength={3}
                  maxLength={255}
                  value={form.image}
                  onChange={(event) => setForm({ ...form, image: event.target.value })}
                  placeholder="registry/image:tag"
                />
              </label>
              <div className="deploy-number-row">
                <label>
                  Replicas
                  <input
                    aria-label="Replica count"
                    type="number"
                    min={1}
                    max={10}
                    value={form.replicas}
                    onChange={(event) => setForm({ ...form, replicas: Number(event.target.value) })}
                  />
                </label>
                <label>
                  Port
                  <input
                    aria-label="Service port"
                    type="number"
                    min={1}
                    max={65535}
                    value={form.port}
                    onChange={(event) => setForm({ ...form, port: Number(event.target.value) })}
                  />
                </label>
              </div>
              <button type="submit" disabled={isDeploying || !selectedProject}>
                <Rocket size={17} />
                {isDeploying ? "Deploying" : "Deploy"}
              </button>
            </form>
          </section>

          <section className="panel flow-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Project Flow</span>
                <h2>How ClueStack works</h2>
              </div>
            </div>
            <div className="flow-list">
              {flowSteps.map((step, index) => (
                <article className="flow-step" key={step.title}>
                  <div className="flow-index">
                    <span>{index + 1}</span>
                    {step.icon}
                  </div>
                  <div>
                    <strong>{step.title}</strong>
                    <p>{step.body}</p>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <section className="panel telemetry-status-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Metrics</span>
                <h2>Prometheus-backed service telemetry</h2>
                <p>
                  {metrics.source === "prometheus"
                    ? "Showing real Prometheus data through the FastAPI metrics endpoint and Redis cache."
                    : metrics.source === "mock" || metrics.source === "browser sandbox"
                      ? "Showing fallback telemetry. This is not real Prometheus data."
                      : isObservabilityLoading
                        ? "Loading service telemetry from the backend."
                        : "No metric series is currently available for the selected service."}
                </p>
              </div>
              <span className="data-source">{isObservabilityLoading ? "Refreshing" : telemetryMode}</span>
            </div>
          </section>

          <MetricChart area="cpu-card" title="CPU Usage" value={hasMetricData ? `${telemetry.cpu.toFixed(2)} cores` : "No data"} data={chartData} dataKey="cpu" color="#4da3ff" icon={<Cpu size={18} />} />
          <MetricChart area="memory-card" title="Memory Usage" value={hasMetricData ? `${Math.round(telemetry.memory)} MB` : "No data"} data={chartData} dataKey="memory" color="#3ddc97" icon={<HardDrive size={18} />} />
          <MetricChart area="requests-card" title="Request Rate" value={hasMetricData ? `${telemetry.requests.toFixed(1)} rps` : "No data"} data={chartData} dataKey="requests" color="#b38cff" icon={<Activity size={18} />} />
          <MetricChart area="errors-card" title="Error Rate" value={hasMetricData ? `${telemetry.errors.toFixed(2)} rps` : "No data"} data={chartData} dataKey="errors" color="#ff6b7a" icon={<AlertTriangle size={18} />} />

          <section className="panel fleet-panel" id="workloads">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Deployment Inventory</span>
                <h2>Service Fleet</h2>
              </div>
              <span className="data-source">{deployments.length} records</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Deployment ID</th>
                    <th>Status</th>
                    <th>Observed / requested</th>
                    <th>Image</th>
                    <th>Updated</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {deployments.map((deployment) => (
                    <tr key={deployment.name}>
                      <td>
                        <button
                          className="table-service"
                          type="button"
                          onClick={() => selectDeployment(deployment.name)}
                        >
                          {deployment.name}
                        </button>
                      </td>
                      <td><code className="record-id">{shortIdentifier(deployment.id)}</code></td>
                      <td><StatusPill status={deployment.status} /></td>
                      <td>
                        {isManifestSimulation(deployment) || deployment.ready_replicas === null
                          ? `Not observed / ${deployment.replicas}`
                          : `${deployment.ready_replicas}/${deployment.replicas}`}
                      </td>
                      <td className="image-cell">{deployment.image}</td>
                      <td>{relativeTime(deployment.updated_at)}</td>
                      <td>
                        <div className="table-actions">
                          <button
                            className="table-action rollback"
                            type="button"
                            onClick={() => handleRollback(deployment.name)}
                            title="Rollback deployment"
                          >
                            <RefreshCw size={15} />
                          </button>
                          <button className="table-action" type="button" onClick={() => handleDelete(deployment.name)} title="Delete deployment">
                            <Trash2 size={15} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {!deployments.length && (
                    <tr>
                      <td colSpan={7} className="empty-row">No services have been deployed for this project yet.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel reports-panel" id="reports">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Deployment History</span>
                <h2>Project reports</h2>
                <p>Historical operation outcomes persisted for {selectedProject?.name ?? "the selected project"}.</p>
              </div>
              <span className="data-source">{isBrowserFallback ? `${reports.length} simulated reports` : `${reports.length} persisted reports`}</span>
            </div>
            {reportError ? (
              <div className="inline-error">Report history unavailable: {reportError}</div>
            ) : (
              <div className="table-wrap">
                <table className="reports-table">
                  <thead>
                    <tr>
                      <th>Operation</th>
                      <th>Outcome</th>
                      <th>Deployment</th>
                      <th>Target</th>
                      <th>Started</th>
                      <th>Duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reports.map((report) => {
                      const linkedDeployment = deployments.find((deployment) => deployment.id === report.deployment_id);
                      const evidenceName = typeof report.evidence.deployment_name === "string"
                        ? report.evidence.deployment_name
                        : null;
                      return (
                        <tr key={report.id}>
                          <td>
                            <strong className="report-operation">{formatOperation(report.operation)}</strong>
                            <small>{report.summary}</small>
                          </td>
                          <td><span className={`report-status ${report.status}`}>{report.status}</span></td>
                          <td>
                            <span>{linkedDeployment?.name ?? evidenceName ?? "Not associated"}</span>
                            <code className="record-id">{shortIdentifier(report.deployment_id)}</code>
                          </td>
                          <td>{formatOperation(report.target)}</td>
                          <td>{relativeTime(report.started_at)}</td>
                          <td>{formatDuration(report.duration_ms)}</td>
                        </tr>
                      );
                    })}
                    {!reports.length && !isLoading && (
                      <tr>
                        <td colSpan={6} className="empty-row">No deployment reports exist for this project yet.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel alerts-panel" id="alerts">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Alerts</span>
                <h2><AlertTriangle size={18} /> Alertmanager state</h2>
                <p>Prometheus rules feed Alertmanager; ClueStack only displays the current alert state.</p>
              </div>
              <span className="data-source">{alertSourceLabel}</span>
            </div>
            <div className="log-toolbar alert-toolbar">
              <label>
                Severity
                <select
                  aria-label="Filter alerts by severity"
                  value={alertSeverityFilter}
                  onChange={(event) => setAlertSeverityFilter(event.target.value)}
                >
                  <option value="">All severities</option>
                  {alertSeverityOptions.map((severity) => (
                    <option value={severity} key={severity}>{severity}</option>
                  ))}
                </select>
              </label>
              <label>
                Service
                <select
                  aria-label="Filter alerts by service"
                  value={alertServiceFilter}
                  onChange={(event) => setAlertServiceFilter(event.target.value)}
                >
                  <option value="">All services</option>
                  {alertServiceOptions.map((service) => (
                    <option value={service} key={service}>{service}</option>
                  ))}
                </select>
              </label>
              <button type="button" onClick={refreshAlerts}>
                <RefreshCw size={14} />
                Refresh
              </button>
            </div>
            {alertError && (
              <div className="log-state unavailable">
                Alertmanager is temporarily unavailable: {alertError}
              </div>
            )}
            {!alertError && alerts.source === "mock" && (
              <div className="log-state simulated">Showing simulated demo alerts, not Alertmanager data.</div>
            )}
            {!alertError && alerts.source === "browser sandbox" && (
              <div className="log-state simulated">Showing browser sandbox alerts, not Alertmanager data.</div>
            )}
            <div className="alert-list">
              {visibleAlerts.map((alert) => (
                <AlertCard alert={alert} key={alert.fingerprint} />
              ))}
              {!visibleAlerts.length && !alertError && (
                <div className="empty-log">
                  <CheckCircle2 size={18} />
                  <span>{alerts.alerts.length ? "No alerts match the current filters." : "No active alerts from Alertmanager."}</span>
                </div>
              )}
            </div>
          </section>

          <section className="panel logs-panel" id="logs">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Runtime Logs</span>
                <h2><Terminal size={18} /> Loki-backed Kubernetes logs</h2>
              </div>
              <span className="data-source">{sourceLabel(logs.source)}</span>
            </div>
            <div className="log-toolbar">
              <label>
                Search
                <input
                  aria-label="Search logs"
                  value={logSearch}
                  onChange={(event) => setLogSearch(event.target.value)}
                  placeholder="Filter log text"
                />
              </label>
              <label>
                Pod
                <select
                  aria-label="Filter logs by pod"
                  value={logPodFilter}
                  onChange={(event) => setLogPodFilter(event.target.value)}
                >
                  <option value="">All pods</option>
                  {logPodOptions.map((pod) => (
                    <option value={pod} key={pod}>{pod}</option>
                  ))}
                </select>
              </label>
              <label>
                Window
                <select
                  aria-label="Log time window"
                  value={logWindowMinutes}
                  onChange={(event) => setLogWindowMinutes(Number(event.target.value))}
                >
                  <option value={5}>Last 5m</option>
                  <option value={15}>Last 15m</option>
                  <option value={30}>Last 30m</option>
                  <option value={60}>Last 60m</option>
                </select>
              </label>
              <button type="button" onClick={handleRefresh} disabled={isObservabilityLoading}>
                <RefreshCw size={14} />
                Refresh
              </button>
            </div>
            {logError && (
              <div className="log-state unavailable">
                Loki logs are temporarily unavailable: {logError}
              </div>
            )}
            {!logError && logs.source === "mock" && (
              <div className="log-state simulated">Showing simulated backend logs, not Loki data.</div>
            )}
            {!logError && logs.source === "browser sandbox" && (
              <div className="log-state simulated">Showing browser sandbox logs, not Loki data.</div>
            )}
            <div className="log-viewer">
              {logs.lines.map((entry) => (
                <div className="log-line" key={`${entry.timestamp}-${entry.line}`}>
                  <time>{new Date(entry.timestamp).toLocaleTimeString()}</time>
                  <div>
                    <div className="log-context">
                      <span>{entry.pod ?? selectedDeployment?.name ?? "unknown pod"}</span>
                      {entry.container && <span>{entry.container}</span>}
                      {entry.namespace && <span>{entry.namespace}</span>}
                    </div>
                    <code>{entry.line}</code>
                  </div>
                </div>
              ))}
              {!logs.lines.length && !logError && (
                <div className="empty-log">
                  <Terminal size={18} />
                  <span>No logs found for the selected service, filters, and time window.</span>
                </div>
              )}
            </div>
          </section>

          <section className="panel audit-panel" id="observability">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Audit Trail</span>
                <h2><ShieldCheck size={18} /> Recent Actions</h2>
              </div>
              <span className="data-source">{auditLogs.length} events</span>
            </div>
            <div className="audit-list">
              {auditLogs.map((entry) => (
                <div className={`audit-entry ${auditTone(entry.status)}`} key={entry.id}>
                  <span className="audit-marker" />
                  <div>
                    <div className="audit-topline">
                      <strong>{formatAuditAction(entry.action)}</strong>
                      <time>{relativeTime(entry.created_at)}</time>
                    </div>
                    <p>{entry.message}</p>
                    <span>{entry.service ?? "platform"} / {entry.actor}</span>
                  </div>
                </div>
              ))}
              {!auditLogs.length && (
                <div className="empty-log">
                  <ShieldCheck size={18} />
                  <span>No audit events yet</span>
                </div>
              )}
            </div>
          </section>

          <section className="panel readiness-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">Platform Signals</span>
                <h2>Readiness</h2>
              </div>
            </div>
            <Signal label="Control plane" value={isDemoMode || usingApiFallback ? "Browser sandbox ready" : "FastAPI online"} state="ok" />
            <Signal label="Metrics path" value={telemetryMode} state="ok" />
            <Signal label="Log source" value={isBrowserFallback ? "Mock logs" : sourceLabel(logs.source)} state="ok" />
            <Signal label="Average CPU" value={`${telemetry.avgCpu.toFixed(2)} cores`} state="ok" />
            <Signal label="Peak memory" value={`${Math.round(telemetry.peakMemory)} MB`} state="ok" />
            <Signal
              label="Pending work"
              value={`${summary.Pending + summary.Deploying} rollout events`}
              state={summary.Pending + summary.Deploying ? "warn" : "ok"}
            />
          </section>
        </div>
      </main>
    </div>
  );
}

function AlertCard({ alert }: { alert: AlertSummary }) {
  const tone = alert.severity ? severityTone(alert.severity) : "unknown";
  return (
    <article className={`alert-card ${tone}`}>
      <div className="alert-card-topline">
        <div>
          <strong>{alert.alertname}</strong>
          <span>{alert.summary ?? "Prometheus alert is active"}</span>
        </div>
        <span className={`alert-state ${tone}`}>{alert.severity ?? alert.status}</span>
      </div>
      {alert.description && <p>{alert.description}</p>}
      <div className="alert-meta">
        <span>{alert.status}</span>
        {alert.service && <span>{alert.service}</span>}
        {alert.namespace && <span>{alert.namespace}</span>}
        {alert.pod && <span>{alert.pod}</span>}
        {alert.instance && <span>{alert.instance}</span>}
        {alert.starts_at && <time>{relativeTime(alert.starts_at)}</time>}
      </div>
    </article>
  );
}

function ReadinessList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="readiness-list">
      <strong>{title}</strong>
      <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
    </div>
  );
}

function AnalysisResultDetails({ result }: { result: RepositoryAnalysisResult }) {
  return (
    <div className="analysis-results" aria-live="polite">
      <div className="analysis-summary">
        <span className={`analysis-status ${result.retrieval_status}`}>{result.retrieval_status}</span>
        <strong>{result.source_label}</strong>
        <span>{result.source === "local_upload" ? "Local folder evidence" : "Public GitHub evidence"}</span>
        {result.branch && result.commit_sha && (
          <span>{result.branch} · <code>{result.commit_sha.slice(0, 12)}</code></span>
        )}
        <span>{result.files_retrieved} files · {result.content_bytes_retrieved.toLocaleString()} bytes</span>
      </div>
      <div className="analysis-findings">
        {result.findings.map((finding) => (
          <article className="analysis-finding" key={finding.category}>
            <div>
              <strong>{formatOperation(finding.category)}</strong>
              <span className={`finding-status ${finding.status}`}>{finding.status}</span>
            </div>
            <p>{finding.values.length ? finding.values.join(", ") : finding.detail}</p>
            {finding.evidence_paths.length > 0 && (
              <ul>{finding.evidence_paths.map((path) => <li key={path}><code>{path}</code></li>)}</ul>
            )}
          </article>
        ))}
      </div>
      {result.missing_prerequisites.length > 0 && (
        <div className="analysis-notes">
          <strong>Missing prerequisites</strong>
          <ul>{result.missing_prerequisites.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      )}
      {result.warnings.length > 0 && (
        <div className="analysis-notes warning">
          <strong>Incomplete or limited evidence</strong>
          <ul>{result.warnings.map((item) => <li key={item}>{item}</li>)}</ul>
        </div>
      )}
      <small>Analysis report: <code>{shortIdentifier(result.report_id)}</code> · {relativeTime(result.analyzed_at)}</small>
    </div>
  );
}

function ReadinessResultDetails({ result }: { result: RepositoryReadinessResult }) {
  return (
    <div className="readiness-results" aria-live="polite">
      <div className="readiness-heading">
        <div>
          <span className="section-kicker">Evidence-Based Readiness</span>
          <h3>Repository readiness</h3>
        </div>
        <span className={`readiness-status ${result.overall_status}`}>{formatOperation(result.overall_status)}</span>
      </div>
      <div className="readiness-grid">
        {result.dimensions.map((dimension) => (
          <article className="readiness-card" key={dimension.id}>
            <div>
              <strong>{formatOperation(dimension.id)}</strong>
              <span className={`readiness-status ${dimension.status}`}>{formatOperation(dimension.status)}</span>
            </div>
            <p>{dimension.explanation}</p>
            {dimension.findings.length > 0 && (
              <div className="readiness-findings">
                {dimension.findings.map((finding) => (
                  <div className={`readiness-finding ${finding.severity}`} key={finding.id}>
                    <span>{finding.required ? "Required" : finding.severity}</span>
                    <p>{finding.description}</p>
                    {finding.evidence_paths.map((path) => <code key={path}>{path}</code>)}
                  </div>
                ))}
              </div>
            )}
            {dimension.missing_prerequisites.length > 0 && <ReadinessList title="Missing prerequisites" items={dimension.missing_prerequisites} />}
            {dimension.warnings.length > 0 && <ReadinessList title="Warnings and recommendations" items={dimension.warnings} />}
            {dimension.unknown_conditions.length > 0 && <ReadinessList title="Unknown or unverified" items={dimension.unknown_conditions} />}
          </article>
        ))}
      </div>
      <small>
        Assessment report: <code>{shortIdentifier(result.report_id)}</code>
        {" · "}Analysis report: <code>{shortIdentifier(result.analysis_report_id)}</code>
        {" · "}{relativeTime(result.assessed_at)}
      </small>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="mini-stat">
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function SummaryCard({ icon, label, value, tone }: { icon: ReactNode; label: string; value: string | number; tone: string }) {
  return (
    <article className={`summary-card ${tone}`}>
      <div className="summary-icon">{icon}</div>
      <div>
        <strong>{value}</strong>
        <span>{label}</span>
      </div>
    </article>
  );
}

function ClusterStat({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="cluster-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function DemoModeBanner() {
  return (
    <section className="demo-mode-banner" role="note" aria-label="Demo Mode">
      <div className="demo-mode-title">
        <AlertTriangle size={22} aria-hidden="true" />
        <div>
          <strong>DEMO MODE</strong>
          <span>Browser sandbox</span>
        </div>
      </div>
      <p>
        This frontend-only sandbox keeps all changes in your browser and uses realistic simulated infrastructure data.
      </p>
      <div className="demo-capabilities" aria-label="Demo capabilities">
        <span className="real">Interactive UI</span>
        <span>Simulated Kubernetes</span>
        <span>Mock metrics & logs</span>
      </div>
    </section>
  );
}

function Fact({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="fact">
      <div className="fact-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function Signal({ label, value, state }: { label: string; value: string; state: "ok" | "warn" }) {
  return (
    <div className={`signal ${state}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function isManifestSimulation(deployment: DeploymentRecord) {
  return deployment.status === "Simulated"
    || (deployment.status === "Running"
      && (deployment.message.includes("Kubernetes execution is disabled (manifest simulation)")
        || deployment.message.includes("kubectl execution is disabled in Demo Mode")));
}

function StatusPill({ status, size = "default" }: { status: DeploymentStatus; size?: "default" | "large" }) {
  const theme = STATUS_THEME[status];
  return (
    <span className={`status-pill ${size}`} style={{ color: theme.color }}>
      {theme.icon}
      {theme.label}
    </span>
  );
}

function MetricChart({
  area,
  title,
  value,
  data,
  dataKey,
  color,
  icon,
}: {
  area: string;
  title: string;
  value: string;
  data: Record<string, string | number>[];
  dataKey: string;
  color: string;
  icon: ReactNode;
}) {
  return (
    <section className={`metric-card ${area}`}>
      <div className="metric-heading">
        <div>
          <span>{title}</span>
          <strong>{value}</strong>
        </div>
        <div className="metric-icon" style={{ color }}>{icon}</div>
      </div>
      {data.length ? (
        <ResponsiveContainer width="100%" height={170}>
          <AreaChart data={data}>
            <defs>
              <linearGradient id={`gradient-${dataKey}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.35} />
                <stop offset="95%" stopColor={color} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#27313c" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="time" tick={{ fill: "#9aa4b2", fontSize: 11 }} stroke="#354252" />
            <YAxis tick={{ fill: "#9aa4b2", fontSize: 11 }} stroke="#354252" width={42} />
            <Tooltip
              contentStyle={{ background: "#121821", border: "1px solid #303b48", borderRadius: 8, color: "#f4f7fb" }}
              labelStyle={{ color: "#f4f7fb" }}
            />
            <Area type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2.4} fill={`url(#gradient-${dataKey})`} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      ) : (
        <div className="metric-empty">No Prometheus series is available for this metric yet.</div>
      )}
    </section>
  );
}

function sourceLabel(source: string) {
  if (source === "mock") {
    return "Simulated telemetry";
  }
  if (source === "loki") {
    return "Loki";
  }
  if (source === "alertmanager") {
    return "Alertmanager";
  }
  if (source === "unavailable") {
    return "Unavailable";
  }
  if (source === "empty") {
    return "Waiting";
  }
  if (source === "manifest-simulation") {
    return "Manifest simulation";
  }
  return source.charAt(0).toUpperCase() + source.slice(1);
}

function severityTone(severity: string) {
  if (["critical", "page", "high"].includes(severity.toLowerCase())) {
    return "critical";
  }
  if (["warning", "warn", "medium"].includes(severity.toLowerCase())) {
    return "warning";
  }
  return "info";
}

function auditTone(status: string) {
  if (status === "failure") {
    return "failure";
  }
  if (status === "not_found") {
    return "warning";
  }
  return "success";
}

function formatAuditAction(action: string) {
  return action
    .split(".")
    .map((part) => part.replace(/_/g, " "))
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function formatOperation(value: string) {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function shortIdentifier(value: string | null | undefined) {
  return value ? `${value.slice(0, 8)}…` : "Legacy / unavailable";
}

function formatDuration(durationMs: number | null) {
  if (durationMs === null) {
    return "Not recorded";
  }
  if (durationMs < 1_000) {
    return `${durationMs} ms`;
  }
  return `${(durationMs / 1_000).toFixed(durationMs < 10_000 ? 1 : 0)} s`;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(2)} GiB`;
}

function relativeTime(value: string) {
  const timestamp = new Date(value).getTime();
  if (Number.isNaN(timestamp)) {
    return "Unknown";
  }
  const diffSeconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (diffSeconds < 60) {
    return `${diffSeconds}s ago`;
  }
  const diffMinutes = Math.round(diffSeconds / 60);
  if (diffMinutes < 60) {
    return `${diffMinutes}m ago`;
  }
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) {
    return `${diffHours}h ago`;
  }
  return new Date(value).toLocaleDateString();
}

function averageValue(values: number[]) {
  if (!values.length) {
    return 0;
  }
  return values.reduce((sum, item) => sum + item, 0) / values.length;
}

function maxValue(values: number[]) {
  if (!values.length) {
    return 0;
  }
  return Math.max(...values);
}

function repositoryRelativePath(path: string) {
  const parts = path.split("/");
  return parts.length > 1 ? parts.slice(1).join("/") : path;
}

export default App;
