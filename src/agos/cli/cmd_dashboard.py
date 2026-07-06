"""`agos dashboard` command."""
from __future__ import annotations

import typer

from agos.core.repo import find_repo_root
from agos.web.server import serve_dashboard_forever


def dashboard_command(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface to bind."),
    port: int = typer.Option(8788, "--port", min=0, help="Port to bind; 0 selects a free port."),
    open_browser: bool = typer.Option(
        False,
        "--open/--no-open",
        help="Open the dashboard URL in a browser.",
    ),
) -> None:
    """Start the local read-only AGOS dashboard."""

    try:
        repo_root = find_repo_root()
        serve_dashboard_forever(repo_root, host=host, port=port, open_browser=open_browser)
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
