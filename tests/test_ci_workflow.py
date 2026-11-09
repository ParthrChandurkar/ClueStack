"""Offline security contracts for the optional self-hosted deployment job."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.load((ROOT / ".github/workflows/ci-cd.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
JOB = WORKFLOW["jobs"]["deploy-local-minikube"]
STEPS = {step["name"]: step for step in JOB["steps"]}
CONTEXT_CHECK = STEPS["Verify Minikube context"]["run"]
SECRET_CHECK = STEPS["Require existing deployment secrets"]["run"]
FOUNDATION = STEPS["Apply Terraform local foundation"]["run"]
IMPORTS = re.findall(r'Import-IfExists "([^"]+)" "([^"]+)"', FOUNDATION)
SCRIPTS = "\n".join(step.get("run", "") for step in JOB["steps"])
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")


def test_workflow_yaml_and_local_deployment_opt_in() -> None:
    assert set(WORKFLOW["on"]) == {"push", "pull_request", "workflow_dispatch"}
    assert JOB["needs"] == "publish"
    assert JOB["runs-on"] == ["self-hosted", "Windows", "X64", "local-k8s"]
    assert JOB["if"] == (
        "${{ (github.event_name == 'push' || github.event_name == 'workflow_dispatch') "
        "&& github.ref == 'refs/heads/main' && vars.INFRAWATCH_DEPLOY_TARGET == 'local-minikube' }}"
    )


def test_every_local_kubectl_operation_pins_context_and_kubeconfig() -> None:
    commands = [line.strip() for line in SCRIPTS.splitlines() if re.search(r"\bkubectl\s", line)]
    assert commands
    for command in commands:
        assert 'kubectl --context=minikube --kubeconfig "$HOME/.kube/config" ' in command
    assert "use-context" not in SCRIPTS
    assert "minikube --profile=minikube service" in SCRIPTS
    for command in SCRIPTS.splitlines():
        if re.search(r"terraform -chdir=terraform (import|apply)\b", command):
            assert '"-var=kube_context=minikube"' in command
            assert '"-var=kubeconfig_path=$HOME/.kube/config"' in command


def test_preflight_is_mandatory_and_precedes_all_mutations() -> None:
    names = list(STEPS)
    assert names.index("Verify Minikube context") < names.index("Require existing deployment secrets")
    assert names.index("Require existing deployment secrets") < names.index("Set up Terraform")
    assert "continue-on-error" not in JOB
    for step in JOB["steps"]:
        assert "continue-on-error" not in step
        assert "if" not in step  # Normal success gating: a failed preflight stops deployment.
    for command in (CONTEXT_CHECK + SECRET_CHECK).splitlines():
        if "kubectl " in command:
            assert " config view " in command or " get " in command
            if " get " in command:
                assert "--request-timeout=10s" in command


def test_no_local_credential_creation_interpolation_or_artifacts() -> None:
    assert JOB["env"] == {"DOCKERHUB_USERNAME": "${{ secrets.DOCKERHUB_USERNAME }}"}
    for forbidden in (
        "create secret", "apply -f -", "--from-literal", "--docker-password", "$postgresPassword",
        "$databaseUrl", "DOCKERHUB_TOKEN", "--raw", "upload-artifact", "::add-mask::",
    ):
        assert forbidden not in json.dumps(JOB)
    assert "${{" not in SCRIPTS  # No GitHub secret interpolation into generated PowerShell scripts.
    assert "POSTGRES_PASSWORD" in SECRET_CHECK and "DATABASE_URL" in SECRET_CHECK
    assert "'Opaque:ready'" in SECRET_CHECK
    assert "'kubernetes.io/dockerconfigjson:ready'" in SECRET_CHECK
    assert "Out-File" not in SECRET_CHECK and "Write-Output" not in SECRET_CHECK
    assert "2>$null" in SECRET_CHECK


def test_required_secret_keys_match_workload_contract_and_documentation() -> None:
    postgres = yaml.safe_load((ROOT / "k8s/deployments/postgres-statefulset.yaml").read_text(encoding="utf-8"))
    password = postgres["spec"]["template"]["spec"]["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"]
    assert password == {"name": "infrawatch-secrets", "key": "POSTGRES_PASSWORD"}
    backend = yaml.safe_load((ROOT / "k8s/deployments/backend.yaml").read_text(encoding="utf-8"))
    assert {"secretRef": {"name": "infrawatch-secrets"}} in (
        backend["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    )
    documentation = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("infrawatch-secrets", "dockerhub-pull-secret", "POSTGRES_PASSWORD", "DATABASE_URL"):
        assert name in SECRET_CHECK and f"`{name}`" in documentation
    assert "does not create, replace, or rotate either Secret" in documentation


def test_publish_retains_credentials_and_password_stdin_login() -> None:
    publish = WORKFLOW["jobs"]["publish"]
    assert publish["env"] == {
        "DOCKERHUB_USERNAME": "${{ secrets.DOCKERHUB_USERNAME }}",
        "DOCKERHUB_TOKEN": "${{ secrets.DOCKERHUB_TOKEN }}",
    }
    steps = {step["name"]: step for step in publish["steps"]}
    required = steps["Require DockerHub credentials"]["run"]
    assert '[ -z "$DOCKERHUB_USERNAME" ] || [ -z "$DOCKERHUB_TOKEN" ]' in required
    assert "exit 1" in required
    assert steps["Log in to DockerHub"]["run"].strip() == (
        'echo "$DOCKERHUB_TOKEN" | docker login --username "$DOCKERHUB_USERNAME_CLEAN" --password-stdin'
    )
    assert list(steps).index("Require DockerHub credentials") < list(steps).index("Log in to DockerHub")
    for component in ("backend", "frontend"):
        name = f"Build and push {component} image"
        assert list(steps).index("Log in to DockerHub") < list(steps).index(name)
        assert f'docker push "${component.upper()}_IMAGE:${{GITHUB_SHA}}"' in steps[name]["run"]


def test_foundation_state_and_provider_contract() -> None:
    assert STEPS["Set up Terraform"]["with"]["terraform_wrapper"] == "false"
    assert "$stateJson = terraform -chdir=terraform show -json 2>$null" in FOUNDATION
    assert "--ignore-not-found -o name 2>$null" in FOUNDATION
    provider = (ROOT / "terraform/main.tf").read_text(encoding="utf-8")
    assert re.search(r"config_path\s*=\s*var.kubeconfig_path", provider)
    assert re.search(r"config_context\s*=\s*var.kube_context", provider)
    resources = re.findall(r'^resource "([^"]+)" "([^"]+)"', provider, re.MULTILINE)
    assert {address for address, _ in IMPORTS} == {f"{kind}.{name}" for kind, name in resources}
    assert len(IMPORTS) == 8


def run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    if not POWERSHELL:
        pytest.skip("PowerShell unavailable; static workflow contracts still run")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30, check=False,
    )


def test_all_local_powershell_steps_parse_without_execution() -> None:
    # Parse only: no workflow command (including kubectl or Terraform) is executed.
    for step in JOB["steps"]:
        if "run" not in step:
            continue
        script = step["run"].replace("'", "''")
        result = run_powershell(
            "$errors = $null; $tokens = $null\n"
            f"$null = [System.Management.Automation.Language.Parser]::ParseInput('{script}', "
            "[ref]$tokens, [ref]$errors)\n"
            "if ($errors.Count) { Write-Output 'PowerShell syntax error'; exit 1 }\nexit 0"
        )
        assert result.returncode == 0, step["name"]


@pytest.mark.parametrize(
    ("scenario", "expected_calls", "success"),
    [
        ("valid", 4, True),
        ("wrong_context", 1, False),
        ("context_error", 1, False),
        ("nodes_error", 2, False),
        ("database_missing", 3, False),
        ("database_empty", 3, False),
        ("database_wrong_type", 3, False),
        ("database_error_with_marker", 3, False),
        ("database_throw", 3, False),
        ("database_unexpected_output", 3, False),
        ("registry_missing", 4, False),
        ("registry_empty", 4, False),
        ("registry_wrong_type", 4, False),
    ],
)
def test_preflight_fails_closed_with_mocked_kubectl(scenario: str, expected_calls: int, success: bool) -> None:
    # A shell function shadows kubectl. Neither a real executable nor a cluster is used.
    mock = r"""
