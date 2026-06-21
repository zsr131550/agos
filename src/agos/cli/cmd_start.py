"""`agos start` command."""
from __future__ import annotations

from pathlib import Path

import typer
from ulid import ULID

from agos.adapters.multica import MulticaAdapter
from agos.core.config import AGOSConfig
from agos.core.ledger import append_task_record
from agos.core.repo import config_path, current_task_dir, current_task_is_active, find_repo_root
from agos.core.status import TaskStatus
from agos.core.task import Task, TaskExecutorConfig


def _parse_gate_overrides(gate_values: list[str] | None) -> list[str]:
    overrides: list[str] = []
    for value in gate_values or []:
        overrides.extend(part.strip() for part in value.split(",") if part.strip())
    return overrides


def _current_task_paths(task_dir: Path) -> tuple[Path, Path, Path]:
    return task_dir / "task.yaml", task_dir / "ledger.jsonl", task_dir / "status.json"


def start_command(
    title: str = typer.Option(..., "--title", help="Human-readable task title."),
    intent: str | None = typer.Option(None, "--intent", help="Task intent or scope."),
    workflow: str | None = typer.Option(None, "--workflow", help="Workflow name from agos.yaml."),
    gate: list[str] | None = typer.Option(None, "--gate", help="Override locked gates by id."),
) -> None:
    """Start a new AGOS task and dispatch it through the configured executor."""

    repo_root = find_repo_root()
    task_dir = current_task_dir(repo_root)
    if current_task_is_active(task_dir):
        typer.echo("Active task already exists in .agos/tasks/current", err=True)
        raise typer.Exit(code=1)

    config = AGOSConfig.load(config_path(repo_root))
    workflow_name = workflow or config.default_workflow
    overrides = _parse_gate_overrides(gate)
    try:
        resolved_gates = config.resolve_gates(workflow_name, overrides or None)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    task = Task(
        id=f"agos-{ULID()}",
        title=title,
        intent=intent,
        workflow=workflow_name,
        gates=resolved_gates,
        executor=TaskExecutorConfig(
            adapter=config.executor.name,
            agent=config.executor.agent,
        ),
    )

    task_yaml_path, ledger_path, status_path = _current_task_paths(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    task.save(task_yaml_path)

    append_task_record(
        ledger_path,
        "task_started",
        task_id=task.id,
        title=task.title,
        workflow=task.workflow,
    )
    append_task_record(
        ledger_path,
        "gates_locked",
        task_id=task.id,
        gates=[gate_config.model_dump(mode="python") for gate_config in task.gates],
    )

    if task.executor.adapter != "multica":
        typer.echo(f"Unsupported executor '{task.executor.adapter}'", err=True)
        raise typer.Exit(code=1)
    adapter = MulticaAdapter()
    try:
        run = adapter.start(task)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    final_record = append_task_record(
        ledger_path,
        "executor_dispatched",
        task_id=task.id,
        adapter=run.adapter,
        run_id=run.run_id,
        issue_id=run.issue_id,
    )
    status = TaskStatus.for_started_task(task=task, run=run, ledger_head_hash=final_record["hash"])
    status.save(status_path)

    typer.echo(run.issue_id or run.run_id)

