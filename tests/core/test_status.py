"""Tests for status.json (derived cache of the ledger)."""
from __future__ import annotations

from pathlib import Path
import os

import pytest

from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger, LedgerTamperError
from agos.core.repo import repo_paths
from agos.core.status import GateState, Status, derive_status, load_status, replay_status, save_status
from agos.core.task import ExecutorBinding, Task, save_task


def _task_with_ledger(tmp_repo: Path, *, gates: list[str] | None = None):
    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-replay",
        title="Replay task",
        workflow="feature",
        gates=list(gates or []),
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    return paths, task, ledger, started


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


def test_load_status_preserves_invalid_cache_error_without_replay_inputs(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.status_json.parent.mkdir(parents=True, exist_ok=True)
    paths.status_json.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError):
        load_status(paths)


def test_load_status_preserves_invalid_cache_error_with_empty_ledger(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-empty-ledger",
        title="Empty ledger",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    paths.ledger.touch()
    paths.status_json.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ValueError):
        load_status(paths)


def test_load_status_returns_valid_cache_with_empty_ledger(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-empty-ledger-cache",
        title="Empty ledger cache",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    paths.ledger.touch()
    cached = Status(
        task_id=task.id,
        phase="executing",
        gates={},
        ledger_head_hash="",
    )
    save_status(cached, paths)

    assert load_status(paths) == cached


def test_load_status_repairs_cache_after_crash_between_ledger_and_cache(tmp_repo: Path):
    paths, task, ledger, started = _task_with_ledger(tmp_repo)
    save_status(
        Status(
            task_id=task.id,
            phase="executing",
            gates={},
            ledger_head_hash=started["hash"],
        ),
        paths,
    )
    final = ledger.append(
        {
            "type": "task_execution_completed",
            "task_id": task.id,
            "mode": "candidate",
            "run_id": "candidate-1",
            "state": "completed",
        }
    )

    recovered = load_status(paths)

    assert recovered is not None
    assert recovered.phase == "done"
    assert recovered.executor_run is not None
    assert recovered.executor_run.adapter == "candidate_pipeline"
    assert recovered.executor_run.run_id == "candidate-1"
    assert recovered.ledger_head_hash == final["hash"]
    assert load_status(paths) == recovered


def test_load_status_rebuilds_missing_cache_from_legacy_events(tmp_repo: Path):
    paths, task, ledger, _started = _task_with_ledger(tmp_repo, gates=["tests_pass"])
    ledger.append(
        {
            "type": "executor_dispatched",
            "task_id": task.id,
            "adapter": "multica",
            "run_id": "legacy-1",
            "issue_id": "AGO-1",
        }
    )
    ledger.append({"type": "checkpoint", "run_id": "legacy-1", "last_seq": 7})
    evaluated = ledger.append(
        {
            "type": "gate_evaluated",
            "gate": "tests_pass",
            "state": "pass",
            "stage": "pre-push",
        }
    )
    final = ledger.append(
        {"type": "executor_blocked", "run_id": "legacy-1", "state": "failed"}
    )

    recovered = load_status(paths)

    assert recovered is not None
    assert recovered.phase == "blocked"
    assert recovered.executor_run is not None
    assert recovered.executor_run.model_dump() == {
        "adapter": "multica",
        "run_id": "legacy-1",
        "issue_id": "AGO-1",
    }
    assert recovered.last_event_seq == 7
    assert recovered.gates["tests_pass"] == GateState(
        state="pass",
        last_evaluated=evaluated["ts"],
    )
    assert recovered.ledger_head_hash == final["hash"]
    assert paths.status_json.is_file()


def test_load_status_repairs_invalid_cache_from_verified_ledger(tmp_repo: Path):
    paths, task, ledger, _started = _task_with_ledger(tmp_repo)
    final = ledger.append(
        {"type": "dashboard_paused", "task_id": task.id, "phase": "blocked"}
    )
    paths.status_json.write_text("{not-json", encoding="utf-8")

    recovered = load_status(paths)

    assert recovered is not None
    assert recovered.phase == "blocked"
    assert recovered.ledger_head_hash == final["hash"]


def test_load_status_does_not_rewrite_current_compatible_cache(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths, task, _ledger, started = _task_with_ledger(tmp_repo)
    cached = Status(
        task_id=task.id,
        phase="gated",
        gates={},
        ledger_head_hash=started["hash"],
    )
    save_status(cached, paths)
    monkeypatch.setattr(
        "agos.core.status.save_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected rewrite")),
    )

    assert load_status(paths) == cached


def test_load_status_rejects_tampered_ledger_without_replacing_cache(tmp_repo: Path):
    paths, task, ledger, started = _task_with_ledger(tmp_repo)
    cached = Status(
        task_id=task.id,
        phase="executing",
        gates={},
        ledger_head_hash=started["hash"],
    )
    save_status(cached, paths)
    ledger.append({"type": "checkpoint", "last_seq": 1})
    original_cache = paths.status_json.read_text(encoding="utf-8")
    lines = paths.ledger.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1].replace('"last_seq": 1', '"last_seq": 2')
    paths.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerTamperError):
        load_status(paths)

    assert paths.status_json.read_text(encoding="utf-8") == original_cache


def test_replay_status_restores_dashboard_and_terminal_transitions():
    task = Task(
        id="agos-dashboard-replay",
        title="Dashboard replay",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )

    restored = replay_status(task, [{"type": "dashboard_restored", "hash": "restore"}])
    archived = replay_status(task, [{"type": "dashboard_archived", "hash": "archive"}])
    running = replay_status(
        task,
        [{"type": "task_execution_completed", "state": "running", "hash": "running"}],
        cached=Status(
            task_id=task.id,
            phase="blocked",
            gates={},
            ledger_head_hash="old",
        ),
    )
    failed = replay_status(
        task,
        [{"type": "task_execution_blocked", "state": "failed", "hash": "failed"}],
    )

    assert restored.executor_run is not None
    assert restored.executor_run.run_id == f"restored-{task.id}"
    assert archived.phase == "done"
    assert running.phase == "executing"
    assert failed.phase == "blocked"


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
