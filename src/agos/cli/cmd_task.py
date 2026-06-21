"""`agos task` maintenance commands."""
from __future__ import annotations

import shutil

import typer

from agos.core.repo import find_repo_root, repo_paths
from agos.core.status import load_status


task_app = typer.Typer(help="Inspect or clear the active AGOS task.")


@task_app.command("status")
def task_status_command() -> None:
    """Print the active task status cache when one exists."""

    repo_root = find_repo_root()
    paths = repo_paths(repo_root)
    status = load_status(paths)
    if status is None:
        typer.echo("No active AGOS task found")
        return
    typer.echo(status.model_dump_json(indent=2))


@task_app.command("clear")
def task_clear_command(
    force: bool = typer.Option(False, "--force", help="Clear .agos/tasks/current."),
) -> None:
    """Clear the active task directory after explicit confirmation via --force."""

    if not force:
        typer.echo("Use --force to clear .agos/tasks/current", err=True)
        raise typer.Exit(code=2)

    repo_root = find_repo_root()
    paths = repo_paths(repo_root)
    shutil.rmtree(paths.current_task, ignore_errors=True)
    paths.current_task.mkdir(parents=True, exist_ok=True)
    typer.echo("Cleared active AGOS task")
