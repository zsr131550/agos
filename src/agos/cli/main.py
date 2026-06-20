"""AGOS CLI entrypoint."""
from __future__ import annotations

import typer

from agos import __version__

app = typer.Typer(
    name="agos",
    help="Executor-agnostic governance layer for AI coding agents.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """AGOS CLI command group."""


@app.command()
def version() -> None:
    """Print the AGOS version and exit."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
