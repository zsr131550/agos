"""Top-level `agos doctor` diagnostics."""
from __future__ import annotations

import json
from dataclasses import dataclass

import typer
from pydantic import ValidationError

from agos.cli.orchestration_registry import register_configured_orchestration_backends
from agos.cli.reviewer_registry import configured_reviewer_adapters, configured_reviewer_specs
from agos.cli.worker_registry import register_configured_worker_adapters
from agos.core.config import AGOSConfig
from agos.core.execution_service import ExecutionService
from agos.core.repo import find_repo_root, repo_paths


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    state: str
    detail: str = ""

    def payload(self) -> dict[str, str]:
        return {"name": self.name, "state": self.state, "detail": self.detail}


def doctor_command(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Diagnose the current AGOS repository configuration."""

    checks = _run_checks()
    healthy = all(check.state == "passed" for check in checks)
    payload = {"healthy": healthy, "checks": [check.payload() for check in checks]}

    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
    else:
        typer.echo(_format_checks(checks))

    if not healthy:
        raise typer.Exit(code=1)


def _run_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    repo_root = None
    config = None

    try:
        repo_root = find_repo_root()
        checks.append(DoctorCheck("git_repo", "passed", str(repo_root)))
    except Exception as exc:
        checks.append(DoctorCheck("git_repo", "failed", str(exc)))
        return checks

    paths = repo_paths(repo_root)
    if paths.agos_yaml.is_file():
        checks.append(DoctorCheck("agos_initialized", "passed", str(paths.agos_yaml)))
    else:
        checks.append(DoctorCheck("agos_initialized", "failed", "missing .agos/agos.yaml"))
        checks.extend(
            [
                DoctorCheck("config", "skipped", "repository is not initialized"),
                DoctorCheck("workers", "skipped", "repository is not initialized"),
                DoctorCheck("reviewers", "skipped", "repository is not initialized"),
                DoctorCheck("orchestration", "skipped", "repository is not initialized"),
            ]
        )
        return checks

    try:
        config = AGOSConfig.load(paths.agos_yaml)
        checks.append(DoctorCheck("config", "passed", str(paths.agos_yaml)))
    except Exception as exc:
        checks.append(DoctorCheck("config", "failed", _safe_config_error(exc)))
        checks.extend(
            [
                DoctorCheck("workers", "skipped", "config is invalid"),
                DoctorCheck("reviewers", "skipped", "config is invalid"),
                DoctorCheck("orchestration", "skipped", "config is invalid"),
            ]
        )
        return checks

    service = ExecutionService(paths)
    try:
        register_configured_worker_adapters(service)
        worker_count = len(service.worker_adapters())
        checks.append(DoctorCheck("workers", "passed", f"{worker_count} worker(s) configured"))
    except Exception as exc:
        checks.append(DoctorCheck("workers", "failed", str(exc)))

    try:
        reviewer_specs = configured_reviewer_specs(repo_root)
        configured_reviewer_adapters(repo_root)
        checks.append(DoctorCheck("reviewers", "passed", f"{len(reviewer_specs)} reviewer(s) configured"))
    except Exception as exc:
        checks.append(DoctorCheck("reviewers", "failed", str(exc)))

    try:
        register_configured_orchestration_backends(service)
        state = "passed" if config.orchestration.backend in service.orchestration_backend_names() else "failed"
        detail = (
            f"backend {config.orchestration.backend!r} configured"
            if state == "passed"
            else f"unknown backend {config.orchestration.backend!r}"
        )
        checks.append(DoctorCheck("orchestration", state, detail))
    except Exception as exc:
        checks.append(DoctorCheck("orchestration", "failed", str(exc)))

    return checks


def _format_checks(checks: list[DoctorCheck]) -> str:
    return "\n".join(
        f"[{check.state}] {check.name}{': ' + check.detail if check.detail else ''}"
        for check in checks
    )


def _safe_config_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return f"invalid AGOS configuration: {exc}"
    return str(exc)
