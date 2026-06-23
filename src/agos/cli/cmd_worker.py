"""`agos worker` diagnostic commands."""
from __future__ import annotations

import json

import typer
from pydantic import ValidationError

from agos.cli.worker_registry import register_configured_worker_adapters
from agos.core.execution_service import ExecutionService
from agos.core.execution_worker import ExecutionWorkerAdapter, WorkerHealth, WorkerHealthCheck
from agos.core.repo import find_initialized_repo_root, repo_paths


worker_app = typer.Typer(help="Inspect configured worker adapters.")


@worker_app.command("doctor")
def worker_doctor_command(
    worker: str | None = typer.Option(None, "--worker", help="Diagnose one configured worker."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Diagnose configured execution worker adapters."""

    try:
        healths = _healths(worker, json_output=json_output)
    except typer.Exit:
        raise
    except Exception as exc:
        _report_error(_safe_error_message(exc), json_output=json_output)
        raise typer.Exit(code=1) from exc

    healthy = all(health.is_healthy for health in healths)
    if json_output:
        typer.echo(json.dumps(_doctor_payload(healths), sort_keys=True))
    else:
        typer.echo(_format_healths(healths))
    if not healthy:
        raise typer.Exit(code=1)


def _healths(worker: str | None, *, json_output: bool) -> list[WorkerHealth]:
    service = _service()
    adapters = service.worker_adapters()
    if worker is not None:
        adapter = adapters.get(worker)
        if adapter is None:
            _report_error(f"unknown worker: {worker}", json_output=json_output)
            raise typer.Exit(code=1)
        return [_adapter_health(adapter)]
    return [_adapter_health(adapters[name]) for name in sorted(adapters)]


def _service() -> ExecutionService:
    repo_root = find_initialized_repo_root()
    service = ExecutionService(repo_paths(repo_root))
    register_configured_worker_adapters(service)
    return service


def _adapter_health(adapter: ExecutionWorkerAdapter) -> WorkerHealth:
    try:
        return adapter.health()
    except Exception as exc:
        return WorkerHealth(
            name=getattr(adapter, "name", "unknown"),
            adapter=adapter.__class__.__name__,
            checks=[
                WorkerHealthCheck(
                    name="health_check",
                    state="failed",
                    detail=str(exc),
                )
            ],
        )


def _doctor_payload(healths: list[WorkerHealth]) -> dict[str, object]:
    return {
        "healthy": all(health.is_healthy for health in healths),
        "workers": [_health_payload(health) for health in healths],
    }


def _health_payload(health: WorkerHealth) -> dict[str, object]:
    return {
        "name": health.name,
        "adapter": health.adapter,
        "state": health.state,
        "checks": [
            {
                "name": check.name,
                "state": check.state,
                "detail": check.detail,
            }
            for check in health.checks
        ],
        "metadata": dict(health.metadata),
    }


def _format_healths(healths: list[WorkerHealth]) -> str:
    lines: list[str] = []
    for health in healths:
        lines.append(f"{health.name}: {health.state}")
        for check in health.checks:
            detail = f": {check.detail}" if check.detail else ""
            lines.append(f"  [{check.state}] {check.name}{detail}")
    return "\n".join(lines)


def _report_error(message: str, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps({"healthy": False, "workers": [], "error": message}, sort_keys=True))
    typer.echo(message, err=True)


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "invalid AGOS configuration"
    return str(exc)
