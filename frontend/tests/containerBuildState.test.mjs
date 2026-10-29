import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/containerBuildState.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { containerBuildAllowedInDemoMode, containerBuildViewState } = await import(moduleUrl);

test("container build lifecycle states remain explicit", () => {
  assert.equal(containerBuildViewState(false, false, null, null, ""), "idle");
  assert.equal(containerBuildViewState(true, false, null, null, ""), "validating");
  assert.equal(containerBuildViewState(false, false, { eligible: true }, null, ""), "approval");
  assert.equal(containerBuildViewState(false, true, null, null, ""), "building");
  assert.equal(containerBuildViewState(false, false, null, { status: "succeeded" }, ""), "succeeded");
  assert.equal(containerBuildViewState(false, false, null, { status: "rejected" }, ""), "rejected");
  assert.equal(containerBuildViewState(false, false, null, null, "failure"), "error");
});

test("Demo Mode cannot submit genuine builds", () => {
  assert.equal(containerBuildAllowedInDemoMode(true), false);
  assert.equal(containerBuildAllowedInDemoMode(false), true);
});
