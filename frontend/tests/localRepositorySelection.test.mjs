import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/localRepositorySelection.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { isLocalAnalysisCandidate, isLocalSnapshotCandidate, summarizeLocalSelection, summarizeLocalSnapshotSelection } = await import(moduleUrl);

test("accepts recognized configuration while excluding generated and sensitive paths", () => {
  assert.equal(isLocalAnalysisCandidate("sample/package.json"), true);
  assert.equal(isLocalAnalysisCandidate("sample/k8s/deployment.yaml"), true);
  assert.equal(isLocalAnalysisCandidate("sample/node_modules/package.json"), false);
  assert.equal(isLocalAnalysisCandidate("sample/.env"), false);
  assert.equal(isLocalAnalysisCandidate("sample/.terraform/generated.tf"), false);
  assert.equal(isLocalAnalysisCandidate("sample/k8s/client.crt"), false);
  assert.equal(isLocalAnalysisCandidate("../package.json"), false);
  assert.equal(isLocalAnalysisCandidate("C:/package.json"), false);
});

test("snapshot selection includes source text but excludes secrets and generated trees", () => {
  assert.equal(isLocalSnapshotCandidate("sample/src/main.ts"), true);
  assert.equal(isLocalSnapshotCandidate("sample/Dockerfile"), true);
  assert.equal(isLocalSnapshotCandidate("sample/.env"), false);
  assert.equal(isLocalSnapshotCandidate("sample/node_modules/module.js"), false);
  assert.equal(isLocalSnapshotCandidate("sample/logo.png"), false);
  const summary = summarizeLocalSnapshotSelection([
    { path: "sample/src/main.ts", size: 100 },
    { path: "sample/logo.png", size: 100 },
  ]);
  assert.equal(summary.accepted.length, 1);
  assert.equal(summary.ignoredCount, 1);
});

test("summarizes recognized files and applies total-size limits", () => {
  const normal = summarizeLocalSelection([
    { path: "sample/package.json", size: 200 },
    { path: "sample/src/main.ts", size: 300 },
  ]);
  assert.equal(normal.accepted.length, 1);
  assert.equal(normal.ignoredCount, 1);
  assert.equal(normal.totalBytes, 200);
  assert.equal(normal.error, "");

  const oversized = summarizeLocalSelection([
    { path: "sample/package.json", size: 1_000_001 },
  ]);
  assert.match(oversized.error, /256 KB/);
});
