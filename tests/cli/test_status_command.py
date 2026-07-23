from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


runner = CliRunner()


def test_status_json_reports_uninitialized_repo(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["repo_root"] == str(tmp_repo)
    assert payload["initialized"] is False
    assert payload["active_task"] is False
    assert payload["task"] is None


def test_status_json_reports_active_task(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["initialized"] is True
    assert payload["active_task"] is True
    assert payload["task"]["task_id"] == "agos-01"
    assert payload["task"]["phase"] == "executing"
    assert payload["task"]["executor_run"]["run_id"] == "run-01"


def test_status_human_reports_active_task(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "initialized: yes" in result.stdout
    assert "active task: agos-01" in result.stdout
    assert "phase: executing" in result.stdout


def test_status_command_repairs_stale_cache_from_terminal_ledger_event(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    terminal = Ledger(paths.ledger).append(
        {"type": "executor_completed", "run_id": "run-01", "state": "completed"}
    )
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["task"]["phase"] == "done"
    assert payload["task"]["executor_run"]["run_id"] == "run-01"
    assert payload["task"]["ledger_head_hash"] == terminal["hash"]


def _active_task(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workflows": {"feature": {"gates": []}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task = Task(
        id="agos-01",
        title="CLI status task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    dispatched = ledger.append(
        {
            "type": "executor_dispatched",
            "task_id": task.id,
            "adapter": "multica",
            "run_id": "run-01",
            "issue_id": "AGO-1",
        }
    )
    status = TaskStatus.for_started_task(
        task=task,
        run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
        ledger_head_hash=dispatched["hash"],
    )
    save_status(status, paths)
    return paths
