"""`agos ci --local` command."""
from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from agos.core.config import load_config, resolve_gates
from agos.core.gate import GateContext, build_gate, gates_match
from agos.core.ledger import Ledger, LedgerTamperError, append_task_record
from agos.core.repo import find_repo_root, repo_paths
from agos.core.status import GateState, load_status, save_status
from agos.core.task import load_task


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
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
        has_upstream = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "@{u}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        command = ["git", "diff", "@{u}..HEAD"] if has_upstream.returncode == 0 else ["git", "diff", "HEAD"]
    else:
        raise ValueError(f"unsupported stage: {stage}")

    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


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
    status = load_status(paths)
    if status is None:
        return

    ledger = Ledger(paths.ledger)
    try:
        ledger.verify_chain()
    except LedgerTamperError as exc:
        typer.echo(f"Ledger verification failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    task = load_task(paths.task_yaml)
    config = load_config(repo_root)
    try:
        resolved_gates = resolve_gates(config, task.workflow, override=task.gates)
    except KeyError as exc:
        typer.echo(f"Current gate set does not match gates_locked: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    locked_records = [record for record in ledger.read_all() if record.get("type") == "gates_locked"]
    locked = locked_records[-1]["gates"] if locked_records else []
    if not gates_match(locked, resolved_gates):
        typer.echo("Current gate set does not match gates_locked", err=True)
        raise typer.Exit(code=1)

    checkpoint_heads = [
        record.get("repo_head")
        for record in ledger.read_all()
        if record.get("type") == "checkpoint" and record.get("repo_head")
    ]
    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    for repo_head in checkpoint_heads:
        if not _is_ancestor(repo_root, repo_head, current_head):
            record = append_task_record(
                paths.ledger,
                "repo_history_drift",
                stage=stage,
                checkpoint_repo_head=repo_head,
                current_repo_head=current_head,
            )
            status.ledger_head_hash = record["hash"]
            save_status(status, paths)
            typer.echo("repo_history_drift: checkpoint repo head is not an ancestor of current HEAD", err=True)
            raise typer.Exit(code=1)

    diff = _git_diff_for_stage(repo_root, stage)
    applicable = [gate for gate in resolved_gates if stage in gate.stage]

    exit_code = 0
    gate_states = dict(status.gates)
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
        record = append_task_record(
            paths.ledger,
            "gate_evaluated",
            gate=gate_spec.id,
            stage=stage,
            state=result.state,
            reason=result.reason,
            evidence_path=result.evidence_path,
        )
        gate_states[gate_spec.id] = GateState(state=result.state, last_evaluated=record["ts"])
        status.ledger_head_hash = record["hash"]
        if result.state == "block":
            exit_code = 1
            typer.echo(result.reason, err=True)

    status.gates = gate_states
    save_status(status, paths)
    if exit_code:
        raise typer.Exit(code=exit_code)
