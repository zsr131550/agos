"""Smoke test: package imports and CLI runs."""
from __future__ import annotations

from typer.testing import CliRunner

from agos import __version__
from agos.cli.main import app

runner = CliRunner()


def test_version_prints():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
