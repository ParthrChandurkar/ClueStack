import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(new URL("../src/githubProjectRegistration.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const {
  buildGitHubProjectCreate,
  buildLocalProjectCreate,
  registerGitHubProject,
  validateGitHubProjectRegistration,
} = await import(moduleUrl);

function validForm(overrides = {}) {
  return {
    name: "Catalog Repository",
    repositoryUrl: "https://github.com/example/catalog-api",
    description: "Public catalog service",
    defaultNamespace: "infrawatch",
    ...overrides,
  };
}

function projectRecord() {
  return {
    id: "9eb8ca13-c578-4f25-b094-4bfa85d6839f",
    name: "Catalog Repository",
    slug: "catalog-repository",
    mode: "github_repository",
    source_type: "github_public_readonly",
    repository_url: "https://github.com/example/catalog-api",
    default_namespace: "infrawatch",
    description: "Public catalog service",
    created_at: "2026-11-06T08:00:00Z",
    updated_at: "2026-11-06T08:00:00Z",
    archived_at: null,
  };
}

test("valid public GitHub repository registration uses the backend project contract", () => {
  assert.deepEqual(buildGitHubProjectCreate(validForm()), {
    name: "Catalog Repository",
    mode: "github_repository",
    source_type: "github_public_readonly",
    repository_url: "https://github.com/example/catalog-api",
    default_namespace: "infrawatch",
    description: "Public catalog service",
  });
});

test("missing project name is rejected before submission", () => {
  const errors = validateGitHubProjectRegistration(validForm({ name: " " }));
  assert.match(errors.name, /Project name/);
});

test("malformed GitHub repository URL is rejected", () => {
  const errors = validateGitHubProjectRegistration(validForm({ repositoryUrl: "github.com/example/catalog-api" }));
  assert.match(errors.repositoryUrl, /https:\/\/github\.com/);
});

test("unsupported repository domain and embedded credentials are rejected", () => {
  const domainErrors = validateGitHubProjectRegistration(validForm({
    repositoryUrl: "https://gitlab.com/example/catalog-api",
  }));
  const credentialErrors = validateGitHubProjectRegistration(validForm({
    repositoryUrl: "https://token@github.com/example/catalog-api",
  }));
  assert.ok(domainErrors.repositoryUrl);
  assert.ok(credentialErrors.repositoryUrl);
});

test("backend validation failure is returned without refreshing projects", async () => {
  let refreshed = false;
  await assert.rejects(
    registerGitHubProject(
      validForm(),
      async () => { throw new Error("Repository URL is already registered"); },
      async () => { refreshed = true; },
    ),
    /already registered/,
  );
  assert.equal(refreshed, false);
});

test("successful registration refreshes the project list with the created project", async () => {
  const created = projectRecord();
  let refreshedProject = null;
  const result = await registerGitHubProject(
    validForm(),
    async () => created,
    async (project) => { refreshedProject = project; },
  );
  assert.equal(result.id, created.id);
  assert.equal(refreshedProject.id, created.id);
});

test("existing local project creation keeps manual local semantics", () => {
  assert.deepEqual(buildLocalProjectCreate(" Payments Lab ", " payments-lab "), {
    name: "Payments Lab",
    slug: "payments-lab",
    mode: "local",
    source_type: "manual",
    default_namespace: "infrawatch",
  });
});
