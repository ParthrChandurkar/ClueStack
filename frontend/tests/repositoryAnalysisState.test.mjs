import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(new URL("../src/repositoryAnalysisState.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {
  isCurrentRepositoryRequest,
  repositoryAnalysisViewState,
  repositoryReadinessViewState,
  repositoryRemoteAnalysisAllowed,
} = await import(moduleUrl);

test("repository analysis exposes an idle state before a request", () => {
  assert.equal(repositoryAnalysisViewState(false, null, ""), "idle");
});

test("repository analysis exposes loading while a request is active", () => {
  assert.equal(repositoryAnalysisViewState(true, null, ""), "loading");
});

test("repository analysis exposes success for a completed result", () => {
  assert.equal(repositoryAnalysisViewState(false, { project_id: "synthetic" }, ""), "success");
});

test("repository analysis exposes failure without presenting stale success", () => {
  assert.equal(repositoryAnalysisViewState(false, { project_id: "synthetic" }, "Unavailable"), "error");
});

test("repository readiness exposes empty, loading, success, and failure states", () => {
  assert.equal(repositoryReadinessViewState(false, null, ""), "idle");
  assert.equal(repositoryReadinessViewState(true, null, ""), "loading");
  assert.equal(repositoryReadinessViewState(false, { overall_status: "ready" }, ""), "success");
  assert.equal(repositoryReadinessViewState(false, null, "Missing analysis"), "error");
});

test("stale project responses are rejected by request generation", () => {
  assert.equal(isCurrentRepositoryRequest(3, 3), true);
  assert.equal(isCurrentRepositoryRequest(2, 3), false);
});

test("browser Demo Mode cannot present remote analysis or readiness as genuine", () => {
  assert.equal(repositoryRemoteAnalysisAllowed(true), false);
  assert.equal(repositoryRemoteAnalysisAllowed(false), true);
});
