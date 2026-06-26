from __future__ import annotations

import subprocess

import pytest


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


def test_resolve_executable_expands_bare_command_on_windows(monkeypatch):
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(
        command_module.shutil,
        "which",
        lambda name: r"C:\npm\claude.CMD" if name == "claude" else None,
    )

    assert command_module._resolve_executable(["claude", "-p", "hi"]) == [
        r"C:\npm\claude.CMD",
        "-p",
        "hi",
    ]


def test_resolve_executable_returns_none_when_unresolvable(monkeypatch):
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(command_module.shutil, "which", lambda name: None)

    assert command_module._resolve_executable(["multica", "daemon"]) is None


def test_resolve_executable_returns_none_on_posix(monkeypatch):
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "linux")
    # which must not be consulted on POSIX; fail loudly if it is.
    monkeypatch.setattr(
        command_module.shutil,
        "which",
        lambda name: pytest.fail("shutil.which must not be called on POSIX"),
    )

    assert command_module._resolve_executable(["claude", "-p"]) is None


def test_resolve_executable_returns_none_for_string_and_full_path(monkeypatch):
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    full = r"C:\npm\claude.CMD"
    monkeypatch.setattr(command_module.shutil, "which", lambda name: full)

    # String commands are passed through unchanged by the caller (tokenizing
    # would risk mis-quoting), so _resolve_executable declines to intervene.
    assert command_module._resolve_executable("claude -p") is None
    # An already-resolved full path needs no further resolution.
    assert command_module._resolve_executable([full, "-p"]) is None


def test_run_command_retries_with_resolved_executable_on_windows(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(
        command_module.shutil,
        "which",
        lambda name: r"C:\npm\claude.CMD" if name == "claude" else None,
    )

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise FileNotFoundError(2, "shim not resolvable by CreateProcess")
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)

    result = run_command(["claude", "-p", "hi"], capture_output=True)

    assert calls[0] == ["claude", "-p", "hi"]
    assert calls[1] == [r"C:\npm\claude.CMD", "-p", "hi"]
    assert result.returncode == 0


def test_run_command_reraises_when_executable_unresolvable(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(command_module.shutil, "which", lambda name: None)

    def fake_run(args, **kwargs):
        raise FileNotFoundError(2, "not found")

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)

    with pytest.raises(FileNotFoundError):
        run_command(["nonexistent-binary"], capture_output=True)


def test_run_command_does_not_resolve_when_subprocess_succeeds(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    # which must not be consulted when the first subprocess.run succeeds;
    # this locks in that git/.exe calls never touch shutil.which (the doctor
    # test stubs which globally and would otherwise regress).
    monkeypatch.setattr(
        command_module.shutil,
        "which",
        lambda name: pytest.fail(
            "shutil.which must not be called when subprocess succeeds"
        ),
    )

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)

    result = run_command(["git", "status"], capture_output=True)

    assert result.returncode == 0
