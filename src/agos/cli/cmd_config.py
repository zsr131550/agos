"""`agos config` inspection commands."""
from __future__ import annotations

import json

import typer
import yaml
from pydantic import ValidationError

from agos.core.config import AGOSConfig
from agos.core.repo import find_initialized_repo_root, repo_paths


config_app = typer.Typer(help="Inspect and validate AGOS configuration.")


@config_app.command("show")
def config_show_command(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Print the validated `.agos/agos.yaml` configuration."""

    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        config = AGOSConfig.load(paths.agos_yaml)
    except Exception as exc:
        _report_config_error(exc, json_output=json_output)

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "path": str(paths.agos_yaml),
                    "config": config.model_dump(mode="json"),
                },
                sort_keys=True,
            )
        )
        return

    typer.echo(
        yaml.safe_dump(
            config.model_dump(mode="python"),
            sort_keys=False,
        ).rstrip()
    )


@config_app.command("validate")
def config_validate_command(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Validate `.agos/agos.yaml` and report the result."""

    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        AGOSConfig.load(paths.agos_yaml)
    except Exception as exc:
        _report_config_error(exc, json_output=json_output)

    if json_output:
        typer.echo(json.dumps({"ok": True, "path": str(paths.agos_yaml)}, sort_keys=True))
    else:
        typer.echo("AGOS configuration valid")


def _report_config_error(exc: Exception, *, json_output: bool) -> None:
    message = _safe_config_error(exc)
    if json_output:
        typer.echo(json.dumps({"ok": False, "error": message}, sort_keys=True))
    else:
        typer.echo(message, err=True)
    raise typer.Exit(code=2)


def _safe_config_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return f"invalid AGOS configuration: {exc}"
    return str(exc)
