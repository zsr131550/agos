"""Tests for status.json (derived cache of the ledger)."""
from __future__ import annotations

from pathlib import Path
import os

from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import GateState, Status, derive_status, load_status, save_status


def test_save_and_load_status(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    status = Status(
        task_id="T1",
        phase="executing",
        gates={"tests_pass": GateState()},
        ledger_head_hash="abc",
        last_event_seq=None,
    )
    save_status(status, paths)
    loaded = load_status(paths)
    assert loaded == status


def test_load_status_none_when_absent(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    assert load_status(paths) is None


def test_derive_status_uses_ledger_head(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": "T1"})
    ledger.append({"type": "checkpoint"})
    status = derive_status(
        paths,
        task_id="T1",
        gates=["tests_pass"],
        ledger=ledger,
        executor_run=None,
        last_event_seq=5,
        gate_states=None,
    )
    assert status.task_id == "T1"
    assert status.ledger_head_hash == ledger.head_hash()
    assert status.last_event_seq == 5
    assert status.phase == "executing"
    assert set(status.gates) == {"tests_pass"}
    assert status.gates["tests_pass"].state == "unknown"


def test_derive_status_carries_gate_states(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": "T1"})
    states = {
        "tests_pass": GateState(
            state="pass",
            last_evaluated="2026-06-21T00:00:00Z",
        )
    }
    status = derive_status(
        paths,
        task_id="T1",
        gates=["tests_pass"],
        ledger=ledger,
        executor_run=None,
        last_event_seq=None,
        gate_states=states,
    )
    assert status.gates["tests_pass"].state == "pass"


def test_derive_status_carries_executor_run(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": "T1"})

    status = derive_status(
        paths,
        task_id="T1",
        gates=[],
        ledger=ledger,
        executor_run=ExecutorRun(adapter="multica", run_id="run-1", issue_id="AGO-1"),
        last_event_seq=1,
        gate_states=None,
    )

    assert status.executor_run is not None
    assert status.executor_run.adapter == "multica"
    assert status.executor_run.run_id == "run-1"
    assert status.executor_run.issue_id == "AGO-1"


def test_save_status_removes_temp_file_when_replace_fails(monkeypatch, tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    status = Status(
        task_id="T1",
        phase="executing",
        gates={},
        ledger_head_hash="abc",
        last_event_seq=None,
    )
    temp_names: list[str] = []
    unlinked: list[str] = []

    original_mkstemp = __import__("tempfile").mkstemp
    original_unlink = os.unlink

    def recording_mkstemp(*args, **kwargs):
        fd, name = original_mkstemp(*args, **kwargs)
        temp_names.append(name)
        return fd, name

    def failing_replace(src, dst):
        del src, dst
        raise OSError("replace failed")

    def recording_unlink(path):
        unlinked.append(path)
        return original_unlink(path)

    monkeypatch.setattr("agos.core.status.tempfile.mkstemp", recording_mkstemp)
    monkeypatch.setattr("agos.core.status.os.replace", failing_replace)
    monkeypatch.setattr("agos.core.status.os.unlink", recording_unlink)

    try:
        save_status(status, paths)
    except OSError as exc:
        assert str(exc) == "replace failed"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected replace failure")

    assert unlinked == temp_names
    assert not Path(temp_names[0]).exists()


def test_save_status_ignores_unlink_failure_after_replace_failure(monkeypatch, tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    status = Status(
        task_id="T1",
        phase="executing",
        gates={},
        ledger_head_hash="abc",
        last_event_seq=None,
    )

    monkeypatch.setattr(
        "agos.core.status.os.replace",
        lambda src, dst: (_ for _ in ()).throw(OSError("replace failed")),
    )
    monkeypatch.setattr(
        "agos.core.status.os.unlink",
        lambda path: (_ for _ in ()).throw(OSError("unlink failed")),
    )

    try:
        save_status(status, paths)
    except OSError as exc:
        assert str(exc) == "replace failed"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected replace failure")
