"""Top-level `agos status` command."""
from __future__ import annotations

import json

import typer

from agos.core.repo import find_repo_root, repo_paths
from agos.core.status import Status, load_status


def status_command(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Print current repository and active-task status."""

    try:
        repo_root = find_repo_root()
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    paths = repo_paths(repo_root)
    initialized = paths.agos_yaml.is_file()
    status = load_status(paths) if initialized else None
    payload = _status_payload(repo_root=str(repo_root), initialized=initialized, status=status)

    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
        return

    typer.echo(_format_status(payload))


def _status_payload(*, repo_root: str, initialized: bool, status: Status | None) -> dict[str, object]:
    return {
        "repo_root": repo_root,
        "initialized": initialized,
        "active_task": status is not None,
        "task": status.model_dump(mode="json") if status is not None else None,
    }


def _format_status(payload: dict[str, object]) -> str:
    lines = [
        f"repo: {payload['repo_root']}",
        f"initialized: {'yes' if payload['initialized'] else 'no'}",
    ]
    task = payload["task"]
    if isinstance(task, dict):
        lines.append(f"active task: {task['task_id']}")
        lines.append(f"phase: {task['phase']}")
    else:
        lines.append("active task: none")
    return "\n".join(lines)
