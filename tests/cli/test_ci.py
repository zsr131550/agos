from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.config import AGOSConfig, default_config
from agos.core.config import resolve_gates
from agos.core.gate import gates_locked_payload
from agos.core.ledger import append_task_record
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, load_status, save_status
from agos.core.task import ExecutorBinding, Task, new_task_id, save_task

runner = CliRunner()


def _write_active_task(tmp_repo, config_data: dict | None = None):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)

    config_data = config_data or default_config(agent="Lambda").model_dump(mode="python")
    paths.agos_yaml.write_text(
        yaml.safe_dump(config_data, sort_keys=False),
        encoding="utf-8",
    )

    config = AGOSConfig.load(paths.agos_yaml)
    workflow = config.default_workflow
    resolved_gates = resolve_gates(config, workflow)
    gate_ids = [gate.id for gate in resolved_gates]
    task = Task(
        id=f"agos-{new_task_id()}",
        title="CI task",
        intent="Run local gate checks",
        workflow=workflow,
        gates=gate_ids,
        executor=ExecutorBinding(adapter=config.executor.name, agent=config.executor.agent),
    )
    save_task(task, paths.task_yaml)

    append_task_record(paths.ledger, "task_started", task_id=task.id, title=task.title)
    append_task_record(
        paths.ledger,
        "gates_locked",
        task_id=task.id,
        gates=gates_locked_payload(resolved_gates),
    )
    dispatched = append_task_record(
        paths.ledger,
        "executor_dispatched",
        task_id=task.id,
        adapter=config.executor.name,
        run_id="run-123",
        issue_id="AGO-99",
    )
    status = TaskStatus.for_started_task(
        task=task,
        run=SimpleNamespace(adapter=config.executor.name, run_id="run-123", issue_id="AGO-99"),
        ledger_head_hash=dispatched["hash"],
    )
    save_status(status, paths)
    return paths, task


def _read_ledger(paths):
    return [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]


def test_ci_no_active_task_passes(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["ci", "--local", "--stage", "pre-commit"])

    assert result.exit_code == 0


def test_ci_blocks_on_failing_command_gate(monkeypatch, tmp_repo):
    config_data = {
        "executor": {"name": "multica", "agent": "Lambda"},
        "default_workflow": "feature",
        "workflows": {
            "feature": {
                "gates": [
                    {
                        "id": "tests_pass",
                        "stage": ["pre-commit"],
                        "command": 'python -c "raise SystemExit(1)"',
                    }
                ]
            }
        },
    }
    paths, _task = _write_active_task(tmp_repo, config_data=config_data)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_ci._git_diff_for_stage", lambda *_args, **_kwargs: "")

    result = runner.invoke(app, ["ci", "--local", "--stage", "pre-commit"])

    assert result.exit_code == 1
    assert "gate tests_pass" in result.stderr

    records = _read_ledger(paths)
    assert records[-1]["type"] == "gate_evaluated"
    assert records[-1]["gate"] == "tests_pass"
    assert records[-1]["state"] == "block"

    status = load_status(paths)
    assert status is not None
    assert status.gates["tests_pass"].state == "block"


def test_ci_reverifies_ledger_chain(monkeypatch, tmp_repo):
    paths, _task = _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    records = _read_ledger(paths)
    tampered = records[-1]
    tampered["issue_id"] = "AGO-CHANGED"
    records[-1] = tampered
    paths.ledger.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["ci", "--local", "--stage", "pre-commit"])

    assert result.exit_code == 1
    assert "ledger" in result.stderr.lower() or "hash" in result.stderr.lower()


def test_ci_blocks_on_repo_history_drift(monkeypatch, tmp_repo):
    paths, task = _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    append_task_record(
        paths.ledger,
        "checkpoint",
        task_id=task.id,
        run_id="run-123",
        evidence_refs=["messages/run-123.jsonl"],
        repo_head="0" * 40,
        last_seq=1,
    )
    monkeypatch.setattr("agos.cli.cmd_ci._git_diff_for_stage", lambda *_args, **_kwargs: "")

    result = runner.invoke(app, ["ci", "--local", "--stage", "pre-commit"])

    assert result.exit_code == 1
    assert "repo_history_drift" in result.stderr or "history drift" in result.stderr

    records = _read_ledger(paths)
    assert records[-1]["type"] == "repo_history_drift"


