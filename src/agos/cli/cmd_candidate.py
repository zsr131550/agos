"""`agos candidate` commands."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from agos.adapters.workers import LocalWorktreeWorkerAdapter
from agos.core.execution_service import ExecutionService
from agos.core.execution_store import ExecutionStore
from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review import Finding


candidate_app = typer.Typer(help="Inspect and manage execution candidates.")
merge_app = typer.Typer(help="Decide and apply candidate bundles.")


@candidate_app.command("list")
def candidate_list_command() -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        store = ExecutionStore(paths)
        candidates = store.read_candidates()
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if not candidates:
        typer.echo("No candidates found")
        return

    for candidate in candidates:
        typer.echo(f"{candidate.id} {candidate.status} {candidate.subtask_id} {candidate.summary}")


@candidate_app.command("submit")
def candidate_submit_command(
    subtask_id: str,
    summary: str = typer.Option("", "--summary", help="Candidate summary."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        service = ExecutionService(paths)
        service.register_worker_adapter(
            LocalWorktreeWorkerAdapter(service.workspace_manager),
        )
        candidate = service.submit_candidate(
            subtask_id,
            summary=summary,
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(candidate.id)


@candidate_app.command("test")
def candidate_test_command(
    candidate_id: str,
    gate: str | None = typer.Option(None, "--gate", help="Optional locked gate id."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        runs = ExecutionService(paths).test_candidate(candidate_id, gate_id=gate)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    for run in runs:
        typer.echo(f"{run.gate_id}: {run.state}")


@candidate_app.command("review")
def candidate_review_command(
    candidate_id: str,
    packet_only: bool = typer.Option(False, "--packet-only", help="Create a review packet."),
    ingest: Path | None = typer.Option(None, "--ingest", help="Ingest normalized findings JSON."),
    review_id: str | None = typer.Option(None, "--review-id", help="Review id for ingested findings."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        service = ExecutionService(paths)
        if packet_only and ingest is not None:
            raise ValueError("--packet-only and --ingest cannot be used together")
        if packet_only:
            packet_ref, _packet = service.review_candidate(candidate_id)
            typer.echo(packet_ref)
            return
        if ingest is None:
            raise ValueError("Use --packet-only or --ingest <file>")
        if review_id is None:
            raise ValueError("--review-id is required with --ingest")
        payload = json.loads(ingest.read_text(encoding="utf-8"))
        findings = [Finding.model_validate(item) for item in payload["findings"]]
        report_ref, report = service.ingest_candidate_review(
            candidate_id,
            review_id,
            findings=findings,
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(report_ref)
    for finding in report.findings:
        typer.echo(f"{finding.id}: {finding.title}")


@candidate_app.command("decide")
def candidate_decide_command(
    candidate_id: str,
    decision: str = typer.Option(..., "--decision", help="accepted, rejected, superseded, or needs-changes."),
    reason: str = typer.Option(..., "--reason", help="Decision rationale."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        outcome = ExecutionService(paths).decide_candidate(
            candidate_id,
            decision=decision.replace("-", "_"),
            reason=reason,
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"{outcome.id} {outcome.decision}")


@candidate_app.command("apply")
def candidate_apply_command(candidate_id: str) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        candidate = ExecutionService(paths).apply_candidate(candidate_id)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"{candidate.id} applied")


@merge_app.command("decide")
def candidate_merge_decide_command(candidate_ids: list[str] = typer.Argument(None)) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        decision = ExecutionService(paths).decide_candidate_bundle(candidate_ids or None)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"{decision.id} {decision.strategy} {' '.join(decision.candidate_ids)}")


@merge_app.command("apply")
def candidate_merge_apply_command(bundle_decision_id: str) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        candidates = ExecutionService(paths).apply_candidate_bundle(bundle_decision_id)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("applied " + " ".join(candidate.id for candidate in candidates))


candidate_app.add_typer(merge_app, name="merge")
