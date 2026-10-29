import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/sourceSnapshotState.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { snapshotBuildCandidate, snapshotOperationsAllowed, sourceSnapshotViewState } = await import(moduleUrl);

const available = {
  id: "snapshot",
  status: "available",
  integrity_verified: true,
  analysis_report_id: "analysis",
};

test("snapshot states distinguish loading, empty, failure, and success", () => {
  assert.equal(sourceSnapshotViewState(true, [], ""), "loading");
  assert.equal(sourceSnapshotViewState(false, [], ""), "empty");
  assert.equal(sourceSnapshotViewState(false, [], "failed"), "failure");
  assert.equal(sourceSnapshotViewState(false, [available], ""), "success");
});

test("only verified available snapshots matching the analysis can feed build eligibility", () => {
  assert.equal(snapshotBuildCandidate([available], "analysis")?.id, "snapshot");
  assert.equal(snapshotBuildCandidate([{ ...available, integrity_verified: false }], "analysis"), undefined);
  assert.equal(snapshotBuildCandidate([{ ...available, status: "expired" }], "analysis"), undefined);
  assert.equal(snapshotBuildCandidate([available], "other"), undefined);
});

test("Demo Mode cannot create or verify genuine snapshots", () => {
  assert.equal(snapshotOperationsAllowed(true), false);
  assert.equal(snapshotOperationsAllowed(false), true);
});
