"""`agos resolve` command."""
from __future__ import annotations

import typer

from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review import FindingResolution
from agos.core.review_service import ReviewService


def resolve_command(
    finding_id: str,
    status: str = typer.Option(
        ...,
        "--status",
        help="resolved, accepted-risk, false-positive, or superseded.",
    ),
    evidence: list[str] | None = typer.Option(
        None,
        "--evidence",
        help="Evidence ref supporting resolution.",
    ),
    rationale: str = typer.Option(
        ...,
        "--rationale",
        help="Resolution rationale.",
    ),
    approved_by: str | None = typer.Option(
        None,
        "--approved-by",
        help="Required for accepted-risk.",
    ),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        resolution = FindingResolution(
            status=status.replace("-", "_"),
            evidence_refs=evidence or [],
            rationale=rationale,
            approved_by=approved_by,
        )
        finding = ReviewService(repo_paths(repo_root)).resolve_finding(finding_id, resolution)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"{finding.id} {finding.status}")
