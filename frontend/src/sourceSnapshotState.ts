import type { SourceSnapshotRecord } from "./types";

export type SourceSnapshotViewState = "empty" | "loading" | "success" | "failure";

export function sourceSnapshotViewState(
  loading: boolean,
  snapshots: SourceSnapshotRecord[],
  error: string,
): SourceSnapshotViewState {
  if (loading) return "loading";
  if (error) return "failure";
  return snapshots.length > 0 ? "success" : "empty";
}

export function snapshotBuildCandidate(
  snapshots: SourceSnapshotRecord[],
  analysisReportId: string | undefined,
): SourceSnapshotRecord | undefined {
  if (!analysisReportId) return undefined;
  return snapshots.find((snapshot) => (
    snapshot.status === "available"
    && snapshot.integrity_verified
    && snapshot.analysis_report_id === analysisReportId
  ));
}

export function snapshotOperationsAllowed(demoMode: boolean): boolean {
  return !demoMode;
}
