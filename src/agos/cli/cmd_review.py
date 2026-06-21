"""`agos review` command."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review import Finding
from agos.core.review_service import ReviewService


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
