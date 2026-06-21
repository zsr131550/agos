"""`agos resolve` command."""
from __future__ import annotations

import typer

from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review import FindingResolution
from agos.core.review_service import ReviewService


_STATUS_VALUES = {
    "resolved": "resolved",
    "accepted-risk": "accepted_risk",
    "false-positive": "false_positive",
    "superseded": "superseded",
}


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
        model_status = _STATUS_VALUES.get(status)
        if model_status is None:
            raise ValueError(f"invalid status: {status}")

        repo_root = find_initialized_repo_root()
        resolution = FindingResolution(
            status=model_status,
            evidence_refs=evidence or [],
            rationale=rationale,
            approved_by=approved_by,
        )
        finding = ReviewService(repo_paths(repo_root)).resolve_finding(finding_id, resolution)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"{finding.id} {finding.status}")
