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


def test_resolve_executable_prefers_cmd_over_powershell_shim_on_windows(monkeypatch):
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")

    def fake_which(name):
        return {
            "codex": r"C:\Users\me\AppData\Roaming\npm\codex.ps1",
            "codex.cmd": r"C:\Users\me\AppData\Roaming\npm\codex.cmd",
        }.get(name)

    monkeypatch.setattr(command_module.shutil, "which", fake_which)

    assert command_module._resolve_executable(["codex", "exec", "--json", "{}"]) == [
        r"C:\Users\me\AppData\Roaming\npm\codex.cmd",
        "exec",
        "--json",
        "{}",
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
    # Resolve to a .exe (not a .CMD shim) so the retry stays on the
    # subprocess.run path; the .CMD-shim retry path is covered separately.
    monkeypatch.setattr(
        command_module.shutil,
        "which",
        lambda name: r"C:\npm\multica.exe" if name == "multica" else None,
    )

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise FileNotFoundError(2, "not resolvable by CreateProcess")
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        command_module.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("Popen must not be used for a non-shim retry"),
    )

    result = run_command(["multica", "daemon"], capture_output=True)

    assert calls[0] == ["multica", "daemon"]
    assert calls[1] == [r"C:\npm\multica.exe", "daemon"]
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


class _FakeProc:
    """Stand-in for a Popen process with a controllable communicate()."""

    def __init__(self, returncode=0, pid=4242, communicate=None):
        self.returncode = returncode
        self.pid = pid
        self._communicate = communicate

    def communicate(self, input=None, timeout=None):
        if self._communicate is not None:
            return self._communicate(input=input, timeout=timeout)
        return ("out", "err")


def _patch_popen(monkeypatch, proc, recorder):
    import agos.core.command as command_module

    def fake_popen(args, **kwargs):
        recorder["args"] = args
        recorder["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(command_module.subprocess, "Popen", fake_popen)


def test_run_command_uses_popen_tree_kill_for_cmd_shim_on_windows(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    recorder = {}
    _patch_popen(monkeypatch, _FakeProc(returncode=0), recorder)
    monkeypatch.setattr(
        command_module.subprocess,
        "run",
        lambda *a, **k: pytest.fail("subprocess.run must not spawn a .CMD shim"),
    )

    result = run_command(
        [r"C:\npm\claude.CMD", "-p", "hi"], capture_output=True, encoding="utf-8"
    )

    assert recorder["args"] == [r"C:\npm\claude.CMD", "-p", "hi"]
    assert result.returncode == 0


def test_run_command_cmd_shim_retry_uses_popen(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(
        command_module.shutil,
        "which",
        lambda name: r"C:\npm\claude.CMD" if name == "claude" else None,
    )

    run_calls = []

    def fake_run(args, **kwargs):
        run_calls.append(args)
        raise FileNotFoundError(2, "bare .CMD shim not resolvable by CreateProcess")

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)
    recorder = {}
    _patch_popen(monkeypatch, _FakeProc(returncode=0), recorder)

    result = run_command(["claude", "-p", "hi"], capture_output=True)

    assert run_calls[0] == ["claude", "-p", "hi"]
    assert recorder["args"] == [r"C:\npm\claude.CMD", "-p", "hi"]
    assert result.returncode == 0


def test_run_command_kills_tree_on_timeout_for_cmd_shim(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")

    killed = {}
    monkeypatch.setattr(command_module, "_kill_tree", lambda pid: killed.setdefault("pid", pid))

    def communicate(*, input=None, timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired(cmd=["claude.CMD"], timeout=timeout)
        return ("partial", "err")

    _patch_popen(
        monkeypatch, _FakeProc(returncode=0, pid=99, communicate=communicate), {}
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_command([r"C:\npm\claude.CMD", "-p", "hi"], capture_output=True)

    assert killed["pid"] == 99


def test_run_command_cmd_shim_translates_capture_output(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    recorder = {}
    _patch_popen(monkeypatch, _FakeProc(returncode=0), recorder)

    run_command([r"C:\npm\claude.CMD", "-p"], capture_output=True)

    assert recorder["kwargs"]["stdout"] == subprocess.PIPE
    assert recorder["kwargs"]["stderr"] == subprocess.PIPE
    assert "capture_output" not in recorder["kwargs"]


def test_run_command_cmd_shim_check_raises_called_process_error(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    _patch_popen(monkeypatch, _FakeProc(returncode=2), {})

    with pytest.raises(subprocess.CalledProcessError):
        run_command([r"C:\npm\claude.CMD", "-p"], check=True, capture_output=True)


def test_kill_tree_invokes_taskkill_tree_force(monkeypatch):
    import agos.core.command as command_module

    recorded = {}

    def fake_run(args, **kwargs):
        recorded["args"] = args
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)

    command_module._kill_tree(4242)

    assert recorded["args"] == ["taskkill", "/T", "/F", "/PID", "4242"]


def test_run_command_does_not_use_popen_for_non_shim_on_windows(monkeypatch):
    from agos.core.command import run_command
    import agos.core.command as command_module

    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(
        command_module.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("Popen must not be used for a non-shim command"),
    )

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(command_module.subprocess, "run", fake_run)

    result = run_command(["git", "status"], capture_output=True)

    assert result.returncode == 0
