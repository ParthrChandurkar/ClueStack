"""CLI orchestration helpers for ClueStack.

The CLI is intentionally thin in this phase. It discovers whether the command
is being run from a ClueStack repository checkout and points users to the
existing scripts that currently own Docker, Terraform, Kustomize, and
Kubernetes behavior.
"""

from __future__ import annotations

import platform
from pathlib import Path

REPO_MARKERS = ("docker-compose.yml", "k8s", "terraform", "scripts")


def find_repo_root(start: Path | None = None) -> Path | None:
    """Return the nearest ClueStack repository root, if the current tree has one."""

    current = (start or Path.cwd()).resolve()
    candidates = (current, *current.parents)
    for candidate in candidates:
        if all((candidate / marker).exists() for marker in REPO_MARKERS):
            return candidate
    return None


def platform_label() -> str:
    """Return a concise platform label for user-facing guidance."""

    system = platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    return system or "unknown"


def lifecycle_guidance(command: str, repo_root: Path | None = None) -> str:
    """Build an honest placeholder message for lifecycle commands."""

    root = repo_root if repo_root is not None else find_repo_root()
    platform_name = platform_label()
    lines = [
        f"infrawatch {command}: Python CLI lifecycle orchestration is not implemented yet.",
        "This command did not start, stop, inspect, or modify any local infrastructure.",
        "",
    ]

    if root is None:
        lines.extend(
            [
                "Run this command from a ClueStack repository checkout to see the current script-based workflow.",
                "For now, use the documented Docker Compose or Minikube setup from the project README.",
            ]
        )
        return "\n".join(lines)

    lines.append(f"Detected ClueStack repository: {root}")
    lines.append("Current supported workflow:")

    if command == "start":
        if platform_name == "windows":
            lines.append(r"  .\scripts\start-k8s.ps1")
        else:
            lines.append("  bash scripts/setup.sh --start-minikube")
        lines.append("  # or use Docker Compose: docker compose up --build")
    elif command == "stop":
        if platform_name == "windows":
            lines.append(r"  .\scripts\stop-k8s.ps1")
            lines.append(r"  .\scripts\stop-local.ps1")
        else:
            lines.append("  kubectl delete -k k8s --ignore-not-found=true")
            lines.append("  docker compose down")
    elif command == "status":
        if platform_name == "windows":
            lines.append(r"  .\scripts\doctor.ps1")
        else:
            lines.append("  bash scripts/health-check.sh")
            lines.append("  bash scripts/setup.sh --check-only")
    elif command == "doctor":
        if platform_name == "windows":
            lines.append(r"  .\scripts\doctor.ps1")
        else:
            lines.append("  bash scripts/setup.sh --check-only")
            lines.append("  bash scripts/health-check.sh")
    else:
        lines.append("  see README.md and docs/GETTING_STARTED.md")

    lines.extend(
        [
            "",
            "Use the installed CLI for the supported local lifecycle commands.",
            "For lower-level troubleshooting, see README.md and docs/GETTING_STARTED.md.",
        ]
    )
    return "\n".join(lines)
