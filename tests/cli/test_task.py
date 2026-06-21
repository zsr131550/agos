from __future__ import annotations

from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.repo import repo_paths

runner = CliRunner()


def test_task_status_reports_no_active_task(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["task", "status"])

    assert result.exit_code == 0
    assert "No active AGOS task found" in result.stdout


def test_task_clear_requires_force(monkeypatch, tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.current_task.mkdir(parents=True)
    (paths.current_task / "task.yaml").write_text("id: stale\n", encoding="utf-8")
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["task", "clear"])

    assert result.exit_code == 2
    assert "Use --force" in result.stderr
    assert (paths.current_task / "task.yaml").exists()


def test_task_clear_force_removes_current_task(monkeypatch, tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.current_task.mkdir(parents=True)
    (paths.current_task / "task.yaml").write_text("id: stale\n", encoding="utf-8")
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["task", "clear", "--force"])

    assert result.exit_code == 0
    assert "Cleared active AGOS task" in result.stdout
    assert not paths.current_task.exists() or not any(paths.current_task.iterdir())
