from __future__ import annotations

import json
from types import SimpleNamespace

from rich.text import Text
import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import RunStatus
from agos.core.task_execution import TaskExecutionResult

runner = CliRunner()


class _FakeTaskExecutionService:
    def __init__(self, result: TaskExecutionResult) -> None:
        self.result = result
        self.requests = []

    def start(self, request):
        self.requests.append(request)
        return self.result


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


def _candidate_result(**updates) -> TaskExecutionResult:
    values = {
        "task_id": "agos-candidate-1",
        "mode": "candidate",
        "run_id": "candidate-run-1",
        "state": "completed",
        "candidate_ids": ["candidate-1"],
        "applied_candidate_ids": ["candidate-1"],
    }
    values.update(updates)
    return TaskExecutionResult.model_validate(values)


def test_start_help_exposes_mode_and_json() -> None:
    result = runner.invoke(app, ["start", "--help"])
    help_text = Text.from_ansi(result.stdout).plain

    assert result.exit_code == 0
    assert "--mode" in help_text
    assert "--json" in help_text


def test_start_candidate_json_is_normalized(monkeypatch, tmp_repo) -> None:
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    service = _FakeTaskExecutionService(_candidate_result())
    monkeypatch.setattr(
        "agos.cli.cmd_start.build_task_execution_service",
        lambda _root: service,
        raising=False,
    )

    result = runner.invoke(
        app,
        ["start", "--title", "Candidate", "--mode", "candidate", "--json"],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "candidate"
    assert payload["candidate_ids"] == payload["applied_candidate_ids"]
    assert service.requests[0].mode == "candidate"


def test_start_candidate_human_output_uses_run_id(monkeypatch, tmp_repo) -> None:
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    service = _FakeTaskExecutionService(_candidate_result())
    monkeypatch.setattr(
        "agos.cli.cmd_start.build_task_execution_service",
        lambda _root: service,
        raising=False,
    )

    result = runner.invoke(app, ["start", "--title", "Candidate", "--mode", "candidate"])

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "candidate-run-1"


def test_start_emits_compatibility_warnings_only_to_stderr(monkeypatch, tmp_repo) -> None:
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    service = _FakeTaskExecutionService(
        TaskExecutionResult(
            task_id="agos-legacy-1",
            mode="legacy",
            run_id="legacy-run-1",
            issue_id="AGO-1",
            state="running",
            compatibility_warnings=["configuration uses legacy defaults"],
        )
    )
    monkeypatch.setattr(
        "agos.cli.cmd_start.build_task_execution_service",
        lambda _root: service,
        raising=False,
    )

    result = runner.invoke(app, ["start", "--title", "Compatible"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "AGO-1"
    assert "Warning: configuration uses legacy defaults" in result.stderr


def test_start_writes_task_status_and_dispatches(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.executor_registry.MulticaAdapter.start",
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

    run_meta = json.loads(
        (tmp_repo / ".agos" / "tasks" / "current" / "evidence" / "runs" / "task-123.json").read_text(
            encoding="utf-8"
        )
    )
    assert run_meta["task_id"] == task_data["id"]
    assert run_meta["adapter"] == "multica"
    assert run_meta["issue_id"] == "AGO-77"

    ledger_path = tmp_repo / ".agos" / "tasks" / "current" / "ledger.jsonl"
    records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records] == [
        "task_started",
        "gates_locked",
        "task_execution_started",
        "executor_dispatched",
    ]
    assert [gate["id"] for gate in records[1]["gates"]] == ["tests_pass", "build_clean"]


def test_start_aborts_if_task_already_active(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    current_dir = tmp_repo / ".agos" / "tasks" / "current"
    (current_dir / "task.yaml").write_text("id: agos-existing\n", encoding="utf-8")

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.executor_registry.MulticaAdapter.start",
        lambda self, task: (_ for _ in ()).throw(AssertionError("dispatch should not run")),
    )

    result = runner.invoke(app, ["start", "--title", "Blocked"])

    assert result.exit_code == 1
    assert "Active task already exists" in result.stderr


def test_start_stages_task_until_dispatch_succeeds(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    current_dir = tmp_repo / ".agos" / "tasks" / "current"
    staging_root = tmp_repo / ".agos" / "tasks" / "staging"
    monkeypatch.chdir(tmp_repo)

    def fake_start(_self, task):
        assert not current_dir.exists() or not any(current_dir.iterdir())
        staged_task_yaml = staging_root / task.id / "task.yaml"
        assert staged_task_yaml.is_file()
        return SimpleNamespace(adapter="multica", run_id="task-staged", issue_id="AGO-100")

    monkeypatch.setattr("agos.cli.executor_registry.MulticaAdapter.start", fake_start)

    result = runner.invoke(app, ["start", "--title", "Staged"])

    assert result.exit_code == 0
    assert (current_dir / "task.yaml").is_file()
    assert not staging_root.exists() or not any(staging_root.iterdir())


def test_start_marks_terminal_executor_failure_blocked(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    config_path = tmp_repo / ".agos" / "agos.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["executor"] = {"name": "codex_cli", "agent": "codex", "command": "codex"}
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.adapters.local_cli_executor.CodexCliExecutorAdapter.start",
        lambda self, task: SimpleNamespace(adapter="codex_cli", run_id="task-empty-output", issue_id=None),
    )
    monkeypatch.setattr(
        "agos.adapters.local_cli_executor.CodexCliExecutorAdapter.status",
        lambda self, run_id, issue_id=None: RunStatus(
            state="failed",
            detail="Executor completed without writing files to outputs/agos-01",
        ),
    )

    result = runner.invoke(app, ["start", "--title", "Empty output"])

    assert result.exit_code == 0
    status = json.loads((tmp_repo / ".agos" / "tasks" / "current" / "status.json").read_text(encoding="utf-8"))
    assert status["phase"] == "blocked"

    records = [
        json.loads(line)
        for line in (tmp_repo / ".agos" / "tasks" / "current" / "ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert records[-2]["type"] == "executor_blocked"
    assert records[-2]["state"] == "failed"
    assert "without writing files" in records[-2]["detail"]
    assert records[-1]["type"] == "task_execution_blocked"
    assert records[-1]["blocked_stage"] == "executor"


def test_start_cleans_up_current_task_when_dispatch_fails(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    current_dir = tmp_repo / ".agos" / "tasks" / "current"
    monkeypatch.chdir(tmp_repo)

    def fail_start(_self, _task):
        raise RuntimeError("multica unavailable")

    monkeypatch.setattr("agos.cli.executor_registry.MulticaAdapter.start", fail_start)

    result = runner.invoke(app, ["start", "--title", "Dispatch fails"])

    assert result.exit_code == 1
    assert "multica unavailable" in result.stderr
    assert not current_dir.exists() or not any(current_dir.iterdir())


def test_start_uses_gate_override_when_provided(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.executor_registry.MulticaAdapter.start",
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
