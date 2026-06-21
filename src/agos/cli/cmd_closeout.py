"""`agos closeout` command."""
from __future__ import annotations

import typer

from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review_service import ReviewService


def closeout_command() -> None:
    try:
        repo_root = find_initialized_repo_root()
        proof = ReviewService(repo_paths(repo_root)).closeout()
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"proof.json written for {proof.task_id}")
