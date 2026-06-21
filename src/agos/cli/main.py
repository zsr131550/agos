"""AGOS CLI entrypoint."""
from __future__ import annotations

import typer

from agos import __version__
from agos.cli.cmd_ci import ci_local_command
from agos.cli.cmd_checkpoint import checkpoint_command
from agos.cli.cmd_init import init_command
from agos.cli.cmd_start import start_command
from agos.cli.cmd_task import task_app

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


app.command("init")(init_command)
app.command("start")(start_command)
app.command("checkpoint")(checkpoint_command)
app.command("ci")(ci_local_command)
app.add_typer(task_app, name="task")


if __name__ == "__main__":
    app()
