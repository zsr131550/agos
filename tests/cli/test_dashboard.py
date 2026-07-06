from __future__ import annotations

from typer.testing import CliRunner

from agos.cli.main import app


runner = CliRunner()


def test_dashboard_command_is_registered() -> None:
    result = runner.invoke(app, ["dashboard", "--help"])

    assert result.exit_code == 0
    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--open" in result.stdout


def test_dashboard_command_invokes_server(monkeypatch, tmp_repo) -> None:
    called = {}

    def fake_serve(repo_root, *, host: str, port: int, open_browser: bool) -> str:
        called["repo_root"] = repo_root
        called["host"] = host
        called["port"] = port
        called["open_browser"] = open_browser
        return "http://127.0.0.1:9999"

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_dashboard.serve_dashboard_forever", fake_serve)

    result = runner.invoke(app, ["dashboard", "--host", "127.0.0.1", "--port", "0", "--no-open"])

    assert result.exit_code == 0
    assert called["repo_root"] == tmp_repo
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 0
    assert called["open_browser"] is False