$ErrorActionPreference = 'Stop'
$global:callCount = 0
function kubectl {
    $Arguments = @($args)
    $global:callCount++
    $global:LASTEXITCODE = 0
    if ($Arguments[0] -cne '--context=minikube' -or $Arguments[1] -cne '--kubeconfig' -or
        $Arguments[2] -cne "$HOME/.kube/config") { throw 'Unexpected target' }
    if ($Arguments -contains 'config') {
        if ($scenario -eq 'context_error') { $global:LASTEXITCODE = 1; return }
        if ($scenario -eq 'wrong_context') { return 'unrelated-cluster' }
        return 'minikube'
    }
    if ($Arguments -contains 'nodes') {
        if ($scenario -eq 'nodes_error') { $global:LASTEXITCODE = 1 }
        return
    }
    if ($Arguments -contains 'infrawatch-secrets') {
        if ($scenario -eq 'database_missing') { $global:LASTEXITCODE = 1; return }
        if ($scenario -eq 'database_empty') { return 'Opaque:' }
        if ($scenario -eq 'database_wrong_type') { return 'other:ready' }
        if ($scenario -eq 'database_error_with_marker') { $global:LASTEXITCODE = 1 }
        if ($scenario -eq 'database_throw') { throw 'synthetic-sensitive-marker' }
        if ($scenario -eq 'database_unexpected_output') { return 'synthetic-sensitive-marker' }
        return 'Opaque:ready'
    }
    if ($Arguments -contains 'dockerhub-pull-secret') {
        if ($scenario -eq 'registry_missing') { $global:LASTEXITCODE = 1; return }
        if ($scenario -eq 'registry_empty') { return 'kubernetes.io/dockerconfigjson:' }
        if ($scenario -eq 'registry_wrong_type') { return 'Opaque:ready' }
        return 'kubernetes.io/dockerconfigjson:ready'
    }
    throw 'Unexpected mocked command'
}
"""
    script = f"$scenario = '{scenario}'\n" + mock
    script += "\ntry {\n" + CONTEXT_CHECK + SECRET_CHECK
    script += "\nWrite-Output 'preflight-passed'\n} catch {\n"
    script += "Write-Output $_.Exception.Message\nWrite-Output ('calls:' + $global:callCount)\nexit 1\n}\n"
    script += "Write-Output ('calls:' + $global:callCount)\nexit 0\n"
    result = run_powershell(script)
    assert (result.returncode == 0) is success, result.stdout + result.stderr
    assert f"calls:{expected_calls}" in result.stdout
    assert ("preflight-passed" in result.stdout) is success
    assert "synthetic-sensitive-marker" not in result.stdout + result.stderr
    if not success:
        assert "No deployment was attempted." in result.stdout


def run_mocked_foundation(scenario: str, *, failed_import: int = 0, runs: int = 1) -> dict:
    # Functions shadow BOTH executables. The complete workflow step runs against
    # in-memory state; no real Terraform, kubectl, files, credentials or services.
    managed = IMPORTS if scenario == "managed" else IMPORTS[:2] if scenario == "mixed" else []
    if scenario in {"identity_mismatch", "missing_state_id"}:
        managed = [(IMPORTS[0][0], "wrong-resource" if scenario == "identity_mismatch" else "")]
    if scenario == "different_address_case":
        managed = [(IMPORTS[0][0].upper(), IMPORTS[0][1])]
    fixture = json.dumps([
        {"mode": "managed", "address": address, "values": {"id": resource_id, "private": "fixture-sensitive-marker"}}
        for address, resource_id in managed
    ]).replace("'", "''")
    mock = r"""
