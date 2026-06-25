from __future__ import annotations

import json
from types import SimpleNamespace

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.execution_worker import WorkerHealth, WorkerHealthCheck
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


runner = CliRunner()


def _stub_cli_entrypoint_success(monkeypatch):
    import agos.cli.cmd_doctor as cmd_doctor

    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda _name: r"C:\\Tools\\agos.cmd")

    def fake_run_command(args, **_kwargs):
        if args[:3] == ["git", "rev-parse", "--git-path"]:
            return type("Proc", (), {"returncode": 0, "stdout": ".git/hooks\n", "stderr": ""})()
        return type("Proc", (), {"returncode": 0, "stdout": "agos 0.1.0", "stderr": ""})()

    monkeypatch.setattr(cmd_doctor, "run_command", fake_run_command)


def test_doctor_cli_entrypoint_check_passes_when_console_script_runs(monkeypatch):
    import agos.cli.cmd_doctor as cmd_doctor

    _stub_cli_entrypoint_success(monkeypatch)

    check = cmd_doctor._cli_entrypoint_check()

    assert check.state == "passed"
    assert "console script" in check.detail


def test_doctor_cli_entrypoint_check_fails_when_console_script_path_is_missing(monkeypatch):
    import agos.cli.cmd_doctor as cmd_doctor

    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda _name: None)

    check = cmd_doctor._cli_entrypoint_check()

    assert check.state == "failed"
    assert "not found" in check.detail


def test_doctor_cli_entrypoint_check_fails_when_console_script_cannot_run(monkeypatch):
    import agos.cli.cmd_doctor as cmd_doctor

    monkeypatch.setattr(cmd_doctor.shutil, "which", lambda _name: r"C:\\Tools\\agos.cmd")
    monkeypatch.setattr(
        cmd_doctor,
        "run_command",
        lambda *_args, **_kwargs: type("Proc", (), {"returncode": 1, "stdout": "", "stderr": "permission denied"})(),
    )

    check = cmd_doctor._cli_entrypoint_check()

    assert check.state == "failed"
    assert "permission denied" in check.detail


def test_doctor_json_reports_healthy_initialized_repo(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    _stub_cli_entrypoint_success(monkeypatch)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["healthy"] is True
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["git_repo"]["state"] == "passed"
    assert checks["agos_initialized"]["state"] == "passed"
    assert checks["config"]["state"] == "passed"
    assert checks["workers"]["state"] == "passed"
    assert checks["reviewers"]["state"] == "passed"
    assert checks["orchestration"]["state"] == "passed"
    assert checks["python_version"]["state"] == "passed"
    assert checks["cli_entrypoint"]["state"] == "passed"
    assert checks["git_hooks"]["state"] == "warning"


def test_doctor_warns_on_dev_only_reviewer(monkeypatch, tmp_repo):
    agos_dir = tmp_repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "agos.yaml").write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workers": {"local_worktree": {"type": "local_worktree"}},
                "reviewers": {"clean": {"type": "fake", "role": "reviewer"}},
                "allow_fake_reviewer": True,
                "orchestration": {"backend": "native_async", "max_parallel": 2},
                "workflows": {"feature": {"gates": []}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_repo)
    _stub_cli_entrypoint_success(monkeypatch)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["healthy"] is True
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["reviewers"]["state"] == "warning"
    assert "dev-only reviewer" in checks["reviewers"]["detail"]


def test_doctor_human_reports_check_lines(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    _stub_cli_entrypoint_success(monkeypatch)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[passed] git_repo" in result.stdout
    assert "[passed] config" in result.stdout
    assert "[warning] git_hooks" in result.stdout


def test_doctor_json_fails_for_invalid_config(monkeypatch, tmp_repo):
    agos_dir = tmp_repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "agos.yaml").write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workflows": {
                    "feature": {
                        "gates": [
                            {
                                "id": "bad_gate",
                                "stage": ["pre-commit"],
                                "command": "pytest -q",
                                "argv": ["pytest", "-q"],
                            }
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_repo)
    _stub_cli_entrypoint_success(monkeypatch)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["healthy"] is False
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["config"]["state"] == "failed"
    assert "invalid AGOS configuration" in checks["config"]["detail"]


def test_doctor_reports_missing_hooks_as_warning(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    _stub_cli_entrypoint_success(monkeypatch)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["git_hooks"]["state"] == "warning"
    assert "not installed" in checks["git_hooks"]["detail"]


def test_doctor_warns_when_active_task_has_no_trust_anchor(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-task-01",
        title="Doctor trust anchor task",
        workflow="feature",
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    record = Ledger(paths.ledger).append({"type": "task_started", "task_id": task.id})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-01"),
            ledger_head_hash=record["hash"],
        ),
        paths,
    )
    monkeypatch.chdir(tmp_repo)
    _stub_cli_entrypoint_success(monkeypatch)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["trust_anchor"]["state"] == "warning"
    assert "agos anchor publish" in checks["trust_anchor"]["detail"]


def test_doctor_reports_uninitialized_repo(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    _stub_cli_entrypoint_success(monkeypatch)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["agos_initialized"]["state"] == "failed"
    assert checks["git_hooks"]["state"] == "skipped"


def test_doctor_reports_non_git_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["git_repo"]["state"] == "failed"


def test_doctor_reports_installed_and_unmanaged_hooks(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    hooks_dir = tmp_repo / ".git" / "hooks"
    (hooks_dir / "pre-commit").write_text("# Managed by AGOS\n", encoding="utf-8")
    (hooks_dir / "pre-push").write_text("# Managed by AGOS\n", encoding="utf-8")
    monkeypatch.chdir(tmp_repo)
    _stub_cli_entrypoint_success(monkeypatch)

    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["git_hooks"]["state"] == "passed"

    (hooks_dir / "pre-push").write_text("#!/bin/sh\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["git_hooks"]["state"] == "warning"
    assert "not managed" in checks["git_hooks"]["detail"]


def test_doctor_worker_health_check_warning_and_failure():
    from agos.cli.cmd_doctor import _worker_health_check

    class WarningAdapter:
        def health(self):
            return WorkerHealth(
                name="warn",
                adapter="fake",
                checks=[WorkerHealthCheck(name="artifact_contract", state="warning", detail="no globs")],
            )

    class FailedAdapter:
        def health(self):
            return WorkerHealth(
                name="fail",
                adapter="fake",
                checks=[WorkerHealthCheck(name="command_available", state="failed", detail="missing")],
            )

    class RaisingAdapter:
        def health(self):
            raise RuntimeError("boom")

    assert _worker_health_check(SimpleNamespace(worker_adapters=lambda: {})).state == "warning"
    warning = _worker_health_check(SimpleNamespace(worker_adapters=lambda: {"warn": WarningAdapter()}))
    assert warning.state == "warning"
    assert "no globs" in warning.detail
    failed = _worker_health_check(
        SimpleNamespace(worker_adapters=lambda: {"fail": FailedAdapter(), "raise": RaisingAdapter()})
    )
    assert failed.state == "failed"
    assert "missing" in failed.detail
    assert "boom" in failed.detail


def _write_config(tmp_repo) -> None:
    agos_dir = tmp_repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "agos.yaml").write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workers": {"local_worktree": {"type": "local_worktree"}},
                "reviewers": {"manual": {"type": "manual", "role": "security_reviewer"}},
                "orchestration": {"backend": "native_async", "max_parallel": 2},
                "workflows": {"feature": {"gates": []}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
