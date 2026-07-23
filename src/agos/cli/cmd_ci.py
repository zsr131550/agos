"""`agos ci --local` command."""

from __future__ import annotations

from pathlib import Path

import typer

from agos.core.command import run_command
from agos.core.config import load_config, resolve_gates
from agos.core.gate import GateContext, build_gate, gates_match
from agos.core.ledger import Ledger, LedgerTamperError
from agos.core.repo import find_repo_root, repo_paths
from agos.core.task_state import TaskEvent, TaskState


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    completed = run_command(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _git_diff_for_stage(repo_root, stage: str) -> str:
    if stage == "pre-commit":
        command = ["git", "diff", "--cached"]
    elif stage == "pre-push":
        has_upstream = run_command(
            ["git", "rev-parse", "--verify", "--quiet", "@{u}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        command = (
            ["git", "diff", "@{u}..HEAD"]
            if has_upstream.returncode == 0
            else ["git", "diff", "HEAD"]
        )
    else:
        raise ValueError(f"unsupported stage: {stage}")

    completed = run_command(
        command,
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout or ""


def ci_local_command(
    local: bool = typer.Option(
        False,
        "--local",
        help="Run the local advisory gate checks.",
    ),
    stage: str = typer.Option(
        ...,
        "--stage",
        help="Git hook stage to evaluate.",
    ),
) -> None:
    """Evaluate local advisory gates for a human developer's commit or push."""

    if not local:
        typer.echo("Only --local is supported in v0.1", err=True)
        raise typer.Exit(code=2)

    repo_root = find_repo_root()
    paths = repo_paths(repo_root)
    task_state = TaskState(paths)
    ledger = Ledger(paths.ledger)
    try:
        snapshot = task_state.current()
        if snapshot is None:
            return
        records = ledger.read_verified()
    except LedgerTamperError as exc:
        typer.echo(f"Ledger verification failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    task = snapshot.task
    config = load_config(repo_root)
    try:
        resolved_gates = resolve_gates(config, task.workflow, override=task.gates)
    except KeyError as exc:
        typer.echo(f"Current gate set does not match gates_locked: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    locked_records = [record for record in records if record.get("type") == "gates_locked"]
    locked = locked_records[-1]["gates"] if locked_records else []
    if not gates_match(locked, resolved_gates):
        typer.echo("Current gate set does not match gates_locked", err=True)
        raise typer.Exit(code=1)

    checkpoint_heads = [
        record.get("repo_head")
        for record in records
        if record.get("type") == "checkpoint" and record.get("repo_head")
    ]
    current_head = run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    for repo_head in checkpoint_heads:
        if not _is_ancestor(repo_root, repo_head, current_head):
            task_state.record(
                TaskEvent(
                    "repo_history_drift",
                    {
                        "task_id": task.id,
                        "stage": stage,
                        "checkpoint_repo_head": repo_head,
                        "current_repo_head": current_head,
                    },
                )
            )
            typer.echo(
                "repo_history_drift: checkpoint repo head is not an ancestor of current HEAD",
                err=True,
            )
            raise typer.Exit(code=1)

    diff = _git_diff_for_stage(repo_root, stage)
    applicable = [gate for gate in resolved_gates if stage in gate.stage]

    exit_code = 0
    gate_events: list[TaskEvent] = []
    for gate_spec in applicable:
        gate = build_gate(gate_spec)
        result = gate.evaluate(
            GateContext(
                repo_root=repo_root,
                stage=stage,
                diff=diff,
                evidence_dir=paths.evidence,
            )
        )
        gate_events.append(
            TaskEvent(
                "gate_evaluated",
                {
                    "task_id": task.id,
                    "gate": gate_spec.id,
                    "stage": stage,
                    "state": result.state,
                    "reason": result.reason,
                    "evidence_path": result.evidence_path,
                },
            )
        )
        if result.state == "block":
            exit_code = 1
            typer.echo(result.reason, err=True)

    if gate_events:
        task_state.record(gate_events[0], *gate_events[1:])
    if exit_code:
        raise typer.Exit(code=exit_code)
