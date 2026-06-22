from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


pytestmark = pytest.mark.integration
runner = CliRunner()


def test_multi_agent_runtime_closed_loop(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    plan = {
        "id": "plan-01",
        "task_id": "agos-01",
        "max_parallel": 2,
        "requires_candidate_review": True,
        "subtasks": [
            {
                "id": "readme",
                "title": "README",
                "write_scope": ["README.md"],
                "worker": {"adapter": "local_worktree"},
            },
            {
                "id": "guide",
                "title": "Guide",
                "write_scope": ["docs/guide.md"],
                "worker": {"adapter": "local_worktree"},
            },
        ],
    }
    plan_path = tmp_repo / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    started = runner.invoke(app, ["execute-plan", "run", "--plan", str(plan_path), "--json"])

    assert started.exit_code == 0
    started_payload = json.loads(started.stdout)
    run_id = started_payload["run_id"]
    assert sorted(started_payload["completed_subtasks"]) == ["guide", "readme"]

    status = runner.invoke(app, ["execute-plan", "status", run_id, "--json"])
    resumed = runner.invoke(app, ["execute-plan", "resume", run_id, "--json"])
    cancelled = runner.invoke(app, ["execute-plan", "cancel", run_id, "--json"])

    assert status.exit_code == 0
    assert resumed.exit_code == 0
    assert cancelled.exit_code == 0
    assert json.loads(status.stdout)["run_id"] == run_id
    assert json.loads(resumed.stdout)["completed_subtasks"] == ["readme", "guide"]
    assert json.loads(cancelled.stdout)["run_id"] == run_id
    assert (paths.current_task / "execution" / "runs" / run_id / "status.json").exists()


def _active_task(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workers": {"local_worktree": {"type": "local_worktree"}},
                "reviewers": {
                    "manual": {"type": "manual", "role": "test_reviewer", "required": True}
                },
                "orchestration": {
                    "backend": "native_async",
                    "max_parallel": 2,
                    "max_retries": 1,
                },
                "workflows": {"docs_only": {"gates": []}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task = Task(
        id="agos-01",
        title="Execution runtime integration task",
        workflow="docs_only",
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