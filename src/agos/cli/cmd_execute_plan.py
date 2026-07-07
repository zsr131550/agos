"""`agos execute-plan` commands."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from agos.cli.orchestration_registry import register_configured_orchestration_backends
from agos.cli.planner_registry import configured_planner_adapter
from agos.cli.reviewer_registry import configured_reviewer_adapters, configured_reviewer_specs
from agos.cli.worker_registry import register_configured_worker_adapters
from agos.core.execution_pipeline import AutoExecutionResult, run_auto_execution
from agos.core.execution_runtime import ExecutionRuntimeSnapshot
from agos.core.execution_service import ExecutionService
from agos.core.repo import find_initialized_repo_root, repo_paths


execute_plan_app = typer.Typer(
    help="Create workspaces and run execution-plan workers.",
    invoke_without_command=True,
)
run_app = typer.Typer(
    help="Run and inspect execution-plan workers.",
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


def run_auto_command(
    dry_run: bool = typer.Option(False, "--dry-run", help="Run without applying accepted candidates."),
    apply: bool = typer.Option(False, "--apply", help="Apply accepted candidates to the governed repo."),
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
    planner_json: str | None = typer.Option(
        None,
        "--planner-json",
        help="Planner output JSON to seed automatic execution planning.",
    ),
    allow_missing_review: bool = typer.Option(
        False,
        "--allow-missing-review",
        help="Allow acceptance when no review adapters are configured.",
    ),
) -> None:
    if dry_run and apply:
        typer.echo("--dry-run and --apply are mutually exclusive", err=True)
        raise typer.Exit(code=2)
    try:
        service = _service()
        result = run_auto_execution(
            service,
            apply=apply,
            allow_missing_review=allow_missing_review,
            planner_json=planner_json,
            planner=configured_planner_adapter(service.paths.root),
            reviewer_adapters=configured_reviewer_adapters(service.paths.root),
            reviewer_specs=configured_reviewer_specs(service.paths.root),
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(result.model_dump_json() if json_output else _format_auto_result(result))


def _service() -> ExecutionService:
    repo_root = find_initialized_repo_root()
    paths = repo_paths(repo_root)
    service = ExecutionService(paths)
    register_configured_worker_adapters(service)
    register_configured_orchestration_backends(service)
    return service


def _format_snapshot(snapshot: ExecutionRuntimeSnapshot) -> str:
    parts = [snapshot.run_id]
    parts.append(f"backend: {snapshot.backend}")
    parts.append(f"state: {snapshot.state}")
    parts.append(f"running: {_join(snapshot.running_subtasks)}")
    parts.append(f"completed: {_join(snapshot.completed_subtasks)}")
    parts.append(f"failed: {_join(snapshot.failed_subtasks)}")
    parts.append(f"cancelled: {_join(snapshot.cancelled_subtasks)}")
    if snapshot.waiting_nodes or snapshot.completed_nodes or snapshot.failed_nodes:
        parts.append(f"waiting nodes: {_join(snapshot.waiting_nodes)}")
        parts.append(f"completed nodes: {_join(snapshot.completed_nodes)}")
        parts.append(f"failed nodes: {_join(snapshot.failed_nodes)}")
    if snapshot.output_refs:
        refs = ", ".join(f"{key}={value}" for key, value in sorted(snapshot.output_refs.items()))
        parts.append(f"output refs: {refs}")
    return " | ".join(parts)


def _snapshot_json(snapshot: ExecutionRuntimeSnapshot) -> str:
    return json.dumps(
        {
            "run_id": snapshot.run_id,
            "backend": snapshot.backend,
            "state": snapshot.state,
            "running_subtasks": list(snapshot.running_subtasks),
            "completed_subtasks": list(snapshot.completed_subtasks),
            "failed_subtasks": list(snapshot.failed_subtasks),
            "cancelled_subtasks": list(snapshot.cancelled_subtasks),
            "waiting_nodes": list(snapshot.waiting_nodes),
            "completed_nodes": list(snapshot.completed_nodes),
            "failed_nodes": list(snapshot.failed_nodes),
            "output_refs": snapshot.output_refs,
        },
        sort_keys=True,
    )


def _format_auto_result(result: AutoExecutionResult) -> str:
    parts = [
        result.plan_id,
        f"run: {result.run_id}",
        f"state: {result.run_state}",
        f"mode: {'dry-run' if result.dry_run else 'apply'}",
        f"planner: {result.planner_source}",
        f"workers: {_join_mapping(result.subtask_worker_assignments)}",
        f"completed: {_join(tuple(result.completed_subtasks))}",
        f"failed: {_join(tuple(result.failed_subtasks))}",
        f"candidates: {_join(tuple(result.candidate_ids))}",
        f"reviewers: {_join(tuple(result.reviewer_ids))}",
        f"reviews: {_join_mapping(result.candidate_review_ids)}",
        f"accepted: {_join(tuple(result.accepted_candidate_ids))}",
        f"applied: {_join(tuple(result.applied_candidate_ids))}",
        f"blocked: {_format_block(result)}",
    ]
    if result.notes:
        parts.append("notes: " + " | ".join(result.notes))
    return " | ".join(parts)


def _join(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "-"


def _join_mapping(values: dict[str, str]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items())) if values else "-"


def _format_block(result: AutoExecutionResult) -> str:
    if result.blocked_stage is None:
        return "-"
    if result.blocked_reason:
        return f"{result.blocked_stage}: {result.blocked_reason}"
    return result.blocked_stage


run_app.callback(invoke_without_command=True)(execute_plan_command)
run_app.command("start")(execute_plan_run_command)
run_app.command("run")(execute_plan_run_command)
run_app.command("auto")(run_auto_command)
run_app.command("resume")(execute_plan_resume_command)
run_app.command("status")(execute_plan_status_command)
run_app.command("cancel")(execute_plan_cancel_command)
