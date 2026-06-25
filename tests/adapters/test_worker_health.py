from __future__ import annotations

import subprocess

import pytest

import agos.adapters.workers._health as health_module
from agos.core.execution_worker import ensure_worker_ready


class _FakeProc:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _raise_oserror(*_args, **_kwargs):
    raise OSError("binary not executable")


def _raise_timeout(*_args, **_kwargs):
    raise subprocess.TimeoutExpired(cmd=["cli"], timeout=5)


ADAPTERS = [
    pytest.param(
        "agos.adapters.workers.codex_cli",
        "CodexWorkerAdapter",
        id="codex",
    ),
    pytest.param(
        "agos.adapters.workers.claude_code",
        "ClaudeWorkerAdapter",
        id="claude",
    ),
]


def _make_adapter(module_path: str, class_name: str, *, command: str = "cli", health_probe: bool = False):
    module = __import__(module_path, fromlist=[class_name])
    cls = getattr(module, class_name)
    return cls(command=command, name=command, health_probe=health_probe)


def _check(health, name: str):
    return next(check for check in health.checks if check.name == name)


@pytest.mark.parametrize(("module_path", "class_name"), ADAPTERS)
def test_health_command_missing_fails(module_path, class_name, monkeypatch):
    monkeypatch.setattr(health_module.shutil, "which", lambda _command: None)
    monkeypatch.setattr(health_module, "run_command", _raise_oserror)

    health = _make_adapter(module_path, class_name).health()

    assert _check(health, "command_available").state == "failed"
    assert health.state == "unhealthy"


@pytest.mark.parametrize(("module_path", "class_name"), ADAPTERS)
def test_health_version_ok(module_path, class_name, monkeypatch):
    monkeypatch.setattr(health_module.shutil, "which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(health_module, "run_command", lambda *_a, **_k: _FakeProc(stdout="cli 1.0"))

    health = _make_adapter(module_path, class_name).health()

    assert _check(health, "version_responds").state == "passed"
    assert health.state == "healthy"


@pytest.mark.parametrize(("module_path", "class_name"), ADAPTERS)
def test_health_version_timeout_warns(module_path, class_name, monkeypatch):
    monkeypatch.setattr(health_module.shutil, "which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(health_module, "run_command", _raise_timeout)

    health = _make_adapter(module_path, class_name).health()

    assert _check(health, "version_responds").state == "warning"
    assert health.state == "healthy"


@pytest.mark.parametrize(("module_path", "class_name"), ADAPTERS)
def test_health_probe_disabled_by_default(module_path, class_name, monkeypatch):
    monkeypatch.setattr(health_module.shutil, "which", lambda command: f"/bin/{command}")
    monkeypatch.setattr(health_module, "run_command", lambda *_a, **_k: _FakeProc(stdout="cli 1.0"))

    health = _make_adapter(module_path, class_name).health()

    assert [check.name for check in health.checks] == ["command_available", "version_responds"]


@pytest.mark.parametrize(("module_path", "class_name"), ADAPTERS)
def test_health_probe_failure_warns_not_fails(module_path, class_name, monkeypatch):
    monkeypatch.setattr(health_module.shutil, "which", lambda command: f"/bin/{command}")

    def fake_run(args, **_kwargs):
        if "--version" in args:
            return _FakeProc(stdout="cli 1.0")
        return _FakeProc(returncode=1, stderr="quota exceeded")

    monkeypatch.setattr(health_module, "run_command", fake_run)

    adapter = _make_adapter(module_path, class_name, health_probe=True)
    health = adapter.health()

    assert _check(health, "cli_executes").state == "warning"
    assert health.state == "healthy"
    ensure_worker_ready(adapter)  # probe warning must not block readiness


def test_registry_passes_health_probe_to_cli_workers(tmp_repo):
    import yaml

    from agos.cli.worker_registry import register_configured_worker_adapters
    from agos.core.execution_service import ExecutionService
    from agos.core.repo import repo_paths

    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workers": {
                    "codex-probe": {
                        "type": "codex_cli",
                        "command": "codex",
                        "health_probe": True,
                    },
                    "claude-probe": {
                        "type": "claude_code",
                        "command": "claude",
                        "health_probe": True,
                    },
                },
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    service = ExecutionService(paths)

    register_configured_worker_adapters(service)

    assert service._worker_adapters["codex-probe"].health_probe is True
    assert service._worker_adapters["claude-probe"].health_probe is True
