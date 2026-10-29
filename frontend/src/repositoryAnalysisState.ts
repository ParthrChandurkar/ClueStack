export type RepositoryAnalysisViewState = "idle" | "loading" | "success" | "error";

export function repositoryAnalysisViewState<T>(
  loading: boolean,
  result: T | null,
  error: string,
): RepositoryAnalysisViewState {
  if (loading) {
    return "loading";
  }
  if (error) {
    return "error";
  }
  if (result) {
    return "success";
  }
  return "idle";
}

export const repositoryReadinessViewState = repositoryAnalysisViewState;

export function isCurrentRepositoryRequest(requestId: number, currentRequestId: number): boolean {
  return requestId === currentRequestId;
}

export function repositoryRemoteAnalysisAllowed(demoMode: boolean): boolean {
  return !demoMode;
}
