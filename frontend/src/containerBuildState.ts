import type { ContainerBuildEligibilityResult, ContainerBuildRecord } from "./types";

export type ContainerBuildViewState =
  | "idle"
  | "pending"
  | "validating"
  | "approval"
  | "queued"
  | "building"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "rejected"
  | "error";

export function containerBuildViewState(
  validating: boolean,
  submitting: boolean,
  eligibility: ContainerBuildEligibilityResult | null,
  build: ContainerBuildRecord | null,
  error: string,
): ContainerBuildViewState {
  if (error) return "error";
  if (validating) return "validating";
  if (submitting) return "building";
  if (build) return build.status;
  if (eligibility) return "approval";
  return "idle";
}

export function containerBuildAllowedInDemoMode(demoMode: boolean): boolean {
  return !demoMode;
}
