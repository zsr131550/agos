"""`agos execute-plan` commands."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from agos.cli.worker_registry import register_configured_worker_adapters
from agos.core.execution_runtime import ExecutionRuntimeSnapshot
from agos.core.execution_service import ExecutionService
from agos.core.repo import find_initialized_repo_root, repo_paths


execute_plan_app = typer.Typer(
    help="Create workspaces and run execution-plan workers.",
    invoke_without_command=True,
)


@execute_plan_app.callback(invoke_without_command=True)
def execute_plan_command(
    ctx: typer.Context,
    plan: Path | None = typer.Option(None, "--plan", help="Execution plan YAML or JSON file."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    if plan is None:
        typer.echo("--plan is required", err=True)
        raise typer.Exit(code=2)
    try:
        service = _service()
        execution_plan = service.execute_plan(plan)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(execution_plan.id)


@execute_plan_app.command("run")
def execute_plan_run_command(
    plan: Path = typer.Option(..., "--plan", help="Execution plan YAML or JSON file."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    try:
        snapshot = _service().start_execution_run(plan)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_snapshot_json(snapshot) if json_output else _format_snapshot(snapshot))


@execute_plan_app.command("resume")
def execute_plan_resume_command(
    run_id: str,
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    try:
        snapshot = _service().resume_execution_run(run_id)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_snapshot_json(snapshot) if json_output else _format_snapshot(snapshot))


@execute_plan_app.command("status")
def execute_plan_status_command(
    run_id: str,
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    try:
        snapshot = _service().status_execution_run(run_id)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_snapshot_json(snapshot) if json_output else _format_snapshot(snapshot))


@execute_plan_app.command("cancel")
def execute_plan_cancel_command(
    run_id: str,
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    try:
        snapshot = _service().cancel_execution_run(run_id)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(_snapshot_json(snapshot) if json_output else _format_snapshot(snapshot))


def _service() -> ExecutionService:
    repo_root = find_initialized_repo_root()
    paths = repo_paths(repo_root)
    service = ExecutionService(paths)
    register_configured_worker_adapters(service)
    return service


def _format_snapshot(snapshot: ExecutionRuntimeSnapshot) -> str:
    parts = [snapshot.run_id]
    parts.append(f"running: {_join(snapshot.running_subtasks)}")
    parts.append(f"completed: {_join(snapshot.completed_subtasks)}")
    parts.append(f"failed: {_join(snapshot.failed_subtasks)}")
    parts.append(f"cancelled: {_join(snapshot.cancelled_subtasks)}")
    return " | ".join(parts)


def _snapshot_json(snapshot: ExecutionRuntimeSnapshot) -> str:
    return json.dumps(
        {
            "run_id": snapshot.run_id,
            "running_subtasks": list(snapshot.running_subtasks),
            "completed_subtasks": list(snapshot.completed_subtasks),
            "failed_subtasks": list(snapshot.failed_subtasks),
            "cancelled_subtasks": list(snapshot.cancelled_subtasks),
        },
        sort_keys=True,
    )


def _join(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "-"
