"""`agos review` command."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from agos.adapters.reviewers import ManualReviewerAdapter
from agos.cli.reviewer_registry import configured_reviewer_adapters, configured_reviewer_specs
from agos.backends.native_async import NativeAsyncBackend
from agos.core.orchestration.registry import OrchestrationRegistry
from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review import Finding
from agos.core.review_orchestration import ReviewOrchestrator
from agos.core.review_orchestrator import ParallelReviewOrchestrator, ReviewerSpec
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
    reviewer: list[str] = typer.Option([], "--reviewer", help="Configured reviewer ids to run."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        adapters = configured_reviewer_adapters(repo_root)
        configured_specs = configured_reviewer_specs(repo_root)
        if not configured_specs:
            orchestrator = ReviewOrchestrator(repo_paths(repo_root), registry=_review_registry())
            run = orchestrator.start_manual_review(
                diff_kind="governed_repo_diff",
                reviewers=reviewer,
            )
            typer.echo(_serialize_review_run(run))
            return

        specs = _selected_reviewer_specs(configured_specs, reviewer)
        manual_specs, auto_specs = _partition_reviewer_specs(specs, adapters)
        if manual_specs and auto_specs:
            raise ValueError("manual and automated reviewers cannot be combined in review run")
        if manual_specs:
            orchestrator = ReviewOrchestrator(repo_paths(repo_root), registry=_review_registry())
            run = orchestrator.start_manual_review(
                diff_kind="governed_repo_diff",
                reviewers=[spec.id for spec in manual_specs],
            )
            typer.echo(_serialize_review_run(run))
            return
        if not auto_specs:
            raise ValueError("at least one configured reviewer is required")

        service = ReviewService(repo_paths(repo_root))
        packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")
        run_id = _configured_review_run_id(packet.review_id)
        result = ParallelReviewOrchestrator(adapters).run(
            run_id=run_id,
            packet=packet,
            reviewers=auto_specs,
        )
        if result.state != "completed":
            failed = ", ".join(result.failed_reviewers)
            raise ValueError(f"required reviewers failed: {failed}")
        report_ref, report = service.ingest_findings(packet.review_id, result.findings)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        json.dumps(
            {
                "backend": "configured_reviewers",
                "kind": "review_run",
                "run_id": result.run_id,
                "review_id": packet.review_id,
                "packet_ref": packet_ref,
                "report_ref": report_ref,
                "reviewers": [spec.id for spec in auto_specs],
                "state": result.state,
                "finding_count": len(report.findings),
            },
            sort_keys=True,
        )
    )


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


def _selected_reviewer_specs(
    specs: list[ReviewerSpec],
    selected: list[str],
) -> list[ReviewerSpec]:
    if not selected:
        return specs
    by_id = {spec.id: spec for spec in specs}
    missing = [reviewer_id for reviewer_id in selected if reviewer_id not in by_id]
    if missing:
        raise ValueError(f"unknown configured reviewer: {', '.join(missing)}")
    return [by_id[reviewer_id] for reviewer_id in selected]


def _partition_reviewer_specs(
    specs: list[ReviewerSpec],
    adapters: dict[str, object],
) -> tuple[list[ReviewerSpec], list[ReviewerSpec]]:
    manual_specs: list[ReviewerSpec] = []
    auto_specs: list[ReviewerSpec] = []
    for spec in specs:
        adapter = adapters.get(spec.adapter)
        if isinstance(adapter, ManualReviewerAdapter):
            manual_specs.append(spec)
        else:
            auto_specs.append(spec)
    return manual_specs, auto_specs


def _configured_review_run_id(review_id: str) -> str:
    suffix = review_id.removeprefix("review-")
    return f"review-run-{suffix}"
