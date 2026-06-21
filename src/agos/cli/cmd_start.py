"""`agos start` command."""
from __future__ import annotations

import typer

from agos.adapters.multica import MulticaAdapter
from agos.core.config import load_config, resolve_gates
from agos.core.gate import gates_locked_payload
from agos.core.ledger import append_task_record
from agos.core.repo import current_task_dir, current_task_is_active, find_initialized_repo_root, repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, new_task_id

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
) -> None:
    """Start a new AGOS task and dispatch it through the configured executor."""

    try:
        repo_root = find_initialized_repo_root()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    task_dir = current_task_dir(repo_root)
    if current_task_is_active(task_dir):
        typer.echo("Active task already exists in .agos/tasks/current", err=True)
        raise typer.Exit(code=1)

    config = load_config(repo_root)
    workflow_name = workflow or config.default_workflow
    overrides = _parse_gate_overrides(gate)
    try:
        resolved_gates = resolve_gates(config, workflow_name, override=overrides or None)
    except KeyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    gate_ids = [gate_spec.id for gate_spec in resolved_gates]
    task = Task(
        id=f"agos-{new_task_id()}",
        title=title,
        intent=intent or "",
        workflow=workflow_name,
        gates=gate_ids,
        executor=ExecutorBinding(
            adapter=config.executor.name,
            agent=config.executor.agent,
        ),
    )

    paths = repo_paths(repo_root)
    task_dir.mkdir(parents=True, exist_ok=True)
    task.save(paths.task_yaml)

    append_task_record(
        paths.ledger,
        "task_started",
        task_id=task.id,
        title=task.title,
        workflow=task.workflow,
    )
    append_task_record(
        paths.ledger,
        "gates_locked",
        task_id=task.id,
        gates=gates_locked_payload(resolved_gates),
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
        paths.ledger,
        "executor_dispatched",
        task_id=task.id,
        adapter=run.adapter,
        run_id=run.run_id,
        issue_id=run.issue_id,
    )
    status = TaskStatus.for_started_task(task=task, run=run, ledger_head_hash=final_record["hash"])
    save_status(status, paths)

    typer.echo(run.issue_id or run.run_id)
