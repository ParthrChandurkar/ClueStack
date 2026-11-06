import type { ProjectCreate, ProjectRecord } from "./types";

export interface GitHubProjectRegistrationForm {
  name: string;
  repositoryUrl: string;
  description: string;
  defaultNamespace: string;
}

export type GitHubProjectRegistrationErrors = Partial<
  Record<keyof GitHubProjectRegistrationForm, string>
>;

export const EMPTY_GITHUB_PROJECT_FORM: GitHubProjectRegistrationForm = {
  name: "",
  repositoryUrl: "",
  description: "",
  defaultNamespace: "infrawatch",
};

const NAMESPACE_PATTERN = /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/;

export function normalizeGitHubRepositoryUrl(value: string): string | null {
  let normalized = value.trim().replace(/\/+$/, "");
  if (normalized.endsWith(".git")) {
    normalized = normalized.slice(0, -4);
  }
  if (!normalized || normalized.length > 255) {
    return null;
  }

  try {
    const parsed = new URL(normalized);
    if (
      parsed.protocol !== "https:"
      || parsed.hostname !== "github.com"
      || parsed.port
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
    ) {
      return null;
    }
    const rawParts = parsed.pathname.replace(/^\//, "").split("/");
    const parts = rawParts.map((part) => decodeURIComponent(part));
    if (
      parts.length !== 2
      || parts.some((part) => !part || part === "." || part === ".." || part.includes("/") || part.includes("\\"))
    ) {
      return null;
    }
    return normalized;
  } catch {
    return null;
  }
}

export function validateGitHubProjectRegistration(
  form: GitHubProjectRegistrationForm,
): GitHubProjectRegistrationErrors {
  const errors: GitHubProjectRegistrationErrors = {};
  const name = form.name.trim().replace(/\s+/g, " ");
  const description = form.description.trim();
  const defaultNamespace = form.defaultNamespace.trim();

  if (name.length < 2 || name.length > 80) {
    errors.name = "Project name must contain between 2 and 80 characters.";
  }
  if (!normalizeGitHubRepositoryUrl(form.repositoryUrl)) {
    errors.repositoryUrl = "Use a public repository URL in the form https://github.com/OWNER/REPOSITORY.";
  }
  if (description.length > 500) {
    errors.description = "Description must not exceed 500 characters.";
  }
  if (
    defaultNamespace.length < 2
    || defaultNamespace.length > 63
    || !NAMESPACE_PATTERN.test(defaultNamespace)
  ) {
    errors.defaultNamespace = "Namespace must use lowercase letters, numbers, and hyphens.";
  }
  return errors;
}

export function buildGitHubProjectCreate(form: GitHubProjectRegistrationForm): ProjectCreate {
  const errors = validateGitHubProjectRegistration(form);
  if (Object.keys(errors).length > 0) {
    throw new Error(Object.values(errors).join(" "));
  }
  return {
    name: form.name.trim().replace(/\s+/g, " "),
    mode: "github_repository",
    source_type: "github_public_readonly",
    repository_url: normalizeGitHubRepositoryUrl(form.repositoryUrl),
    default_namespace: form.defaultNamespace.trim(),
    description: form.description.trim() || null,
  };
}

export function buildLocalProjectCreate(name: string, slug: string): ProjectCreate {
  return {
    name: name.trim(),
    slug: slug.trim() || undefined,
    mode: "local",
    source_type: "manual",
    default_namespace: "infrawatch",
  };
}

export async function registerGitHubProject(
  form: GitHubProjectRegistrationForm,
  create: (payload: ProjectCreate) => Promise<ProjectRecord>,
  refresh: (project: ProjectRecord) => Promise<void>,
): Promise<ProjectRecord> {
  const project = await create(buildGitHubProjectCreate(form));
  await refresh(project);
  return project;
}
