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

    wrapper = bin_dir / "multica.cmd"
    wrapper.write_text(f'@echo off\r\n"{sys.executable}" "%~dp0fake_multica.py" %*\r\n', encoding="utf-8")

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
    real_run = multica_module.subprocess.run

    def spy(args, **kwargs):
        calls.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr(multica_module.subprocess, "run", spy)

    run = MulticaAdapter(multica_bin=stub).start(make_task())

    assert run.adapter == "multica"
    assert run.run_id == "fake-issue-uuid"
    assert run.issue_id == "MUL-1"

    create_args = next(
        args for args in calls if Path(args[0]).stem == "multica" and args[1:3] == ["issue", "create"]
    )
    assert "--workdir" not in create_args
    assert "--repo" not in create_args
    assert "--assignee" in create_args


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
    status = MulticaAdapter(multica_bin=stub).status("fake-task-uuid")

    assert status.state == "completed"


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

    monkeypatch.setattr(multica_module.subprocess, "run", fake_run)

    status = MulticaAdapter(multica_bin=stub).status("fake-task-uuid")

    assert status.state == "failed"
