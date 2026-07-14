"""`agos start` command."""
from __future__ import annotations

from enum import Enum
from pathlib import Path

import typer

from agos.cli.task_execution_registry import build_task_execution_service
from agos.core.adapter import ExecutorRun
from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.task import load_task
from agos.core.task_execution import ExecutorSelection, TaskExecutionRequest
from agos.core.task_execution_service import TaskExecutionError


class StartTaskError(RuntimeError):
    """Raised when an AGOS task cannot be started."""


class _Mode(str, Enum):
    legacy = "legacy"
    candidate = "candidate"


def _parse_gate_overrides(gate_values: list[str] | None) -> list[str]:
    overrides: list[str] = []
    for value in gate_values or []:
        overrides.extend(part.strip() for part in value.split(",") if part.strip())
    return overrides


def start_command(
    title: str = typer.Option(..., "--title", help="Human-readable task title."),
    intent: str | None = typer.Option(None, "--intent", help="Task intent or scope."),
    workflow: str | None = typer.Option(None, "--workflow", help="Workflow name from agos.yaml."),
    gate: list[str] | None = typer.Option(None, "--gate", help="Override locked gates by id."),
    mode: _Mode | None = typer.Option(
        None,
        "--mode",
        help="Override task execution mode (legacy or candidate).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Start a new AGOS task through the configured execution mode."""

    try:
        repo_root = find_initialized_repo_root()
        result = build_task_execution_service(repo_root).start(
            TaskExecutionRequest(
                title=title,
                intent=intent or "",
                workflow=workflow,
                gate_overrides=_parse_gate_overrides(gate),
                mode=mode.value if mode is not None else None,
            )
        )
    except (FileNotFoundError, TaskExecutionError, ValueError, KeyError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    for warning in result.compatibility_warnings:
        typer.echo(f"Warning: {warning}", err=True)
    if json_output:
        typer.echo(result.model_dump_json())
    elif result.mode == "legacy":
        typer.echo(result.issue_id or result.run_id)
    else:
        typer.echo(result.run_id)


def start_task(
    *,
    repo_root: Path,
    title: str,
    intent: str | None = None,
    workflow: str | None = None,
    gate_overrides: list[str] | None = None,
    executor_selection: ExecutorSelection | None = None,
):
    """Compatibility wrapper preserving the legacy ``(task, run)`` result."""

    try:
        result = build_task_execution_service(Path(repo_root)).start(
            TaskExecutionRequest(
                title=title,
                intent=intent or "",
                workflow=workflow,
                gate_overrides=gate_overrides or [],
                mode="legacy",
                executor_selection=executor_selection,
            )
        )
    except (TaskExecutionError, ValueError) as exc:
        raise StartTaskError(str(exc)) from exc

    task = load_task(repo_paths(Path(repo_root)).task_yaml)
    return task, ExecutorRun(
        adapter=task.executor.adapter,
        run_id=result.run_id,
        issue_id=result.issue_id,
    )
