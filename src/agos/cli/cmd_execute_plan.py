"""`agos execute-plan` command."""
from __future__ import annotations

from pathlib import Path

import typer

from agos.adapters.workers import LocalWorktreeWorkerAdapter
from agos.core.execution_service import ExecutionService
from agos.core.repo import find_initialized_repo_root, repo_paths


def execute_plan_command(
    plan: Path = typer.Option(..., "--plan", help="Execution plan YAML or JSON file."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        service = ExecutionService(paths)
        service.register_worker_adapter(
            LocalWorktreeWorkerAdapter(service.workspace_manager),
        )
        execution_plan = service.execute_plan(plan)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(execution_plan.id)
