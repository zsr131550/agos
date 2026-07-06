"""`agos start` command."""
from __future__ import annotations

import shutil

import typer

from agos.cli.executor_registry import configured_executor_adapter
from agos.core.config import load_config, resolve_gates
from agos.core.evidence import EvidenceStore
from agos.core.gate import gates_locked_payload
from agos.core.ledger import Ledger
from agos.core.repo import (
    current_task_dir,
    current_task_is_active,
    find_initialized_repo_root,
    repo_paths,
    staging_task_dir,
    task_paths,
)
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, new_task_id


class StartTaskError(RuntimeError):
    """Raised when an AGOS task cannot be started."""


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
    try:
        _task, run = start_task(
            repo_root=repo_root,
            title=title,
            intent=intent,
            workflow=workflow,
            gate_overrides=_parse_gate_overrides(gate),
        )
    except StartTaskError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    except KeyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(run.issue_id or run.run_id)


def start_task(
    *,
    repo_root,
    title: str,
    intent: str | None = None,
    workflow: str | None = None,
    gate_overrides: list[str] | None = None,
):
    """Start a new AGOS task in repo_root and dispatch it through the configured executor."""

    current_dir = current_task_dir(repo_root)
    if current_task_is_active(current_dir):
        raise StartTaskError("Active task already exists in .agos/tasks/current")

    config = load_config(repo_root)
    workflow_name = workflow or config.default_workflow
    overrides = gate_overrides or []
    try:
        resolved_gates = resolve_gates(config, workflow_name, override=overrides or None)
    except KeyError as exc:
        raise exc

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

    published_paths = repo_paths(repo_root)
    staging_dir = staging_task_dir(repo_root, task.id)
    shutil.rmtree(staging_dir, ignore_errors=True)
    staging_paths = task_paths(repo_root, staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    task.save(staging_paths.task_yaml)

    ledger = Ledger(staging_paths.ledger)
    ledger.append(
        {
            "type": "task_started",
            "task_id": task.id,
            "title": task.title,
            "workflow": task.workflow,
        }
    )
    ledger.append(
        {
            "type": "gates_locked",
            "task_id": task.id,
            "gates": gates_locked_payload(resolved_gates),
        }
    )

    adapter = configured_executor_adapter(staging_paths)
    try:
        run = adapter.start(task)
    except RuntimeError as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise StartTaskError(str(exc)) from exc

    try:
        EvidenceStore(staging_paths.evidence).write_run(
            run.run_id,
            {
                "task_id": task.id,
                "adapter": run.adapter,
                "run_id": run.run_id,
                "issue_id": run.issue_id,
            },
        )
        final_record = ledger.append(
            {
                "type": "executor_dispatched",
                "task_id": task.id,
                "adapter": run.adapter,
                "run_id": run.run_id,
                "issue_id": run.issue_id,
            }
        )
        status = TaskStatus.for_started_task(task=task, run=run, ledger_head_hash=final_record["hash"])
        save_status(status, staging_paths)

        shutil.rmtree(published_paths.current_task, ignore_errors=True)
        published_paths.current_task.parent.mkdir(parents=True, exist_ok=True)
        staging_dir.rename(published_paths.current_task)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return task, run
