from __future__ import annotations

import json
from types import SimpleNamespace

import yaml
from typer.testing import CliRunner

from agos.cli.main import app

runner = CliRunner()


def _write_config(tmp_repo) -> None:
    agos_dir = tmp_repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "tasks" / "current").mkdir(parents=True)
    config = {
        "executor": {"name": "multica", "agent": "Lambda"},
        "default_workflow": "feature",
        "workflows": {
            "feature": {
                "gates": [
                    {
                        "id": "tests_pass",
                        "stage": ["pre-commit", "pre-push"],
                        "command": "pytest -q",
                    },
                    {
                        "id": "build_clean",
                        "stage": ["pre-push"],
                        "command": "python -m compileall src",
                    },
                ]
            }
        },
    }
    (agos_dir / "agos.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_start_writes_task_status_and_dispatches(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.cmd_start.MulticaAdapter.start",
        lambda self, task: SimpleNamespace(adapter="multica", run_id="task-123", issue_id="AGO-77"),
    )

    result = runner.invoke(app, ["start", "--title", "Implement Task 9", "--intent", "Ship init/start"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "AGO-77"

    task_data = yaml.safe_load(
        (tmp_repo / ".agos" / "tasks" / "current" / "task.yaml").read_text(encoding="utf-8")
    )
    assert task_data["title"] == "Implement Task 9"
    assert task_data["intent"] == "Ship init/start"
    assert task_data["executor"]["agent"] == "Lambda"
    assert task_data["gates"] == ["tests_pass", "build_clean"]

    status = json.loads((tmp_repo / ".agos" / "tasks" / "current" / "status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "executing"
    assert status["executor_run"]["run_id"] == "task-123"
    assert status["executor_run"]["issue_id"] == "AGO-77"

    ledger_path = tmp_repo / ".agos" / "tasks" / "current" / "ledger.jsonl"
    records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == [
        "task_started",
        "gates_locked",
        "executor_dispatched",
    ]
    assert [gate["id"] for gate in records[1]["gates"]] == ["tests_pass", "build_clean"]


def test_start_aborts_if_task_already_active(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    current_dir = tmp_repo / ".agos" / "tasks" / "current"
    (current_dir / "task.yaml").write_text("id: agos-existing\n", encoding="utf-8")

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.cmd_start.MulticaAdapter.start",
        lambda self, task: (_ for _ in ()).throw(AssertionError("dispatch should not run")),
    )

    result = runner.invoke(app, ["start", "--title", "Blocked"])

    assert result.exit_code == 1
    assert "Active task already exists" in result.stderr


def test_start_uses_gate_override_when_provided(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.cmd_start.MulticaAdapter.start",
        lambda self, task: SimpleNamespace(adapter="multica", run_id="task-456", issue_id="AGO-88"),
    )

    result = runner.invoke(app, ["start", "--title", "Override gates", "--gate", "build_clean"])

    assert result.exit_code == 0

    task_data = yaml.safe_load(
        (tmp_repo / ".agos" / "tasks" / "current" / "task.yaml").read_text(encoding="utf-8")
    )
    assert task_data["gates"] == ["build_clean"]

    ledger_path = tmp_repo / ".agos" / "tasks" / "current" / "ledger.jsonl"
    records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    assert [gate["id"] for gate in records[1]["gates"]] == ["build_clean"]