$ErrorActionPreference = 'Stop'
$global:events = [System.Collections.Generic.List[string]]::new()
$global:imports = [System.Collections.Generic.List[string]]::new()
$global:lookups = 0
$global:applies = 0
function terraform {
    $Arguments = @($args)
    if ($Arguments[0] -cne '-chdir=terraform') { throw 'Unexpected Terraform directory' }
    $operation = $Arguments[1]
    $global:events.Add($operation)
    $global:LASTEXITCODE = 0
    switch ($operation) {
        'init' {
            if ($scenario -eq 'init_failure') { $global:LASTEXITCODE = 1 }
            return
        }
        'show' {
            if ($Arguments.Count -ne 3 -or $Arguments[2] -cne '-json') { throw 'Unexpected state command' }
            if ($scenario -eq 'state_failure') {
                $global:LASTEXITCODE = 1
                return '{"format_version":"1.0","private":"fixture-sensitive-marker"}'
            }
            if ($scenario -eq 'state_throw') { throw 'fixture-sensitive-marker' }
            if ($scenario -eq 'state_malformed') { return '{fixture-sensitive-marker' }
            if ($scenario -eq 'state_wrong_version') { return '{"format_version":"999"}' }
            if ($scenario -eq 'state_wrong_root') { return '{"format_version":"1.0","values":{}}' }
            if (!$global:managed.Count) { return '{"format_version":"1.0"}' }
            return (@{format_version = '1.0'; values = @{root_module = @{resources = @($global:managed)}}} |
                ConvertTo-Json -Depth 10 -Compress)
        }
        { $_ -in 'import', 'apply' } {
            if ($Arguments -cnotcontains '-var=kube_context=minikube' -or
                $Arguments -cnotcontains "-var=kubeconfig_path=$HOME/.kube/config") { throw 'Unexpected target' }
            if ($operation -eq 'apply') {
                $global:applies++
                if ($scenario -eq 'apply_failure') { $global:LASTEXITCODE = 1 }
                return
            }
            $address = $Arguments[4]
            $id = $Arguments[5]
            $global:imports.Add($address)
            if ($scenario -eq 'import_throw') { throw 'fixture-sensitive-marker' }
            if ($scenario -eq 'import_failure' -and $global:imports.Count -eq ($failedImport + 1)) {
                $global:LASTEXITCODE = 1
                return 'fixture-sensitive-marker'
            }
            $global:managed += @{mode = 'managed'; address = $address; values = @{id = $id}}
            return 'fixture-sensitive-marker'
        }
        default { throw 'Unexpected mocked Terraform command' }
    }
}
function kubectl {
    $Arguments = @($args)
    $global:events.Add('lookup')
    $global:lookups++
    $global:LASTEXITCODE = 0
    if ($Arguments[0] -cne '--context=minikube' -or $Arguments[1] -cne '--kubeconfig' -or
        $Arguments[2] -cne "$HOME/.kube/config" -or $Arguments[3] -cne '--request-timeout=10s' -or
        $Arguments[4] -cne 'get' -or $Arguments[-3] -cne '--ignore-not-found' -or
        $Arguments[-2] -cne '-o' -or $Arguments[-1] -cne 'name') { throw 'Unexpected lookup contract' }
    if ($scenario -eq 'lookup_throw') { throw 'fixture-sensitive-marker' }
    # Model kubectl's typed API NotFound classification: only NotFound is
    # converted to success with empty output by --ignore-not-found.
    $apiStatus = switch ($scenario) {
        'absent' { 404 }
        'not_found' { if ($global:lookups -eq 1) { 404 } else { 200 } }
        'mixed' { if ($global:lookups -le 2) { 404 } else { 200 } }
        'forbidden' { 403 }
        'unauthorized' { 401 }
        'timeout' { 504 }
        'connectivity' { 0 }
        default { 200 }
    }
    if ($apiStatus -eq 404) { return }
    if ($apiStatus -ne 200) {
        $global:LASTEXITCODE = 1
        return 'fixture-sensitive-marker'
    }
    return ($Arguments[5] + '/' + $Arguments[6])
}
"""
    script = "$ProgressPreference = 'SilentlyContinue'\n"  # Suppress module-autoload progress, not errors.
    script += f"$scenario = '{scenario}'\n$failedImport = {failed_import}\n"
    script += f"$global:managed = ConvertFrom-Json -InputObject '{fixture}'\n" + mock
    script += f"\n$failure = $null\ntry {{\nfor ($run = 0; $run -lt {runs}; $run++) {{\n{FOUNDATION}\n}}\n"
    script += "} catch { $failure = $_.Exception.Message }\n"
    script += "@{ failure = $failure; imports = @($global:imports); lookups = $global:lookups; "
    script += "applies = $global:applies; events = @($global:events) } | ConvertTo-Json -Depth 5 -Compress\n"
    script += "if ($failure) { exit 1 }\nexit 0\n"
    result = run_powershell(script)
    assert "fixture-sensitive-marker" not in result.stdout + result.stderr
    assert not result.stderr, result.stderr
    trace = json.loads(result.stdout)
    assert result.returncode == (1 if trace["failure"] else 0)
    return trace


@pytest.mark.parametrize(
    ("scenario", "lookups", "imported"),
    [
        ("managed", 0, []),
        ("absent", 8, []),
        ("unmanaged", 8, list(range(8))),
        ("not_found", 8, list(range(1, 8))),
        ("mixed", 6, list(range(4, 8))),
        ("different_address_case", 8, list(range(8))),
    ],
)
def test_foundation_imports_only_existing_unmanaged_resources(scenario: str, lookups: int, imported: list[int]) -> None:
    trace = run_mocked_foundation(scenario)
    assert trace["failure"] is None
    assert trace["lookups"] == lookups
    assert trace["imports"] == [IMPORTS[index][0] for index in imported]
    assert trace["applies"] == 1
    assert trace["events"][:2] == ["init", "show"]
    assert trace["events"][-1] == "apply"


@pytest.mark.parametrize("failed_import", range(8))
def test_each_failed_import_stops_before_apply(failed_import: int) -> None:
    trace = run_mocked_foundation("import_failure", failed_import=failed_import)
    assert trace["failure"].startswith("Terraform import failed for ")
    assert trace["lookups"] == failed_import + 1
    assert trace["imports"] == [address for address, _ in IMPORTS[:failed_import + 1]]
    assert trace["applies"] == 0
    assert trace["events"][-1] == "import"


@pytest.mark.parametrize("scenario", ["forbidden", "unauthorized", "timeout", "connectivity", "lookup_throw"])
def test_lookup_failure_is_never_treated_as_not_found(scenario: str) -> None:
    trace = run_mocked_foundation(scenario)
    assert trace["failure"].startswith("Kubernetes lookup failed for ")
    assert trace["lookups"] == 1
    assert trace["imports"] == []
    assert trace["applies"] == 0
    assert trace["events"] == ["init", "show", "lookup"]


@pytest.mark.parametrize(
    "scenario",
    ["state_failure", "state_throw", "state_malformed", "state_wrong_version", "state_wrong_root", "missing_state_id"],
)
def test_state_inspection_failure_stops_before_lookup_import_or_apply(scenario: str) -> None:
    trace = run_mocked_foundation(scenario)
    assert trace["failure"] == "Cannot inspect Terraform state safely. Apply was not attempted."
    assert trace["lookups"] == 0 and trace["imports"] == [] and trace["applies"] == 0
    assert trace["events"] == ["init", "show"]


def test_state_identity_mismatch_stops_before_mutations() -> None:
    trace = run_mocked_foundation("identity_mismatch")
    assert trace["failure"].startswith("Terraform state identity mismatch for ")
    assert trace["lookups"] == 0 and trace["imports"] == [] and trace["applies"] == 0


def test_import_exception_is_sanitized_and_stops_apply() -> None:
    trace = run_mocked_foundation("import_throw")
    assert trace["failure"].startswith("Terraform import failed for ")
    assert trace["imports"] == [IMPORTS[0][0]] and trace["applies"] == 0


def test_repeated_runs_do_not_reimport_managed_resources() -> None:
    trace = run_mocked_foundation("unmanaged", runs=2)
    assert trace["failure"] is None
    assert trace["imports"] == [address for address, _ in IMPORTS]
    assert trace["lookups"] == 8 and trace["applies"] == 2
    assert trace["events"][-3:] == ["init", "show", "apply"]


def test_initialization_failure_stops_before_state_inspection() -> None:
    trace = run_mocked_foundation("init_failure")
    assert trace["failure"] == "Terraform initialization failed."
    assert trace["events"] == ["init"]
    assert trace["applies"] == 0


def test_apply_failure_is_not_reported_as_success() -> None:
    trace = run_mocked_foundation("apply_failure")
    assert trace["failure"] == "Terraform foundation apply failed."
    assert trace["applies"] == 1
