import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(new URL("../src/clusterOverviewState.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { canRefreshCluster, clusterOverviewViewState, clusterSummary } = await import(moduleUrl);

const enabledContexts = { enabled: true, contexts: ["minikube"], current_context: "minikube", message: null };
const overview = {
  total_nodes: 2,
  ready_nodes: 1,
  not_ready_nodes: 1,
  pods: { total: 5, running: 3, pending: 1, succeeded: 1, failed: 0, unknown: 0, namespaces: [] },
  resource_usage: { available: false, cpu_cores: null, memory_bytes: null, nodes_reporting: 0, message: "Unavailable" },
};

test("cluster overview exposes a loading state", () => {
  assert.equal(clusterOverviewViewState(true, null, null, ""), "loading");
});

test("disabled Kubernetes access is explicit", () => {
  assert.equal(clusterOverviewViewState(false, { enabled: false, contexts: [], message: "Disabled" }, null, ""), "disabled");
});

test("connection and API failures render as errors", () => {
  assert.equal(clusterOverviewViewState(false, enabledContexts, null, "Cluster unavailable"), "error");
});

test("an empty kubeconfig context list is distinct from disconnected", () => {
  assert.equal(clusterOverviewViewState(false, { ...enabledContexts, contexts: [] }, null, ""), "empty");
});

test("node and Pod statistics preserve ready and running counts", () => {
  assert.deepEqual(clusterSummary(overview), {
    nodes: "1/2 ready",
    pods: "3/5 running",
    metrics: "unavailable",
  });
});

test("manual refresh requires an explicit context and prevents duplicate requests", () => {
  assert.equal(canRefreshCluster("", false, true), false);
  assert.equal(canRefreshCluster("minikube", true, true), false);
  assert.equal(canRefreshCluster("minikube", false, true), true);
});

test("a completed overview renders success without fabricating unavailable metrics", () => {
  assert.equal(clusterOverviewViewState(false, enabledContexts, overview, ""), "success");
  assert.equal(overview.resource_usage.cpu_cores, null);
  assert.equal(overview.resource_usage.memory_bytes, null);
});
