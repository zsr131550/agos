"""Interface tests for the authoritative TaskState module."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import threading

import pytest

from agos.core.ledger import Ledger, LedgerTamperError
from agos.core.repo import repo_paths
from agos.core.status import ExecutorRunInfo, GateState, Status, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.core.task_state import (
    TaskEvent,
    TaskRevision,
    TaskState,
    TaskStateBatchInterrupted,
    TaskStateCommitIndeterminate,
    TaskStateConflict,
    TaskStateIntegrityError,
    TaskStateValidationError,
    TaskStateWriteError,
)


def _save_task(tmp_repo: Path, *, gates: list[str] | None = None) -> tuple[object, Task]:
    paths = repo_paths(tmp_repo)
    task = Task(
        id="task-state-test",
        title="Task state test",
        workflow="feature",
        gates=list(gates or []),
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    return paths, task


def _started_task(
    tmp_repo: Path,
    *,
    gates: list[str] | None = None,
) -> tuple[object, Task, Ledger, dict[str, object]]:
    paths, task = _save_task(tmp_repo, gates=gates)
    ledger = Ledger(paths.ledger)
    started = ledger.append(
        {"type": "task_started", "task_id": task.id, "title": task.title}
    )
    return paths, task, ledger, started


def _record_facts(path_text: str, worker: int, count: int, start_event) -> None:
    """Record ordinary facts from one spawned process."""

    paths = repo_paths(Path(path_text))
    state = TaskState(paths)
    if not start_event.wait(timeout=10):
        raise RuntimeError("concurrent TaskState test start timed out")
    for index in range(count):
        state.record(
            TaskEvent(
                "checkpoint",
                {
                    "task_id": "task-state-test",
                    "worker": worker,
                    "index": index,
                    "last_seq": worker * count + index,
                },
            )
        )


def test_current_replays_full_ledger_and_repairs_invalid_cache(tmp_repo: Path):
    paths, task, ledger, _started = _started_task(tmp_repo, gates=["tests_pass"])
    ledger.append(
        {
            "type": "executor_dispatched",
            "task_id": task.id,
            "adapter": "multica",
            "run_id": "run-1",
        }
    )
    evaluated = ledger.append(
        {"type": "gate_evaluated", "gate": "tests_pass", "state": "pass"}
    )
    final = ledger.append({"type": "checkpoint", "last_seq": 12})
    paths.status_json.write_text("{not-json", encoding="utf-8")

    snapshot = TaskState(paths).current()

    assert snapshot is not None
    assert snapshot.task == task
    assert snapshot.status.executor_run is not None
    assert snapshot.status.executor_run.run_id == "run-1"
    assert snapshot.status.gates["tests_pass"] == GateState(
        state="pass",
        last_evaluated=evaluated["ts"],
    )
    assert snapshot.status.last_event_seq == 12
    assert snapshot.revision == TaskRevision(seq=4, head_hash=str(final["hash"]))
    assert Status.model_validate_json(paths.status_json.read_text(encoding="utf-8")) == snapshot.status


def test_current_preserves_baseline_eligible_cache_until_the_next_write(
    tmp_repo: Path,
):
    paths, task, _ledger, started = _started_task(tmp_repo)
    save_status(
        Status(
            task_id=task.id,
            phase="gated",
            gates={},
            ledger_head_hash=str(started["hash"]),
        ),
        paths,
    )

    snapshot = TaskState(paths).current()

    assert snapshot is not None
    assert snapshot.status.phase == "executing"
    assert Status.model_validate_json(paths.status_json.read_text(encoding="utf-8")).phase == "gated"
    assert any("baseline" in warning for warning in snapshot.warnings)


def test_current_then_different_instance_record_preserves_legacy_baseline(tmp_repo: Path):
    paths, task, ledger, started = _started_task(tmp_repo, gates=["tests_pass"])
    save_status(
        Status(
            task_id=task.id,
            phase="gated",
            gates={"tests_pass": GateState(state="pass")},
            ledger_head_hash=str(started["hash"]),
        ),
        paths,
    )

    current = TaskState(paths).current()
    assert current is not None
    TaskState(paths).record(TaskEvent("checkpoint", {"task_id": task.id, "last_seq": 8}))

    assert [record["type"] for record in ledger.read_all()] == [
        "task_started",
        "task_state_baselined",
        "checkpoint",
    ]
    paths.status_json.unlink()
    recovered = TaskState(paths).current()
    assert recovered is not None
    assert recovered.status.phase == "gated"
    assert recovered.status.gates["tests_pass"].state == "pass"


def test_pending_empty_baseline_rebases_after_a_concurrent_executor_dispatch(tmp_repo: Path):
    paths, task = _save_task(tmp_repo)
    save_status(
        Status(
            task_id=task.id,
            phase="gated",
            executor_run=ExecutorRunInfo(adapter="multica", run_id="legacy-run"),
            gates={},
            ledger_head_hash="",
            last_event_seq=4,
        ),
        paths,
    )
    pending_state = TaskState(paths)
    assert pending_state.current() is not None

    # A process still running the pre-TaskState writer protocol can append
    # between this instance's read and its first migrated write.
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    ledger.append(
        {
            "type": "executor_dispatched",
            "task_id": task.id,
            "adapter": "multica",
            "run_id": "new-run",
        }
    )

    commit = pending_state.record(
        TaskEvent("checkpoint", {"task_id": task.id, "run_id": "new-run", "last_seq": 9})
    )

    assert [record["type"] for record in Ledger(paths.ledger).read_all()] == [
        "task_started",
        "executor_dispatched",
        "task_state_baselined",
        "checkpoint",
    ]
    assert commit.snapshot.status.executor_run is not None
    assert commit.snapshot.status.executor_run.run_id == "new-run"
    assert commit.snapshot.status.phase == "executing"
    assert commit.snapshot.status.last_event_seq == 9


def test_current_rejects_tampering_without_replacing_cache(tmp_repo: Path):
    paths, task, ledger, started = _started_task(tmp_repo)
    cached = Status(
        task_id=task.id,
        phase="executing",
        gates={},
        ledger_head_hash=str(started["hash"]),
    )
    save_status(cached, paths)
    ledger.append({"type": "checkpoint", "last_seq": 1})
    original_cache = paths.status_json.read_text(encoding="utf-8")
    lines = paths.ledger.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1].replace('"last_seq": 1', '"last_seq": 2')
    paths.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(LedgerTamperError):
        TaskState(paths).current()

    assert paths.status_json.read_text(encoding="utf-8") == original_cache


def test_current_rejects_task_identity_mismatch(tmp_repo: Path):
    paths, _task = _save_task(tmp_repo)
    Ledger(paths.ledger).append({"type": "task_started", "task_id": "another-task"})

    with pytest.raises(TaskStateIntegrityError, match="task_id"):
        TaskState(paths).current()

    assert not paths.status_json.exists()


def test_record_requires_the_active_task_id_for_new_events(tmp_repo: Path):
    paths, task, ledger, _started = _started_task(tmp_repo)

    for event in (
        TaskEvent("checkpoint", {"last_seq": 1}),
        TaskEvent("checkpoint", {"task_id": "other-task", "last_seq": 1}),
    ):
        with pytest.raises(TaskStateValidationError, match="task_id"):
            TaskState(paths).record(event)

    assert [record["type"] for record in ledger.read_all()] == ["task_started"]
    assert task.id == "task-state-test"


def test_record_rejects_old_executor_run_events_after_redispatch(tmp_repo: Path):
    paths, task, ledger, _started = _started_task(tmp_repo)
    ledger.append(
        {
            "type": "executor_dispatched",
            "task_id": task.id,
            "adapter": "multica",
            "run_id": "run-old",
        }
    )
    redispatched = ledger.append(
        {
            "type": "executor_dispatched",
            "task_id": task.id,
            "adapter": "multica",
            "run_id": "run-current",
        }
    )
    revision = TaskRevision(seq=redispatched["seq"], head_hash=redispatched["hash"])

    for event, expected in (
        (
            TaskEvent(
                "checkpoint",
                {"task_id": task.id, "run_id": "run-old", "last_seq": 8},
            ),
            None,
        ),
        (
            TaskEvent(
                "executor_completed",
                {"task_id": task.id, "run_id": "run-old", "state": "completed"},
            ),
            revision,
        ),
    ):
        with pytest.raises(TaskStateConflict, match="active executor run"):
            TaskState(paths).record(event, expected=expected)

    assert [record["type"] for record in ledger.read_all()] == [
        "task_started",
        "executor_dispatched",
        "executor_dispatched",
    ]


def test_unreconciled_executor_dispatch_is_a_non_projecting_fact(tmp_repo: Path):
    paths, task, ledger, _started = _started_task(tmp_repo)
    ledger.append(
        {
            "type": "executor_dispatched",
            "task_id": task.id,
            "adapter": "multica",
            "run_id": "run-current",
        }
    )

    commit = TaskState(paths).record(
        TaskEvent(
            "executor_dispatch_unreconciled",
            {
                "task_id": task.id,
                "adapter": "multica",
                "run_id": "run-conflicted",
                "triggered_by": "dashboard_restarted",
                "stage": "ledger_conflict",
                "evidence_ref": "runs/run-conflicted.json",
            },
        )
    )

    assert commit.records[-1]["type"] == "executor_dispatch_unreconciled"
    assert commit.snapshot.status.phase == "executing"
    assert commit.snapshot.status.executor_run is not None
    assert commit.snapshot.status.executor_run.run_id == "run-current"


def test_current_preserves_historical_unknown_event_as_warning(tmp_repo: Path):
    paths, _task, ledger, _started = _started_task(tmp_repo)
    unknown = ledger.append({"type": "legacy_extension", "value": 1})

    snapshot = TaskState(paths).current()

    assert snapshot is not None
    assert snapshot.revision.seq == unknown["seq"]
    assert snapshot.status.phase == "executing"
    assert any("legacy_extension" in warning for warning in snapshot.warnings)


def test_historical_unknown_event_cannot_change_projected_fields(tmp_repo: Path):
    paths, _task, ledger, _started = _started_task(tmp_repo)
    ledger.append(
        {
            "type": "legacy_extension",
            "phase": "done",
            "last_seq": 99,
        }
    )

    snapshot = TaskState(paths).current()

    assert snapshot is not None
    assert snapshot.status.phase == "executing"
    assert snapshot.status.last_event_seq is None


def test_historical_fact_phase_cannot_change_projected_lifecycle(tmp_repo: Path):
    paths, _task, ledger, _started = _started_task(tmp_repo)
    ledger.append({"type": "checkpoint", "last_seq": 9, "phase": "done"})

    snapshot = TaskState(paths).current()

    assert snapshot is not None
    assert snapshot.status.phase == "executing"
    assert snapshot.status.last_event_seq == 9


def test_dispatch_failure_clears_checkpoint_cursor(tmp_repo: Path):
    paths, _task, ledger, _started = _started_task(tmp_repo)
    ledger.append({"type": "checkpoint", "last_seq": 9})
    ledger.append({"type": "dashboard_executor_dispatch_failed", "error": "offline"})

    snapshot = TaskState(paths).current()

    assert snapshot is not None
    assert snapshot.status.phase == "blocked"
    assert snapshot.status.last_event_seq is None


def test_current_returns_snapshot_warning_when_cache_repair_fails(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths, _task, _ledger, _started = _started_task(tmp_repo)
    monkeypatch.setattr(
        "agos.core.task_state.save_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read-only cache")),
    )

    snapshot = TaskState(paths).current()

    assert snapshot is not None
    assert snapshot.status.phase == "executing"
    assert any("read-only cache" in warning for warning in snapshot.warnings)


def test_record_rejects_tampered_ledger_before_append(tmp_repo: Path):
    paths, _task, ledger, _started = _started_task(tmp_repo)
    ledger.append({"type": "checkpoint", "last_seq": 1})
    lines = paths.ledger.read_text(encoding="utf-8").splitlines()
    lines[-1] = lines[-1].replace('"last_seq": 1', '"last_seq": 2')
    tampered = "\n".join(lines) + "\n"
    paths.ledger.write_text(tampered, encoding="utf-8")

    with pytest.raises(LedgerTamperError):
        TaskState(paths).record(TaskEvent("checkpoint", {"last_seq": 2}))

    assert paths.ledger.read_text(encoding="utf-8") == tampered


def test_record_initializes_an_empty_ledger_and_returns_contiguous_records(tmp_repo: Path):
    paths, task = _save_task(tmp_repo, gates=["tests_pass"])

    commit = TaskState(paths).record(
        TaskEvent("task_started", {"task_id": task.id, "title": task.title}),
        TaskEvent("gates_locked", {"task_id": task.id, "gates": ["tests_pass"]}),
        expected=TaskRevision.empty(),
    )

    assert [record["seq"] for record in commit.records] == [1, 2]
    assert [record["type"] for record in commit.records] == ["task_started", "gates_locked"]
    assert commit.snapshot.revision == TaskRevision(
        seq=2,
        head_hash=str(commit.records[-1]["hash"]),
    )
    assert commit.snapshot.status.gates == {"tests_pass": GateState()}
    assert commit.cache_synced is True
    Ledger(paths.ledger).verify_chain()


def test_empty_legacy_cache_is_baselined_before_initialization(tmp_repo: Path):
    paths, task = _save_task(tmp_repo, gates=["tests_pass"])
    save_status(
        Status(
            task_id=task.id,
            phase="gated",
            gates={"tests_pass": GateState(state="pass")},
            ledger_head_hash="",
            last_event_seq=7,
        ),
        paths,
    )
    state = TaskState(paths)

    current = state.current()
    assert current is not None
    assert current.status.phase == "executing"
    commit = state.record(
        TaskEvent("task_started", {"task_id": task.id, "title": task.title}),
        expected=TaskRevision.empty(),
    )

    assert [record["type"] for record in commit.records] == [
        "task_state_baselined",
        "task_started",
    ]
    paths.status_json.unlink()
    recovered = state.current()
    assert recovered is not None
    assert recovered.status.phase == "gated"
    assert recovered.status.gates["tests_pass"].state == "pass"
    assert recovered.status.last_event_seq == 7


def test_initialization_and_explicit_fact_revision_fail_without_writing(tmp_repo: Path):
    paths, task = _save_task(tmp_repo)
    state = TaskState(paths)
    started = TaskEvent("task_started", {"task_id": task.id, "title": task.title})

    with pytest.raises(TaskStateConflict, match="TaskRevision.empty"):
        state.record(started)
    with pytest.raises(TaskStateConflict, match="TaskRevision.empty"):
        state.record(started, expected=TaskRevision(seq=1, head_hash="not-empty"))
    assert not paths.ledger.exists() or Ledger(paths.ledger).read_all() == []

    initial = state.record(started, expected=TaskRevision.empty())
    before = Ledger(paths.ledger).read_all()
    with pytest.raises(TaskStateConflict, match="revision mismatch"):
        state.record(
            TaskEvent("checkpoint", {"last_seq": 1}),
            expected=TaskRevision(
                seq=initial.snapshot.revision.seq,
                head_hash="0" * 64,
            ),
        )
    assert Ledger(paths.ledger).read_all() == before


def test_record_rejects_reserved_metadata_and_unknown_new_events(tmp_repo: Path):
    paths, _task, ledger, _started = _started_task(tmp_repo)
    before = ledger.read_all()

    with pytest.raises(TaskStateValidationError, match="reserved"):
        TaskEvent("checkpoint", {"seq": 99})
    with pytest.raises(TaskStateValidationError, match="not registered"):
        TaskState(paths).record(TaskEvent("misspelled_checkpoint", {}))

    assert ledger.read_all() == before


def test_record_prevalidates_entire_batch_and_wrong_task_identity(tmp_repo: Path):
    paths, _task, ledger, _started = _started_task(tmp_repo)
    before = ledger.read_all()

    with pytest.raises(TaskStateValidationError, match="not registered"):
        TaskState(paths).record(
            TaskEvent("checkpoint", {"last_seq": 1}),
            TaskEvent("misspelled_review_completed", {}),
        )
    with pytest.raises(TaskStateValidationError, match="does not match active task"):
        TaskState(paths).record(
            TaskEvent(
                "review_started",
                {
                    "task_id": "another-task",
                    "review_id": "review-1",
                    "packet_ref": "reviews/review-1/packet.json",
                },
            )
        )

    assert ledger.read_all() == before


def test_record_rejects_fact_phase_override(tmp_repo: Path):
    paths, _task, ledger, _started = _started_task(tmp_repo)

    with pytest.raises(TaskStateValidationError, match="phase.*transition"):
        TaskState(paths).record(
            TaskEvent("checkpoint", {"last_seq": 3, "phase": "done"})
        )

    assert [record["type"] for record in ledger.read_all()] == ["task_started"]


@pytest.mark.parametrize("event_name", ["executor_completed", "closeout_completed"])
def test_record_rejects_incomplete_lifecycle_events(tmp_repo: Path, event_name: str):
    paths, _task, ledger, started = _started_task(tmp_repo)

    with pytest.raises(TaskStateValidationError, match="missing required"):
        TaskState(paths).record(
            TaskEvent(event_name, {}),
            expected=TaskRevision(seq=1, head_hash=str(started["hash"])),
        )

    assert [record["type"] for record in ledger.read_all()] == ["task_started"]


def test_record_rejects_invalid_lifecycle_event_fields(tmp_repo: Path):
    paths, task, ledger, started = _started_task(tmp_repo)
    revision = TaskRevision(seq=1, head_hash=str(started["hash"]))
    cases = [
        (
            TaskEvent("executor_dispatched", {"adapter": "", "run_id": "run-1"}),
            "adapter",
        ),
        (
            TaskEvent("executor_completed", {"run_id": "run-1", "state": "failed"}),
            "completed",
        ),
        (
            TaskEvent("executor_blocked", {"run_id": "run-1", "state": "completed"}),
            "blocked",
        ),
        (
            TaskEvent(
                "closeout_completed",
                {"task_id": task.id, "proof_refs": [], "finding_count": 0},
            ),
            "proof_refs",
        ),
        (
            TaskEvent(
                "closeout_completed",
                {
                    "task_id": task.id,
                    "proof_refs": {"json": "proof.json", "md": "proof.md"},
                    "finding_count": -1,
                },
            ),
            "finding_count",
        ),
        (
            TaskEvent(
                "task_execution_blocked",
                {
                    "task_id": task.id,
                    "mode": "unsupported",
                    "run_id": "run-1",
                    "state": "failed",
                },
            ),
            "mode",
        ),
    ]

    for event, message in cases:
        with pytest.raises(TaskStateValidationError, match=message):
            TaskState(paths).record(event, expected=revision)

    assert [record["type"] for record in ledger.read_all()] == ["task_started"]


def test_record_prevalidates_initialization_event_fields(tmp_repo: Path):
    paths, task = _save_task(tmp_repo)

    with pytest.raises(TaskStateValidationError, match="gates"):
        TaskState(paths).record(
            TaskEvent("task_started", {"task_id": task.id}),
            TaskEvent("gates_locked", {"task_id": task.id, "gates": "tests_pass"}),
            expected=TaskRevision.empty(),
        )

    assert not paths.ledger.exists() or Ledger(paths.ledger).read_all() == []


@pytest.mark.parametrize(
    "events",
    [
        (TaskEvent("gates_locked", {"task_id": "task-state-test", "gates": []}),),
        (
            TaskEvent(
                "task_execution_started",
                {
                    "task_id": "task-state-test",
                    "mode": "legacy",
                    "output_contract": "legacy",
                },
            ),
        ),
        (
            TaskEvent("gates_locked", {"task_id": "task-state-test", "gates": []}),
            TaskEvent("task_started", {"task_id": "task-state-test"}),
        ),
        (
            TaskEvent("task_started", {"task_id": "task-state-test"}),
            TaskEvent("task_started", {"task_id": "task-state-test"}),
        ),
    ],
)
def test_empty_ledger_requires_one_leading_task_started_event(tmp_repo: Path, events):
    paths, _task = _save_task(tmp_repo)

    with pytest.raises(TaskStateConflict, match="task_started"):
        TaskState(paths).record(*events, expected=TaskRevision.empty())

    assert not paths.ledger.exists() or Ledger(paths.ledger).read_all() == []


@pytest.mark.parametrize(
    "event",
    [
        TaskEvent("dashboard_paused", {"phase": []}),
        TaskEvent(
            "task_execution_blocked",
            {
                "task_id": "task-state-test",
                "mode": [],
                "run_id": "run-1",
                "state": "failed",
            },
        ),
        TaskEvent(
            "gate_evaluated",
            {"gate": "tests_pass", "state": []},
        ),
    ],
)
def test_record_normalizes_unhashable_event_choices_to_validation_errors(tmp_repo: Path, event):
    paths, _task, ledger, _started = _started_task(tmp_repo)

    with pytest.raises(TaskStateValidationError):
        TaskState(paths).record(event, expected=TaskRevision(seq=1, head_hash=ledger.head_hash()))

    assert [record["type"] for record in ledger.read_all()] == ["task_started"]


def test_transition_requires_current_revision_and_rejects_stale_revision(tmp_repo: Path):
    paths, task, ledger, started = _started_task(tmp_repo)
    state = TaskState(paths)

    with pytest.raises(TaskStateConflict, match="requires an expected revision"):
        state.record(TaskEvent("dashboard_paused", {"task_id": task.id, "phase": "blocked"}))
    with pytest.raises(TaskStateConflict, match="requires an expected revision"):
        state.record(
            TaskEvent(
                "executor_dispatched",
                {"task_id": task.id, "adapter": "multica", "run_id": "run-1"},
            )
        )

    first = state.record(
        TaskEvent("dashboard_paused", {"task_id": task.id, "phase": "blocked"}),
        expected=TaskRevision(seq=1, head_hash=str(started["hash"])),
    )
    assert first.snapshot.status.phase == "blocked"

    with pytest.raises(TaskStateConflict, match="revision mismatch"):
        state.record(
            TaskEvent("dashboard_resumed", {"task_id": task.id, "phase": "executing"}),
            expected=TaskRevision(seq=1, head_hash=str(started["hash"])),
        )

    assert [record["type"] for record in ledger.read_all()] == [
        "task_started",
        "dashboard_paused",
    ]


def test_concurrent_transitions_from_one_revision_allow_one_commit(tmp_repo: Path):
    paths, task, ledger, started = _started_task(tmp_repo)
    expected = TaskRevision(seq=1, head_hash=str(started["hash"]))
    barrier = threading.Barrier(2)

    def transition(name: str) -> str:
        barrier.wait(timeout=5)
        try:
            TaskState(paths).record(
                TaskEvent(name, {"task_id": task.id, "phase": "blocked"}),
                expected=expected,
            )
        except TaskStateConflict:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(transition, ["dashboard_paused", "dashboard_paused"]))

    assert sorted(results) == ["committed", "conflict"]
    assert [record["type"] for record in ledger.read_all()] == [
        "task_started",
        "dashboard_paused",
    ]


def test_record_baselines_legacy_cache_once_and_replays_without_cache(tmp_repo: Path):
    paths, task, ledger, started = _started_task(tmp_repo)
    save_status(
        Status(
            task_id=task.id,
            phase="gated",
            gates={},
            ledger_head_hash=str(started["hash"]),
            last_event_seq=7,
        ),
        paths,
    )
    state = TaskState(paths)

    commit = state.record(TaskEvent("checkpoint", {"task_id": task.id, "last_seq": 8}))

    assert [record["type"] for record in commit.records] == [
        "task_state_baselined",
        "checkpoint",
    ]
    paths.status_json.unlink()
    recovered = state.current()
    assert recovered is not None
    assert recovered.status.phase == "gated"
    assert recovered.status.last_event_seq == 8

    state.record(
        TaskEvent(
            "review_started",
            {
                "task_id": task.id,
                "review_id": "review-1",
                "packet_ref": "reviews/review-1/packet.json",
            },
        )
    )
    assert [
        record["type"] for record in ledger.read_all()
    ].count("task_state_baselined") == 1


def test_current_then_record_preserves_pending_legacy_baseline(tmp_repo: Path):
    paths, task, ledger, started = _started_task(tmp_repo, gates=["tests_pass"])
    save_status(
        Status(
            task_id=task.id,
            phase="gated",
            gates={"tests_pass": GateState(state="pass")},
            ledger_head_hash=str(started["hash"]),
            last_event_seq=7,
        ),
        paths,
    )
    state = TaskState(paths)

    current = state.current()
    assert current is not None
    assert current.status.phase == "executing"
    state.record(TaskEvent("checkpoint", {"task_id": task.id, "last_seq": 8}))

    assert [record["type"] for record in ledger.read_all()] == [
        "task_started",
        "task_state_baselined",
        "checkpoint",
    ]
    paths.status_json.unlink()
    recovered = state.current()
    assert recovered is not None
    assert recovered.status.phase == "gated"
    assert recovered.status.gates["tests_pass"].state == "pass"
    assert recovered.status.last_event_seq == 8


def test_record_reports_cache_failure_as_a_successful_commit(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths, task, ledger, _started = _started_task(tmp_repo)
    original_save = save_status
    calls = 0

    def fail_once(status, target_paths):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("cache unavailable")
        return original_save(status, target_paths)

    monkeypatch.setattr("agos.core.task_state.save_status", fail_once)
    state = TaskState(paths)

    commit = state.record(TaskEvent("checkpoint", {"task_id": task.id, "last_seq": 4}))

    assert commit.cache_synced is False
    assert commit.records[-1] == ledger.read_all()[-1]
    assert any("cache unavailable" in warning for warning in commit.warnings)
    repaired = state.current()
    assert repaired is not None
    assert repaired.status.ledger_head_hash == ledger.head_hash()


def test_batch_failure_reports_confirmed_prefix_and_unprocessed_events(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths, task, ledger, _started = _started_task(tmp_repo)
    original_append = Ledger._append_unlocked
    calls = 0

    def fail_second(self, record):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return original_append(self, record)

    monkeypatch.setattr(Ledger, "_append_unlocked", fail_second)
    first = TaskEvent(
        "review_started",
        {
            "task_id": task.id,
            "review_id": "review-1",
            "packet_ref": "reviews/review-1/packet.json",
        },
    )
    second = TaskEvent(
        "review_completed",
        {
            "task_id": task.id,
            "review_id": "review-1",
            "report_ref": "reviews/review-1/report.json",
            "open_blocking_count": 0,
        },
    )

    with pytest.raises(TaskStateBatchInterrupted) as raised:
        TaskState(paths).record(first, second)

    assert [record["type"] for record in raised.value.confirmed_records] == [
        "review_started"
    ]
    assert raised.value.unprocessed_events == (second,)
    assert [record["type"] for record in ledger.read_all()] == [
        "task_started",
        "review_started",
    ]


def test_append_failure_before_write_is_retryable_and_does_not_mutate_ledger(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths, task, ledger, _started = _started_task(tmp_repo)
    before = ledger.read_all()
    monkeypatch.setattr(
        Ledger,
        "_append_unlocked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    with pytest.raises(TaskStateWriteError) as raised:
        TaskState(paths).record(TaskEvent("checkpoint", {"task_id": task.id, "last_seq": 1}))

    assert raised.value.retryable is True
    assert ledger.read_all() == before


def test_baseline_append_failure_exposes_only_the_retryable_caller_event(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths, task, ledger, started = _started_task(tmp_repo)
    save_status(
        Status(
            task_id=task.id,
            phase="gated",
            gates={},
            ledger_head_hash=str(started["hash"]),
        ),
        paths,
    )
    event = TaskEvent("checkpoint", {"task_id": task.id, "last_seq": 2})
    monkeypatch.setattr(
        Ledger,
        "_append_unlocked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    with pytest.raises(TaskStateWriteError) as raised:
        TaskState(paths).record(event)

    assert raised.value.event == event
    assert [record["type"] for record in ledger.read_all()] == ["task_started"]


def test_ambiguous_append_raises_commit_indeterminate(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    paths, task, ledger, _started = _started_task(tmp_repo)
    original_append = Ledger._append_unlocked

    def append_then_fail(self, record):
        original_append(self, record)
        raise OSError("connection lost after write")

    monkeypatch.setattr(Ledger, "_append_unlocked", append_then_fail)

    with pytest.raises(TaskStateCommitIndeterminate) as raised:
        TaskState(paths).record(TaskEvent("checkpoint", {"task_id": task.id, "last_seq": 3}))

    assert raised.value.revision_before.seq == 1
    assert raised.value.pending_events[0].name == "checkpoint"
    assert ledger.read_all()[-1]["type"] == "checkpoint"


def test_concurrent_fact_writers_share_one_verified_state_chain(tmp_repo: Path):
    paths, _task, _ledger, _started = _started_task(tmp_repo)
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [
        context.Process(target=_record_facts, args=(str(tmp_repo), worker, 4, start_event))
        for worker in range(3)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    records = Ledger(paths.ledger).read_verified()
    assert [record["seq"] for record in records] == list(range(1, 14))
    assert len({(record["worker"], record["index"]) for record in records[1:]}) == 12
    snapshot = TaskState(paths).current()
    assert snapshot is not None
    assert snapshot.revision.seq == 13
    assert snapshot.status.ledger_head_hash == records[-1]["hash"]


def test_task_event_facts_are_defensively_immutable():
    source = {"evidence_refs": ["one"]}
    event = TaskEvent("checkpoint", source)
    source["evidence_refs"].append("two")
    first = event.facts
    first["evidence_refs"].append("three")

    assert event.facts == {"evidence_refs": ["one"]}
    assert json.loads(json.dumps(event.facts)) == event.facts


@pytest.mark.parametrize(
    "facts",
    [
        [("key", "value")],
        {"value": float("nan")},
        {"value": {"not-json"}},
        {1: "non-string-key"},
    ],
)
def test_task_event_rejects_non_json_object_inputs(facts):
    with pytest.raises(TaskStateValidationError):
        TaskEvent("checkpoint", facts)


@pytest.mark.parametrize("seq", [True, False, 1.0, 1.5])
def test_task_revision_rejects_non_integer_sequences(seq):
    with pytest.raises(ValueError, match="integer"):
        TaskRevision(seq=seq, head_hash="" if not seq else "hash")
