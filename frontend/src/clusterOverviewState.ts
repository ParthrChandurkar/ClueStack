import type { KubernetesClusterOverview, KubernetesContextList } from "./types";

export type ClusterOverviewViewState = "loading" | "disabled" | "error" | "empty" | "idle" | "success";

export function clusterOverviewViewState(
  loading: boolean,
  contexts: KubernetesContextList | null,
  overview: KubernetesClusterOverview | null,
  error: string,
): ClusterOverviewViewState {
  if (loading) return "loading";
  if (error) return "error";
  if (contexts && !contexts.enabled) return "disabled";
  if (contexts?.enabled && contexts.contexts.length === 0) return "empty";
  if (overview) return "success";
  return "idle";
}

export function canRefreshCluster(context: string, loading: boolean, enabled: boolean): boolean {
  return Boolean(context) && !loading && enabled;
}

export function clusterSummary(overview: KubernetesClusterOverview) {
  return {
    nodes: `${overview.ready_nodes}/${overview.total_nodes} ready`,
    pods: `${overview.pods.running}/${overview.pods.total} running`,
    metrics: overview.resource_usage.available ? "available" : "unavailable",
  };
}
