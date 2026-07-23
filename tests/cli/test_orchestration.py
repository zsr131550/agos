from __future__ import annotations

import json

from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


runner = CliRunner()


def _active_task(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text("executor:\n  name: multica\n  agent: Lambda\n", encoding="utf-8")
    task = Task(
        id="agos-01",
        title="Review CLI task",
        intent="Expose review orchestration from the CLI",
        acceptance=["review orchestration run ids are printed"],
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


def test_review_run_starts_orchestration_and_prints_run_id(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(
        app,
        ["review", "run", "--reviewer", "security_reviewer", "--reviewer", "test_reviewer"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["backend"] == "native_async"
    assert payload["kind"] == "review_run"
    assert payload["reviewers"] == ["security_reviewer", "test_reviewer"]
    assert payload["packet_ref"].startswith("reviews/review-")
    assert (paths.orchestration_runs / f"{payload['run_id']}.json").exists()


def test_review_resume_restarts_persisted_run(monkeypatch, tmp_repo):
    _paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    started = runner.invoke(app, ["review", "run", "--reviewer", "security_reviewer"])
    started_payload = json.loads(started.stdout)

    result = runner.invoke(app, ["review", "resume", started_payload["run_id"]])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["backend"] == "native_async"
    assert payload["kind"] == "review_run"
    assert payload["run_id"] == started_payload["run_id"]
    assert payload["review_id"] == started_payload["review_id"]
