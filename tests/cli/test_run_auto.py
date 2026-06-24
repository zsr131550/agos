from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agos.adapters.workers import FakeWorkerAdapter
from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


runner = CliRunner()


def _readme_patch() -> bytes:
    return (
        b"diff --git a/README.md b/README.md\n"
        b"--- a/README.md\n"
        b"+++ b/README.md\n"
        b"@@ -1 +1 @@\n"
        b"-# t\n"
        b"+# changed\n"
    )


def _active_task(tmp_repo: Path, *, reviewers: bool = True):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "executor": {"name": "multica", "agent": "Lambda"},
        "workers": {"fake_worker": {"type": "fake"}},
        "orchestration": {"backend": "native_async", "max_parallel": 1},
        "workflows": {
            "feature": {
                "gates": [
                    {
                        "id": "readme_changed",
                        "stage": ["candidate"],
                        "argv": [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; assert Path('README.md').read_text().startswith('# changed')",
                        ],
                    }
                ]
            }
        },
    }
    if reviewers:
        config["reviewers"] = {"clean": {"type": "fake", "role": "reviewer"}}
    paths.agos_yaml.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    task = Task(
        id="agos-01",
        title="Automatic CLI task",
        intent="Update README.",
        workflow="feature",
        gates=["readme_changed"],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    locked = ledger.append(
        {
            "type": "gates_locked",
            "task_id": task.id,
            "gates": [
                {
                    "id": "readme_changed",
                    "stage": ["candidate"],
                    "argv": [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; assert Path('README.md').read_text().startswith('# changed')",
                    ],
                    "command": None,
                    "type": None,
                }
            ],
        }
    )
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
            ledger_head_hash=locked["hash"],
        ),
        paths,
    )
    return paths


def _register_fake_worker(service):
    adapter = FakeWorkerAdapter(patch_bytes=_readme_patch())
    adapter.name = "fake_worker"
    service.register_worker_adapter(adapter)


def test_run_auto_dry_run_json(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_execute_plan.register_configured_worker_adapters", _register_fake_worker)

    result = runner.invoke(app, ["run", "auto", "--dry-run", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["plan_id"] == "auto-plan-agos-01"
    assert payload["candidate_ids"]
    assert payload["accepted_candidate_ids"] == payload["candidate_ids"]
    assert payload["applied_candidate_ids"] == []
    assert (tmp_repo / "README.md").read_text(encoding="utf-8") == "# t\n"


def test_run_auto_apply_json(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_execute_plan.register_configured_worker_adapters", _register_fake_worker)

    result = runner.invoke(app, ["run", "auto", "--apply", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is False
    assert payload["applied_candidate_ids"] == payload["candidate_ids"]
    assert (tmp_repo / "README.md").read_text(encoding="utf-8") == "# changed\n"


def test_run_auto_missing_active_task(monkeypatch, tmp_repo):
    repo_paths(tmp_repo).agos_dir.mkdir(parents=True, exist_ok=True)
    (repo_paths(tmp_repo).agos_yaml).write_text(
        yaml.safe_dump({"executor": {"name": "multica", "agent": "Lambda"}, "workflows": {}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["run", "auto", "--dry-run", "--json"])

    assert result.exit_code == 1
    assert "No active AGOS task found" in result.stderr


def test_run_auto_human_output_reports_missing_review(monkeypatch, tmp_repo):
    _active_task(tmp_repo, reviewers=False)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_execute_plan.register_configured_worker_adapters", _register_fake_worker)

    result = runner.invoke(app, ["run", "auto", "--dry-run"])

    assert result.exit_code == 0, result.stderr
    assert "auto-plan-agos-01" in result.stdout
    assert "accepted: -" in result.stdout
    assert "review" in result.stdout
