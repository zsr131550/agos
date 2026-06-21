"""Smoke test: package imports and CLI runs."""
from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import agos
from agos import __version__
from agos.cli.main import app

runner = CliRunner()


def test_version_prints():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_imports_local_src_tree():
    repo_root = Path(__file__).resolve().parents[1]
    assert Path(agos.__file__).resolve().is_relative_to(repo_root / "src")
