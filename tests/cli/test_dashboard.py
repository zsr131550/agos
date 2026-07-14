from __future__ import annotations

from typer.testing import CliRunner

from agos.cli.main import app


runner = CliRunner()


def test_dashboard_command_is_registered() -> None:
    result = runner.invoke(app, ["dashboard", "--help"])

    assert result.exit_code == 0


def test_dashboard_command_invokes_server(monkeypatch, tmp_repo) -> None:
    called = {}

    def fake_serve(
        repo_root,
        *,
        host: str,
        port: int,
        open_browser: bool,
        token: str | None,
    ) -> str:
        called["repo_root"] = repo_root
        called["host"] = host
        called["port"] = port
        called["open_browser"] = open_browser
        called["token"] = token
        return "http://127.0.0.1:9999"

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_dashboard.serve_dashboard_forever", fake_serve)

    result = runner.invoke(
        app,
        [
            "dashboard",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--no-open",
            "--token",
            "test-dashboard-token",
        ],
    )

    assert result.exit_code == 0
    assert called["repo_root"] == tmp_repo
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 0
    assert called["open_browser"] is False
    assert called["token"] == "test-dashboard-token"


def test_dashboard_command_reads_token_from_environment(monkeypatch, tmp_repo) -> None:
    called = {}

    def fake_serve(repo_root, **kwargs) -> str:
        called.update(kwargs)
        return "http://127.0.0.1:9999"

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_dashboard.serve_dashboard_forever", fake_serve)

    result = runner.invoke(
        app,
        ["dashboard", "--no-open"],
        env={"AGOS_DASHBOARD_TOKEN": "environment-dashboard-token"},
    )

    assert result.exit_code == 0
    assert called["token"] == "environment-dashboard-token"


def test_dashboard_command_exits_zero_on_keyboard_interrupt(monkeypatch, tmp_repo) -> None:
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_dashboard.serve_dashboard_forever", interrupt)

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 0


def test_dashboard_command_reports_server_error(monkeypatch, tmp_repo) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("bind failed")

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_dashboard.serve_dashboard_forever", fail)

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 1
    assert "bind failed" in result.stderr
