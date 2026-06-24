"""Tests for MulticaAdapter against a stub `multica` binary."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from agos.core.task import ExecutorBinding, Task, new_task_id


def make_stub(tmp_path: Path, monkeypatch) -> str:
    """Install fake_multica.py as a `multica` command on PATH."""
    src = Path(__file__).parent / "stubs" / "fake_multica.py"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    script = bin_dir / "fake_multica.py"
    script.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    if os.name == "nt":
        wrapper = bin_dir / "multica.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "%~dp0fake_multica.py" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = bin_dir / "multica"
        wrapper.write_text(
            f'#!/usr/bin/env sh\nexec "{sys.executable}" "$(dirname "$0")/fake_multica.py" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_MULTICA_STATE", str(tmp_path / "state"))
    return str(wrapper)


def make_task() -> Task:
    return Task(
        id=new_task_id(),
        title="Add rate limiting",
        intent="Protect /login from brute force attempts",
        acceptance=["5 tries -> lockout"],
        workflow="feature",
        gates=["tests_pass"],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )


def test_start_parses_issue_json_no_workdir_flag(tmp_path: Path, monkeypatch):
    from agos.adapters.multica import MulticaAdapter
    import agos.adapters.multica as multica_module

    stub = make_stub(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    real_run = multica_module.run_command

    def spy(args, **kwargs):
        calls.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr(multica_module, "run_command", spy)

    run = MulticaAdapter(multica_bin=stub).start(make_task())

    assert run.adapter == "multica"
    assert run.run_id == "fake-task-uuid"
    assert run.issue_id == "MUL-1"

    create_args = next(
        args for args in calls if Path(args[0]).stem == "multica" and args[1:3] == ["issue", "create"]
    )
    assert "--workdir" not in create_args
    assert "--repo" not in create_args
    assert "--assignee" in create_args
    assert "--allow-duplicate" in create_args


def test_stream_events_maps_messages_to_events(tmp_path: Path, monkeypatch):
    from agos.adapters.multica import MulticaAdapter

    stub = make_stub(tmp_path, monkeypatch)
    events = list(MulticaAdapter(multica_bin=stub).stream_events("fake-task-uuid", since=0))

    assert len(events) == 3
    assert events[0].kind == "text"
    assert events[1].kind == "tool_call"
    assert events[2].kind == "run_complete"
    assert events[0].seq == 1
    assert events[2].seq == 3


def test_stream_events_since_cursor(tmp_path: Path, monkeypatch):
    from agos.adapters.multica import MulticaAdapter

    stub = make_stub(tmp_path, monkeypatch)
    events = list(MulticaAdapter(multica_bin=stub).stream_events("fake-task-uuid", since=1))

    assert [event.seq for event in events] == [2, 3]


def test_status_maps_done_to_completed(tmp_path: Path, monkeypatch):
    from agos.adapters.multica import MulticaAdapter

    stub = make_stub(tmp_path, monkeypatch)
    status = MulticaAdapter(multica_bin=stub).status("fake-task-uuid", issue_id="MUL-1")

    assert status.state == "completed"


def test_status_maps_completed_to_completed(monkeypatch):
    from agos.adapters.multica import MulticaAdapter
    import agos.adapters.multica as multica_module

    class FakeProc:
        returncode = 0
        stdout = '{"runs":[{"id":"fake-task-uuid","status":"completed"}]}'
        stderr = ""

    def fake_run(args, **kwargs):
        del args, kwargs
        return FakeProc()

    monkeypatch.setattr(multica_module, "run_command", fake_run)

    status = MulticaAdapter(multica_bin="multica").status("fake-task-uuid", issue_id="MUL-1")

    assert status.state == "completed"
    assert status.detail == "completed"


def test_not_found_exit_code_maps_to_failed(tmp_path: Path, monkeypatch):
    from agos.adapters.multica import MulticaAdapter
    import agos.adapters.multica as multica_module

    stub = make_stub(tmp_path, monkeypatch)

    class FakeProc:
        returncode = 4
        stdout = ""
        stderr = "not found"

    def fake_run(args, **kwargs):
        return FakeProc()

    monkeypatch.setattr(multica_module, "run_command", fake_run)

    status = MulticaAdapter(multica_bin=stub).status("fake-task-uuid", issue_id="MUL-1")

    assert status.state == "failed"


def test_retryable_exit_retries_before_success(monkeypatch):
    from agos.adapters.multica import MulticaAdapter
    import agos.adapters.multica as multica_module

    calls: list[list[str]] = []

    class FakeProc:
        def __init__(self, *, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(args)
        if len(calls) == 1:
            return FakeProc(returncode=2, stderr="network")
        return FakeProc(returncode=0, stdout='{"runs":[{"id":"fake-task-uuid","status":"done"}]}')

    sleeps: list[int] = []
    monkeypatch.setattr(multica_module, "run_command", fake_run)
    monkeypatch.setattr(multica_module.time, "sleep", lambda seconds: sleeps.append(seconds))

    status = MulticaAdapter(multica_bin="multica").status("fake-task-uuid", issue_id="MUL-1")

    assert status.state == "completed"
    assert len(calls) == 2
    assert sleeps == [2]


def test_timeout_raises_runtime_error_after_retries(monkeypatch):
    from agos.adapters.multica import MulticaAdapter
    import agos.adapters.multica as multica_module

    def timeout(*_args, **_kwargs):
        raise multica_module.subprocess.TimeoutExpired(cmd="multica", timeout=30)

    monkeypatch.setattr(multica_module, "run_command", timeout)

    try:
        MulticaAdapter(multica_bin="multica").status("fake-task-uuid", issue_id="MUL-1")
    except RuntimeError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_status_prefers_issue_id_when_available(monkeypatch):
    from agos.adapters.multica import MulticaAdapter
    import agos.adapters.multica as multica_module

    calls: list[list[str]] = []

    class FakeProc:
        def __init__(self, *, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args, **kwargs):
        calls.append(args)
        return FakeProc(returncode=0, stdout='{"runs":[{"id":"fake-task-uuid","status":"done"}]}')

    monkeypatch.setattr(multica_module, "run_command", fake_run)

    status = MulticaAdapter(multica_bin="multica").status("fake-task-uuid", issue_id="MUL-1")

    assert status.state == "completed"
    assert calls[0][1:4] == ["issue", "runs", "MUL-1"]


def test_default_binary_is_resolved_before_subprocess(monkeypatch):
    from agos.adapters.multica import MulticaAdapter
    import agos.adapters.multica as multica_module

    calls: list[list[str]] = []

    class FakeProc:
        def __init__(self, *, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_which(name: str) -> str | None:
        if name == "multica":
            return r"C:\tools\multica.cmd"
        return None

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["issue", "create"]:
            return FakeProc(returncode=0, stdout='{"identifier":"MUL-1"}')
        if args[1:3] == ["issue", "runs"]:
            return FakeProc(returncode=0, stdout='{"runs":[{"id":"fake-task-uuid"}]}')
        raise AssertionError(args)

    monkeypatch.setattr(multica_module.shutil, "which", fake_which)
    monkeypatch.setattr(multica_module, "run_command", fake_run)

    run = MulticaAdapter().start(make_task())

    assert run.issue_id == "MUL-1"
    assert run.run_id == "fake-task-uuid"
    assert calls[0][0] == r"C:\tools\multica.cmd"


def test_start_accepts_list_payload_from_issue_runs(tmp_path: Path, monkeypatch):
    from agos.adapters.multica import MulticaAdapter
    import agos.adapters.multica as multica_module

    stub = make_stub(tmp_path, monkeypatch)

    class FakeProc:
        def __init__(self, *, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args, **kwargs):
        if args[1:3] == ["issue", "create"]:
            return FakeProc(returncode=0, stdout='{"identifier":"MUL-1"}')
        if args[1:3] == ["issue", "runs"]:
            return FakeProc(returncode=0, stdout='[{"id":"fake-task-uuid","status":"todo"}]')
        raise AssertionError(args)

    monkeypatch.setattr(multica_module, "run_command", fake_run)

    run = MulticaAdapter(multica_bin=stub).start(make_task())

    assert run.issue_id == "MUL-1"
    assert run.run_id == "fake-task-uuid"


def test_stream_events_accepts_list_payload(tmp_path: Path, monkeypatch):
    from agos.adapters.multica import MulticaAdapter
    import agos.adapters.multica as multica_module

    stub = make_stub(tmp_path, monkeypatch)

    class FakeProc:
        def __init__(self, *, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args, **kwargs):
        if args[1:3] == ["issue", "run-messages"]:
            return FakeProc(
                returncode=0,
                stdout='[{"seq":1,"ts":"2026-06-21T00:00:00Z","kind":"text","content":"hello"}]',
            )
        raise AssertionError(args)

    monkeypatch.setattr(multica_module, "run_command", fake_run)

    events = list(MulticaAdapter(multica_bin=stub).stream_events("fake-task-uuid"))

    assert len(events) == 1
    assert events[0].seq == 1
    assert events[0].content == "hello"
