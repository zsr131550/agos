from __future__ import annotations

import subprocess


def test_run_command_applies_default_timeout(monkeypatch):
    from agos.core.command import DEFAULT_COMMAND_TIMEOUT_SECONDS, run_command
    import agos.core.command as command_module

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)

    run_command(["git", "status"], capture_output=True)

    assert captured["kwargs"]["timeout"] == DEFAULT_COMMAND_TIMEOUT_SECONDS
    assert captured["kwargs"]["capture_output"] is True


def test_run_command_preserves_explicit_timeout(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    captured = {}

    def fake_run(args, **kwargs):
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)

    run_command(["multica", "daemon", "status"], timeout=3)

    assert captured["kwargs"]["timeout"] == 3