def test_ci_blocks_when_locked_gates_do_not_match_current_config(monkeypatch, tmp_repo):
    paths, _task = _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    config = AGOSConfig.load(paths.agos_yaml)
    config.workflows["feature"].gates = [config.workflows["feature"].gates[0]]
    config.save(paths.agos_yaml)

    result = runner.invoke(app, ["ci", "--local", "--stage", "pre-commit"])

    assert result.exit_code == 1
    assert "gates_locked" in result.stderr or "gate set" in result.stderr


def test_ci_passes_when_stage_gates_pass(monkeypatch, tmp_repo):
    config_data = {
        "executor": {"name": "multica", "agent": "Lambda"},
        "default_workflow": "feature",
        "workflows": {
            "feature": {
                "gates": [
                    {
                        "id": "tests_pass",
                        "stage": ["pre-commit"],
                        "command": 'python -c "raise SystemExit(0)"',
                    },
                    {
                        "id": "build_clean",
                        "stage": ["pre-push"],
                        "command": 'python -c "raise SystemExit(1)"',
                    },
                ]
            }
        },
    }
    paths, _task = _write_active_task(tmp_repo, config_data=config_data)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_ci._git_diff_for_stage", lambda *_args, **_kwargs: "")

    result = runner.invoke(app, ["ci", "--local", "--stage", "pre-commit"])

    assert result.exit_code == 0

    records = _read_ledger(paths)
    assert records[-1]["type"] == "gate_evaluated"
    assert records[-1]["gate"] == "tests_pass"
    assert records[-1]["state"] == "pass"

    status = load_status(paths)
    assert status is not None
    assert status.gates["tests_pass"].state == "pass"


def test_ci_pre_push_without_upstream_uses_head_diff(monkeypatch, tmp_repo):
    import agos.cli.cmd_ci as cmd_ci

    calls: list[list[str]] = []
    real_run = cmd_ci.run_command

    def fake_run(args, **kwargs):
        if args[:4] == ["git", "rev-parse", "--verify", "--quiet"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="missing upstream")
        if args[:3] == ["git", "diff", "HEAD"]:
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:3] == ["git", "diff", "@{u}..HEAD"]:
            raise AssertionError("should not diff against origin when origin is absent")
        return real_run(args, **kwargs)

    monkeypatch.setattr(cmd_ci, "run_command", fake_run)

    diff = cmd_ci._git_diff_for_stage(tmp_repo, "pre-push")

    assert diff == ""
    assert calls == [["git", "diff", "HEAD"]]


def test_ci_diff_stage_normalizes_missing_stdout(monkeypatch, tmp_repo):
    import agos.cli.cmd_ci as cmd_ci

    captured = {}

    def fake_run_command(*args, **kwargs):
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=None, stderr="")

    monkeypatch.setattr(
        cmd_ci,
        "run_command",
        fake_run_command,
    )

    assert cmd_ci._git_diff_for_stage(tmp_repo, "pre-commit") == ""
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_ci_reuses_single_ledger_read_for_policy_checks(monkeypatch, tmp_repo):
    config_data = {
        "executor": {"name": "multica", "agent": "Lambda"},
        "default_workflow": "feature",
        "workflows": {"feature": {"gates": []}},
    }
    _paths, _task = _write_active_task(tmp_repo, config_data=config_data)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_ci._git_diff_for_stage", lambda *_args, **_kwargs: "")

    import agos.cli.cmd_ci as cmd_ci

    calls = 0
    real_read_all = cmd_ci.Ledger.read_all

    def counted_read_all(self):
        nonlocal calls
        calls += 1
        return real_read_all(self)

    monkeypatch.setattr(cmd_ci.Ledger, "read_all", counted_read_all)

    result = runner.invoke(app, ["ci", "--local", "--stage", "pre-commit"])

    assert result.exit_code == 0
    assert calls == 1


def test_ci_pre_push_with_upstream_uses_upstream_diff(tmp_path, tmp_repo):
    import agos.cli.cmd_ci as cmd_ci

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=tmp_repo, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=tmp_repo, check=True)

    (tmp_repo / "feature.txt").write_text("ready\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.txt"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feature"], cwd=tmp_repo, check=True)

    diff = cmd_ci._git_diff_for_stage(tmp_repo, "pre-push")

    assert "feature.txt" in diff
    assert "+ready" in diff
