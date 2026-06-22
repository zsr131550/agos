"""`agos review` command."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from agos.backends.native_async import NativeAsyncBackend
from agos.core.orchestration.registry import OrchestrationRegistry
from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review import Finding
from agos.core.review_orchestration import ReviewOrchestrator
from agos.core.review_service import ReviewService


review_app = typer.Typer(
    help="Review orchestration commands.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _review_registry() -> OrchestrationRegistry:
    registry = OrchestrationRegistry()
    registry.register_orchestration(NativeAsyncBackend())
    return registry


def _serialize_review_run(run) -> str:
    return json.dumps(
        {
            "backend": run.backend,
            "kind": run.kind,
            "run_id": run.run_id,
            "review_id": run.review_id,
            "packet_ref": run.packet_ref,
            "reviewers": run.reviewers,
        }
    )


def review_command(
    packet_only: bool = typer.Option(
        False,
        "--packet-only",
        help="Create a review packet and exit.",
    ),
    ingest: Path | None = typer.Option(
        None,
        "--ingest",
        help="Ingest normalized findings JSON.",
    ),
    review_id: str | None = typer.Option(
        None,
        "--review-id",
        help="Review id for ingested findings.",
    ),
) -> None:
    _review_command_impl(packet_only=packet_only, ingest=ingest, review_id=review_id)


@review_app.callback()
def review_app_callback(
    ctx: typer.Context,
    packet_only: bool = typer.Option(
        False,
        "--packet-only",
        help="Create a review packet and exit.",
    ),
    ingest: Path | None = typer.Option(
        None,
        "--ingest",
        help="Ingest normalized findings JSON.",
    ),
    review_id: str | None = typer.Option(
        None,
        "--review-id",
        help="Review id for ingested findings.",
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    _review_command_impl(packet_only=packet_only, ingest=ingest, review_id=review_id)


def _review_command_impl(
    *,
    packet_only: bool,
    ingest: Path | None,
    review_id: str | None,
) -> None:
    try:
        repo_root = find_initialized_repo_root()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if packet_only and ingest is not None:
        typer.echo("--packet-only and --ingest cannot be used together", err=True)
        raise typer.Exit(code=2)

    service = ReviewService(repo_paths(repo_root))

    if packet_only:
        try:
            packet_ref, _packet = service.create_packet(diff_kind="governed_repo_diff")
        except Exception as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(packet_ref)
        return

    if ingest is None:
        typer.echo("Use --packet-only or --ingest <file>", err=True)
        raise typer.Exit(code=2)

    if review_id is None:
        typer.echo("--review-id is required with --ingest", err=True)
        raise typer.Exit(code=2)

    try:
        payload = json.loads(ingest.read_text(encoding="utf-8"))
        findings = [Finding.model_validate(item) for item in payload["findings"]]
        report_ref, report = service.ingest_findings(review_id, findings)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(report_ref)
    for finding in report.findings:
        typer.echo(f"{finding.id}: {finding.title}")


@review_app.command("run")
def review_run_command(
    reviewer: list[str] = typer.Option([], "--reviewer", help="Manual reviewer adapter names."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        orchestrator = ReviewOrchestrator(repo_paths(repo_root), registry=_review_registry())
        run = orchestrator.start_manual_review(
            diff_kind="governed_repo_diff",
            reviewers=reviewer,
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(_serialize_review_run(run))


@review_app.command("resume")
def review_resume_command(run_id: str) -> None:
    try:
        repo_root = find_initialized_repo_root()
        orchestrator = ReviewOrchestrator(repo_paths(repo_root), registry=_review_registry())
        run = orchestrator.resume_manual_review(run_id)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(_serialize_review_run(run))
