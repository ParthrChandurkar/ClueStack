export const LOCAL_ANALYSIS_MAX_FILES = 40;
export const LOCAL_ANALYSIS_MAX_FILE_BYTES = 256_000;
export const LOCAL_ANALYSIS_MAX_TOTAL_BYTES = 1_000_000;
export const LOCAL_SNAPSHOT_MAX_FILES = 250;
export const LOCAL_SNAPSHOT_MAX_FILE_BYTES = 1_000_000;
export const LOCAL_SNAPSHOT_MAX_TOTAL_BYTES = 25_000_000;

const EXCLUDED_PARTS = new Set([
  ".git", ".cache", ".mypy_cache", ".next", ".nuxt", ".pytest_cache", ".terraform", ".tox", ".venv",
  "__pycache__", "build", "coverage", "dist", "env", "node_modules", "out", "target", "vendor", "venv",
]);
const SENSITIVE_PARTS = new Set([
  ".env", "credentials", "credential", "secrets", "secret", "private-key", "private_key",
  "id_rsa", "id_ed25519",
]);

export interface LocalSelectionFile {
  path: string;
  size: number;
}

export interface LocalSelectionSummary {
  accepted: LocalSelectionFile[];
  ignoredCount: number;
  totalBytes: number;
  error: string;
}

export function isLocalAnalysisCandidate(path: string): boolean {
  if (!isSafeRelativePath(path)) {
    return false;
  }
  const parts = path.split("/");
  const lowerParts = parts.map((part) => part.toLowerCase());
  if (lowerParts.some((part) => EXCLUDED_PARTS.has(part) || isSensitivePart(part))) {
    return false;
  }
  const basename = lowerParts[lowerParts.length - 1] ?? "";
  const pathLower = lowerParts.join("/");
  return basename === "package.json"
    || basename === "pyproject.toml"
    || (basename.startsWith("requirements") && basename.endsWith(".txt"))
    || basename === "dockerfile"
    || basename.startsWith("dockerfile.")
    || basename === ".dockerignore"
    || ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"].includes(basename)
    || basename.endsWith(".tf")
    || (pathLower.includes(".github/workflows/") && [".yml", ".yaml"].some((suffix) => basename.endsWith(suffix)))
    || (looksLikeKubernetesPath(lowerParts, basename) && [".yml", ".yaml"].some((suffix) => basename.endsWith(suffix)));
}

export function summarizeLocalSelection(files: LocalSelectionFile[]): LocalSelectionSummary {
  const accepted = files.filter((file) => isLocalAnalysisCandidate(file.path));
  const ignoredCount = files.length - accepted.length;
  if (accepted.length > LOCAL_ANALYSIS_MAX_FILES) {
    return { accepted, ignoredCount, totalBytes: 0, error: `Select a folder with at most ${LOCAL_ANALYSIS_MAX_FILES} recognized configuration files.` };
  }
  const oversized = accepted.find((file) => file.size > LOCAL_ANALYSIS_MAX_FILE_BYTES);
  if (oversized) {
    return { accepted, ignoredCount, totalBytes: 0, error: "A recognized configuration file exceeds the 256 KB analysis limit." };
  }
  const totalBytes = accepted.reduce((total, file) => total + file.size, 0);
  if (totalBytes > LOCAL_ANALYSIS_MAX_TOTAL_BYTES) {
    return { accepted, ignoredCount, totalBytes, error: "Recognized configuration exceeds the 1 MB total analysis limit." };
  }
  return { accepted, ignoredCount, totalBytes, error: "" };
}

export function summarizeLocalSnapshotSelection(files: LocalSelectionFile[]): LocalSelectionSummary {
  const accepted = files.filter((file) => isLocalSnapshotCandidate(file.path));
  const ignoredCount = files.length - accepted.length;
  if (accepted.length > LOCAL_SNAPSHOT_MAX_FILES) {
    return { accepted, ignoredCount, totalBytes: 0, error: `The snapshot limit is ${LOCAL_SNAPSHOT_MAX_FILES} source files.` };
  }
  const oversized = accepted.find((file) => file.size > LOCAL_SNAPSHOT_MAX_FILE_BYTES);
  if (oversized) {
    return { accepted, ignoredCount, totalBytes: 0, error: "A source file exceeds the 1 MB snapshot limit." };
  }
  const totalBytes = accepted.reduce((total, file) => total + file.size, 0);
  if (totalBytes > LOCAL_SNAPSHOT_MAX_TOTAL_BYTES) {
    return { accepted, ignoredCount, totalBytes, error: "Selected source exceeds the 25 MB snapshot limit." };
  }
  return { accepted, ignoredCount, totalBytes, error: "" };
}

export function isLocalSnapshotCandidate(path: string): boolean {
  if (!isSafeRelativePath(path)) return false;
  const parts = path.split("/");
  const lower = parts.map((part) => part.toLowerCase());
  if (lower.some((part) => EXCLUDED_PARTS.has(part) || isSensitivePart(part))) return false;
  const basename = lower[lower.length - 1] ?? "";
  const textExtensions = [
    ".c", ".cc", ".conf", ".config", ".cpp", ".cs", ".css", ".go", ".h", ".html", ".ini",
    ".java", ".js", ".json", ".jsx", ".kt", ".md", ".php", ".properties", ".py", ".rb", ".rs",
    ".sh", ".sql", ".svelte", ".tf", ".toml", ".ts", ".tsx", ".txt", ".vue", ".xml", ".yaml", ".yml",
  ];
  return basename === "dockerfile" || basename.startsWith("dockerfile.") || basename === "makefile"
    || basename === ".dockerignore" || basename === ".gitignore"
    || textExtensions.some((extension) => basename.endsWith(extension));
}

function isSafeRelativePath(path: string): boolean {
  const parts = path.split("/");
  return Boolean(path)
    && !path.startsWith("/")
    && !/^[A-Za-z]:\//.test(path)
    && !path.includes("\\")
    && !/[\u0000-\u001f]/.test(path)
    && parts.every((part) => part !== "" && part !== "." && part !== "..");
}

function isSensitivePart(part: string): boolean {
  const stem = part.includes(".") ? part.slice(0, part.lastIndexOf(".")) : part;
  return SENSITIVE_PARTS.has(part)
    || SENSITIVE_PARTS.has(stem)
    || part.startsWith(".env")
    || [".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".der"].some((suffix) => part.endsWith(suffix));
}

function looksLikeKubernetesPath(parts: string[], basename: string): boolean {
  return parts.some((part) => ["k8s", "kubernetes", "manifests", "deploy", "deployments"].includes(part))
    || ["deployment.yml", "deployment.yaml", "service.yml", "service.yaml", "statefulset.yml", "statefulset.yaml", "daemonset.yml", "daemonset.yaml", "kustomization.yml", "kustomization.yaml"].includes(basename);
}
