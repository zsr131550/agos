"""AGOS CLI entrypoint."""
from __future__ import annotations

import typer

from agos import __version__
from agos.cli.cmd_candidate import candidate_app
from agos.cli.cmd_closeout import closeout_command
from agos.cli.cmd_ci import ci_local_command
from agos.cli.cmd_checkpoint import checkpoint_command
from agos.cli.cmd_execute_plan import execute_plan_app
from agos.cli.cmd_init import init_command
from agos.cli.cmd_resolve import resolve_command
from agos.cli.cmd_review import review_app
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
app.add_typer(execute_plan_app, name="execute-plan")
app.command("ci")(ci_local_command)
app.command("resolve")(resolve_command)
app.command("closeout")(closeout_command)
app.add_typer(candidate_app, name="candidate")
app.add_typer(review_app, name="review")
app.add_typer(task_app, name="task")


if __name__ == "__main__":
    app()
