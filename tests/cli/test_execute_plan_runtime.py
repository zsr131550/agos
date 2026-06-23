from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


runner = CliRunner()


def test_execute_plan_runtime_run_status_resume_and_cancel(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    started = runner.invoke(app, ["execute-plan", "run", "--plan", str(_plan_file(tmp_repo))])

    assert started.exit_code == 0
    assert "execution-run-" in started.stdout
    run_id = started.stdout.split()[0]

    status = runner.invoke(app, ["execute-plan", "status", run_id])
    resume = runner.invoke(app, ["execute-plan", "resume", run_id])
    cancel = runner.invoke(app, ["execute-plan", "cancel", run_id])

    assert status.exit_code == 0
    assert "completed: subtask-readme" in status.stdout
    assert resume.exit_code == 0
    assert run_id in resume.stdout
    assert cancel.exit_code == 0
    assert run_id in cancel.stdout


def test_execute_plan_run_uses_configured_external_orchestration_backend(monkeypatch, tmp_repo):
    _active_task(tmp_repo, orchestration={"backend": "external"})
    monkeypatch.chdir(tmp_repo)

    started = runner.invoke(
        app,
        ["execute-plan", "run", "--plan", str(_plan_file(tmp_repo)), "--json"],
    )

    assert started.exit_code == 0, started.stderr
    payload = json.loads(started.stdout)
    assert payload["backend"] == "external"
    assert payload["state"] == "queued"
    assert payload["completed_subtasks"] == []

    status = runner.invoke(app, ["execute-plan", "status", payload["run_id"], "--json"])
    cancelled = runner.invoke(app, ["execute-plan", "cancel", payload["run_id"], "--json"])

    assert status.exit_code == 0, status.stderr
    assert json.loads(status.stdout)["backend"] == "external"
    assert cancelled.exit_code == 0, cancelled.stderr
    assert json.loads(cancelled.stdout)["state"] == "cancelled"


def _active_task(tmp_repo: Path, *, orchestration: dict[str, object] | None = None):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workers": {"local_worktree": {"type": "local_worktree"}},
                "orchestration": orchestration or {"backend": "native_async"},
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task = Task(
        id="agos-01",
        title="Execution runtime CLI task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    status = TaskStatus.for_started_task(
        task=task,
        run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
        ledger_head_hash=started["hash"],
    )
    save_status(status, paths)
    return paths


def _plan_file(tmp_repo: Path) -> Path:
    path = tmp_repo / "execution-plan.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "execution-plan-01",
                "task_id": "agos-01",
                "subtasks": [
                    {
                        "id": "subtask-readme",
                        "title": "Update README",
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path
