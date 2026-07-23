"""Authoritative task state backed by the hash-chained task ledger."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import json
from typing import Any, Literal, cast

from agos.core.file_lock import exclusive_file_lock
from agos.core.ledger import Ledger, LedgerTamperError
from agos.core.repo import AgosPaths
from agos.core.status import (
    ExecutorRunInfo,
    GateState,
    Status,
    read_status_cache,
    save_status,
    status_cache_requires_baseline,
)
from agos.core.task import Task, load_task


_RESERVED_EVENT_FIELDS = frozenset({"type", "seq", "ts", "prev_hash", "hash"})
_PHASES = frozenset({"executing", "gated", "done", "blocked"})


class TaskStateError(Exception):
    """Base class for TaskState failures."""


class TaskStateValidationError(TaskStateError, ValueError):
    """Raised before an invalid event can reach the ledger."""


class TaskStateConflict(TaskStateError):
    """Raised when a lifecycle write does not target the current revision."""

    def __init__(
        self,
        message: str,
        *,
        expected: TaskRevision | None,
        actual: TaskRevision,
    ) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class TaskStateIntegrityError(TaskStateError):
    """Raised when verified records do not belong to the active task."""


class TaskStateWriteError(TaskStateError):
    """Raised when a write is confirmed not to have reached the ledger."""

    def __init__(self, event: TaskEvent, cause: Exception) -> None:
        super().__init__(f"task event {event.name!r} was not committed: {cause}")
        self.event = event
        self.retryable = True


class TaskStateCommitIndeterminate(TaskStateError):
    """Raised when the caller must re-read before deciding whether to retry."""

    def __init__(
        self,
        *,
        revision_before: TaskRevision,
        confirmed_records: tuple[dict[str, Any], ...],
        pending_events: tuple[TaskEvent, ...],
        observed_revision: TaskRevision | None,
        cause: Exception,
    ) -> None:
        super().__init__(
            "task state commit is indeterminate; read the current revision before retrying: "
            f"{cause}"
        )
        self.revision_before = revision_before
        self.confirmed_records = confirmed_records
        self.pending_events = pending_events
        self.observed_revision = observed_revision
        self.retryable = False


class TaskStateBatchInterrupted(TaskStateError):
    """Raised after a durable batch prefix when later events were not written."""

    def __init__(
        self,
        *,
        confirmed_records: tuple[dict[str, Any], ...],
        unprocessed_events: tuple[TaskEvent, ...],
        cache_synced: bool,
        cause: Exception,
    ) -> None:
        super().__init__(
            f"task state batch stopped after {len(confirmed_records)} confirmed record(s): {cause}"
        )
        self.confirmed_records = confirmed_records
        self.unprocessed_events = unprocessed_events
        self.cache_synced = cache_synced
        self.retryable = False


@dataclass(frozen=True, init=False)
class TaskEvent:
    """Immutable event name and defensively copied JSON facts."""

    name: str
    _facts_json: str = field(repr=False)

    def __init__(self, name: str, facts: Mapping[str, Any] | None = None) -> None:
        if not isinstance(name, str) or not name.strip():
            raise TaskStateValidationError("event name must be non-empty text")
        if facts is not None and not isinstance(facts, Mapping):
            raise TaskStateValidationError("event facts must be a JSON object mapping")
        raw_facts = dict(facts or {})
        if any(not isinstance(key, str) for key in raw_facts):
            raise TaskStateValidationError("event fact keys must be strings")
        reserved = sorted(_RESERVED_EVENT_FIELDS.intersection(raw_facts))
        if reserved:
            raise TaskStateValidationError(
                "event facts contain reserved ledger metadata: " + ", ".join(reserved)
            )
        try:
            payload = json.dumps(
                raw_facts,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            normalized = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise TaskStateValidationError(f"event facts must be JSON-compatible: {exc}") from exc
        if not isinstance(normalized, dict):  # pragma: no cover - dict() above is defensive
            raise TaskStateValidationError("event facts must be a JSON object")
        object.__setattr__(self, "name", name.strip())
        object.__setattr__(self, "_facts_json", payload)

    @property
    def facts(self) -> dict[str, Any]:
        """Return a copy so callers cannot mutate a pending event."""

        return cast(dict[str, Any], json.loads(self._facts_json))

    def _ledger_payload(self) -> dict[str, Any]:
        return {"type": self.name, **self.facts}


@dataclass(frozen=True)
class TaskRevision:
    """Optimistic-concurrency cursor for one verified ledger head."""

    seq: int
    head_hash: str

    def __post_init__(self) -> None:
        if type(self.seq) is not int:
            raise ValueError("revision seq must be an integer")
        if not isinstance(self.head_hash, str):
            raise ValueError("revision head_hash must be text")
        if self.seq < 0:
            raise ValueError("revision seq must not be negative")
        if self.seq == 0 and self.head_hash:
            raise ValueError("empty revision cannot have a head hash")
        if self.seq > 0 and not self.head_hash:
            raise ValueError("non-empty revision requires a head hash")

    @classmethod
    def empty(cls) -> TaskRevision:
        return cls(seq=0, head_hash="")


@dataclass(frozen=True)
class TaskSnapshot:
    """One fully replayed task view and its source revision."""

    task: Task
    status: Status
    revision: TaskRevision
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskCommit:
    """Records confirmed by one call and the resulting task snapshot."""

    records: tuple[dict[str, Any], ...]
    snapshot: TaskSnapshot
    cache_synced: bool
    warnings: tuple[str, ...] = ()


RevisionPolicy = Literal["fact", "transition", "initialization"]


@dataclass
class _Projection:
    phase: str
    executor_run: ExecutorRunInfo | None
    gates: dict[str, GateState]
    last_event_seq: int | None
    baseline_applied: bool = False


Reducer = Callable[[_Projection, Task, Mapping[str, Any]], None]
Validator = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class _EventSpec:
    policy: RevisionPolicy
    reducer: Reducer
    validator: Validator
    writable: bool = True


class TaskState:
    """Serialize task facts and project `status.json` from the verified ledger."""

    def __init__(self, paths: AgosPaths) -> None:
        self.paths = paths
        self._ledger = Ledger(paths.ledger)
        self._pending_baseline: tuple[TaskRevision, Status] | None = None

    def current(self) -> TaskSnapshot | None:
        """Verify and replay the complete ledger, repairing the cache when needed."""

        if not self.paths.task_yaml.is_file():
            return None
        with exclusive_file_lock(self.paths.ledger):
            if not self.paths.task_yaml.is_file():
                return None
            task = load_task(self.paths.task_yaml)
            records = self._read_verified_unlocked(task)
            baseline = self._legacy_baseline_event(task, records)
            if baseline is not None:
                self._pending_baseline = (
                    _revision(records),
                    Status.model_validate(baseline.facts["status"]),
                )
            elif self._pending_baseline is not None and (
                self._pending_baseline[1].task_id != task.id
                or any(record.get("type") == "task_state_baselined" for record in records)
            ):
                self._pending_baseline = None
            snapshot = _snapshot(task, records)
            if baseline is not None:
                return TaskSnapshot(
                    task=snapshot.task,
                    status=snapshot.status,
                    revision=snapshot.revision,
                    warnings=(
                        *snapshot.warnings,
                        "baseline-eligible legacy status cache was preserved until the next task-state write",
                    ),
                )
            cache_synced, cache_warning = self._sync_cache(snapshot.status)
            if cache_synced or cache_warning is None:
                return snapshot
            return TaskSnapshot(
                task=snapshot.task,
                status=snapshot.status,
                revision=snapshot.revision,
                warnings=(*snapshot.warnings, cache_warning),
            )

    def record(
        self,
        event: TaskEvent,
        *more: TaskEvent,
        expected: TaskRevision | None = None,
    ) -> TaskCommit:
        """Validate and durably append one event or one short ordered batch."""

        events = (event, *more)
        specs = tuple(_validate_new_event(item) for item in events)
        with exclusive_file_lock(self.paths.ledger):
            if not self.paths.task_yaml.is_file():
                raise TaskStateValidationError(f"task definition not found: {self.paths.task_yaml}")
            task = load_task(self.paths.task_yaml)
            records = self._read_verified_unlocked(task)
            revision_before = _revision(records)
            _validate_revision(events, specs, expected=expected, actual=revision_before)
            for item in events:
                _validate_new_event_identity(item, task)

            baseline = self._baseline_event_for_record(task, records)
            _validate_current_executor_run(
                task,
                records,
                events,
                baseline=baseline,
                expected=expected,
                actual=revision_before,
            )
            pending = list(events)
            if baseline is not None:
                pending.insert(0, baseline)

            confirmed: list[dict[str, Any]] = []
            for index, item in enumerate(pending):
                try:
                    appended = self._ledger._append_unlocked(item._ledger_payload())
                except Exception as exc:
                    caller_pending = tuple(
                        candidate
                        for candidate in pending[index:]
                        if candidate.name != "task_state_baselined"
                    )
                    self._raise_append_failure(
                        task=task,
                        records=records,
                        revision_before=revision_before,
                        confirmed=confirmed,
                        pending=caller_pending,
                        cause=exc,
                    )
                    raise AssertionError("append failure classifier returned")  # pragma: no cover
                records.append(appended)
                confirmed.append(appended)

            self._pending_baseline = None
            snapshot = _snapshot(task, records)
            cache_synced, cache_warning = self._sync_cache(snapshot.status)
            warnings = snapshot.warnings
            if cache_warning is not None:
                warnings = (*warnings, cache_warning)
                snapshot = TaskSnapshot(
                    task=snapshot.task,
                    status=snapshot.status,
                    revision=snapshot.revision,
                    warnings=warnings,
                )
            return TaskCommit(
                records=tuple(_copy_record(item) for item in confirmed),
                snapshot=snapshot,
                cache_synced=cache_synced,
                warnings=warnings,
            )

    def _read_verified_unlocked(self, task: Task) -> list[dict[str, Any]]:
        try:
            raw_records = self._ledger._records_unlocked()
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise TaskStateIntegrityError(f"task ledger is not valid JSONL: {exc}") from exc
        if not all(isinstance(record, dict) for record in raw_records):
            raise TaskStateIntegrityError("every task ledger line must be a JSON object")
        records = cast(list[dict[str, Any]], raw_records)
        Ledger._verify_records(records)
        _validate_record_identities(task, records)
        return records

    def _legacy_baseline_event(
        self,
        task: Task,
        records: list[dict[str, Any]],
    ) -> TaskEvent | None:
        if any(record.get("type") == "task_state_baselined" for record in records):
            return None
        cached, _cache_error = _read_cache(self.paths)
        if cached is None or cached.task_id != task.id:
            return None
        revision = _revision(records)
        if cached.ledger_head_hash != revision.head_hash:
            return None
        projected = _project_status(task, records)[0]
        if not status_cache_requires_baseline(task, records, cached, projected):
            return None
        return TaskEvent(
            "task_state_baselined",
            {"task_id": task.id, "status": cached.model_dump(mode="json")},
        )

    def _baseline_event_for_record(
        self,
        task: Task,
        records: list[dict[str, Any]],
    ) -> TaskEvent | None:
        if any(record.get("type") == "task_state_baselined" for record in records):
            self._pending_baseline = None
            return None
        if self._pending_baseline is None:
            return self._legacy_baseline_event(task, records)

        pending_revision, pending_status = self._pending_baseline
        if pending_status.task_id != task.id:
            self._pending_baseline = None
            return self._legacy_baseline_event(task, records)
        if pending_revision == TaskRevision.empty():
            rebased_status = pending_status
            if records:
                synthetic_baseline = {
                    "type": "task_state_baselined",
                    "task_id": task.id,
                    "status": pending_status.model_dump(mode="json"),
                    "seq": 0,
                    "hash": "",
                }
                rebased_status, _warnings = _project_status(
                    task,
                    [synthetic_baseline, *records],
                )
                rebased_status = rebased_status.model_copy(
                    update={"ledger_head_hash": _revision(records).head_hash}
                )
            return TaskEvent(
                "task_state_baselined",
                {"task_id": task.id, "status": rebased_status.model_dump(mode="json")},
            )
        baseline_index = next(
            (
                index
                for index, record in enumerate(records)
                if record.get("seq") == pending_revision.seq
                and record.get("hash") == pending_revision.head_hash
            ),
            None,
        )
        if baseline_index is None:
            self._pending_baseline = None
            return self._legacy_baseline_event(task, records)

        rebased_status = pending_status
        suffix = records[baseline_index + 1 :]
        if suffix:
            synthetic_baseline = {
                "type": "task_state_baselined",
                "task_id": task.id,
                "status": pending_status.model_dump(mode="json"),
                "seq": pending_revision.seq,
                "hash": pending_revision.head_hash,
            }
            rebased_status, _warnings = _project_status(
                task,
                [*records[: baseline_index + 1], synthetic_baseline, *suffix],
            )
        rebased_status = rebased_status.model_copy(
            update={"ledger_head_hash": _revision(records).head_hash}
        )
        return TaskEvent(
            "task_state_baselined",
            {"task_id": task.id, "status": rebased_status.model_dump(mode="json")},
        )

    def _sync_cache(self, status: Status) -> tuple[bool, str | None]:
        cached, _cache_error = _read_cache(self.paths)
        if cached == status:
            return True, None
        try:
            save_status(status, self.paths)
        except (OSError, ValueError) as exc:
            return False, f"status cache was not synchronized: {exc}"
        return True, None

    def _raise_append_failure(
        self,
        *,
        task: Task,
        records: list[dict[str, Any]],
        revision_before: TaskRevision,
        confirmed: list[dict[str, Any]],
        pending: tuple[TaskEvent, ...],
        cause: Exception,
    ) -> None:
        try:
            observed = self._read_verified_unlocked(task)
        except (TaskStateIntegrityError, LedgerTamperError, OSError, ValueError):
            observed = None

        confirmed_copy = tuple(_copy_record(item) for item in confirmed)
        if observed == records:
            cache_synced = True
            if confirmed:
                snapshot = _snapshot(task, records)
                cache_synced, _warning = self._sync_cache(snapshot.status)
            if confirmed or len(pending) > 1:
                raise TaskStateBatchInterrupted(
                    confirmed_records=confirmed_copy,
                    unprocessed_events=pending,
                    cache_synced=cache_synced,
                    cause=cause,
                ) from cause
            raise TaskStateWriteError(pending[0], cause) from cause

        observed_revision = _revision(observed) if observed is not None else None
        raise TaskStateCommitIndeterminate(
            revision_before=revision_before,
            confirmed_records=confirmed_copy,
            pending_events=pending,
            observed_revision=observed_revision,
            cause=cause,
        ) from cause


def _copy_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(json.dumps(record, ensure_ascii=False)))


def _read_cache(paths: AgosPaths) -> tuple[Status | None, Exception | None]:
    try:
        return read_status_cache(paths), None
    except (OSError, UnicodeError, ValueError) as exc:
        return None, exc


def _revision(records: list[dict[str, Any]] | None) -> TaskRevision:
    if not records:
        return TaskRevision.empty()
    tail = records[-1]
    return TaskRevision(seq=int(tail["seq"]), head_hash=str(tail["hash"]))


def _snapshot(task: Task, records: list[dict[str, Any]]) -> TaskSnapshot:
    status, warnings = _project_status(task, records)
    return TaskSnapshot(
        task=task.model_copy(deep=True),
        status=status,
        revision=_revision(records),
        warnings=warnings,
    )


def _project_status(
    task: Task,
    records: list[dict[str, Any]],
) -> tuple[Status, tuple[str, ...]]:
    projection = _Projection(
        phase="executing",
        executor_run=None,
        gates={gate_id: GateState() for gate_id in task.gates},
        last_event_seq=None,
    )
    warnings: list[str] = []
    for record in records:
        event_name = str(record.get("type", ""))
        spec = _EVENT_REGISTRY.get(event_name)
        if spec is None:
            warnings.append(
                f"ledger record {record.get('seq', '?')} uses unknown event {event_name!r}; "
                "it was preserved as a projection no-op"
            )
            continue
        try:
            spec.reducer(projection, task, record)
        except (TypeError, ValueError) as exc:
            raise TaskStateIntegrityError(
                f"cannot project ledger record {record.get('seq', '?')} ({event_name}): {exc}"
            ) from exc

    status = Status(
        task_id=task.id,
        phase=cast(Any, projection.phase),
        executor_run=projection.executor_run,
        gates=projection.gates,
        ledger_head_hash=str(records[-1]["hash"]) if records else "",
        last_event_seq=projection.last_event_seq,
    )
    return status, tuple(warnings)


def _validate_record_identities(task: Task, records: list[dict[str, Any]]) -> None:
    for record in records:
        task_id = record.get("task_id")
        if task_id is not None and task_id != task.id:
            raise TaskStateIntegrityError(
                f"ledger record {record.get('seq', '?')} task_id {task_id!r} "
                f"does not match active task {task.id!r}"
            )


def _validate_new_event(event: TaskEvent) -> _EventSpec:
    if not isinstance(event, TaskEvent):
        raise TaskStateValidationError("record() accepts TaskEvent values only")
    spec = _EVENT_REGISTRY.get(event.name)
    if spec is None:
        raise TaskStateValidationError(f"event {event.name!r} is not registered")
    if not spec.writable:
        raise TaskStateValidationError(f"event {event.name!r} is internal")
    facts = event.facts
    phase = facts.get("phase")
    if phase is not None and (not isinstance(phase, str) or phase not in _PHASES):
        raise TaskStateValidationError(f"event phase is invalid: {phase!r}")
    if phase is not None and spec.policy != "transition":
        raise TaskStateValidationError(
            "top-level phase is only allowed on lifecycle transition events"
        )
    required = _REQUIRED_EVENT_FIELDS.get(event.name, ())
    missing = [field_name for field_name in required if field_name not in facts]
    if missing:
        raise TaskStateValidationError(
            f"event {event.name!r} is missing required fields: {', '.join(missing)}"
        )
    spec.validator(facts)
    return spec


def _validate_new_event_identity(event: TaskEvent, task: Task) -> None:
    task_id = event.facts.get("task_id")
    if task_id != task.id:
        raise TaskStateValidationError(
            f"event task_id {task_id!r} does not match active task {task.id!r}"
        )


def _validate_current_executor_run(
    task: Task,
    records: list[dict[str, Any]],
    events: tuple[TaskEvent, ...],
    *,
    baseline: TaskEvent | None,
    expected: TaskRevision | None,
    actual: TaskRevision,
) -> None:
    """Reject old executor observations after another run becomes authoritative."""

    executor_run = _project_status(task, records)[0].executor_run
    if baseline is not None:
        executor_run = Status.model_validate(baseline.facts["status"]).executor_run
    if executor_run is None:
        return
    for event in events:
        if event.name not in {"checkpoint", "executor_completed", "executor_blocked"}:
            continue
        run_id = event.facts.get("run_id")
        if run_id != executor_run.run_id:
            raise TaskStateConflict(
                "event run_id does not match the active executor run",
                expected=expected,
                actual=actual,
            )


def _validate_revision(
    events: tuple[TaskEvent, ...],
    specs: tuple[_EventSpec, ...],
    *,
    expected: TaskRevision | None,
    actual: TaskRevision,
) -> None:
    initialization = any(spec.policy == "initialization" for spec in specs)
    transition = any(spec.policy == "transition" for spec in specs)
    if initialization:
        if expected != TaskRevision.empty():
            raise TaskStateConflict(
                "task initialization requires expected=TaskRevision.empty()",
                expected=expected,
                actual=actual,
            )
        if actual != TaskRevision.empty():
            raise TaskStateConflict(
                "task initialization requires an empty ledger",
                expected=expected,
                actual=actual,
            )
        if events[0].name != "task_started" or any(
            event.name == "task_started" for event in events[1:]
        ):
            raise TaskStateConflict(
                "empty task state must be initialized with one leading task_started event",
                expected=expected,
                actual=actual,
            )
    elif actual == TaskRevision.empty():
        raise TaskStateConflict(
            "empty task state must be initialized with task_started",
            expected=expected,
            actual=actual,
        )
    elif transition and expected is None:
        raise TaskStateConflict(
            "lifecycle transition requires an expected revision",
            expected=None,
            actual=actual,
        )
    if expected is not None and expected != actual:
        raise TaskStateConflict(
            f"task revision mismatch: expected {expected}, current {actual}",
            expected=expected,
            actual=actual,
        )


def _validate_noop(_facts: Mapping[str, Any]) -> None:
    return None


def _validate_task_started(facts: Mapping[str, Any]) -> None:
    _require_nonempty_text(facts, "task_id")


def _validate_checkpoint(facts: Mapping[str, Any]) -> None:
    value = facts.get("last_seq")
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise TaskStateValidationError("checkpoint last_seq must be an integer")


def _validate_gate(facts: Mapping[str, Any]) -> None:
    _require_nonempty_text(facts, "gate")
    _require_choice(facts, "state", {"pass", "block"})


def _validate_gates_locked(facts: Mapping[str, Any]) -> None:
    _require_nonempty_text(facts, "task_id")
    if not isinstance(facts.get("gates"), list):
        raise TaskStateValidationError("gates_locked gates must be a list")


def _validate_task_execution_started(facts: Mapping[str, Any]) -> None:
    _require_nonempty_text(facts, "task_id")
    _require_choice(facts, "mode", {"legacy", "candidate"})
    _require_choice(facts, "output_contract", {"legacy", "source_code", "standalone"})


def _validate_dispatched(facts: Mapping[str, Any]) -> None:
    _require_nonempty_text(facts, "adapter")
    _require_nonempty_text(facts, "run_id")


def _validate_executor_completed(facts: Mapping[str, Any]) -> None:
    _require_nonempty_text(facts, "run_id")
    if facts.get("state") != "completed":
        raise TaskStateValidationError("executor_completed state must be 'completed'")


def _validate_executor_blocked(facts: Mapping[str, Any]) -> None:
    _require_nonempty_text(facts, "run_id")
    _require_choice(facts, "state", {"blocked", "failed"})


def _validate_task_execution_terminal(facts: Mapping[str, Any]) -> None:
    _require_nonempty_text(facts, "task_id")
    _require_choice(facts, "mode", {"legacy", "candidate"})
    _require_nonempty_text(facts, "run_id")
    _require_choice(facts, "state", {"running", "completed", "blocked", "failed", "stuck"})


def _validate_closeout(facts: Mapping[str, Any]) -> None:
    _require_nonempty_text(facts, "task_id")
    if not isinstance(facts.get("proof_refs"), Mapping):
        raise TaskStateValidationError("closeout_completed proof_refs must be a mapping")
    finding_count = facts.get("finding_count")
    if type(finding_count) is not int or finding_count < 0:
        raise TaskStateValidationError("closeout_completed finding_count must be a non-negative integer")


def _validate_lifecycle_phase(facts: Mapping[str, Any]) -> None:
    _require_choice(facts, "phase", set(_PHASES))


def _validate_unreconciled_dispatch(facts: Mapping[str, Any]) -> None:
    for field_name in ("task_id", "adapter", "run_id", "triggered_by", "stage", "evidence_ref"):
        _require_nonempty_text(facts, field_name)


def _validate_baseline(facts: Mapping[str, Any]) -> None:
    status = Status.model_validate(facts.get("status"))
    task_id = facts.get("task_id")
    if task_id is not None and status.task_id != task_id:
        raise TaskStateValidationError("baseline status task_id does not match its event task_id")


def _require_nonempty_text(facts: Mapping[str, Any], key: str) -> str:
    value = facts.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TaskStateValidationError(f"event field {key!r} must be non-empty text")
    return value.strip()


def _require_choice(facts: Mapping[str, Any], key: str, allowed: set[str]) -> str:
    value = facts.get(key)
    if not isinstance(value, str) or value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise TaskStateValidationError(
            f"event field {key!r} must be one of: {choices}"
        )
    return cast(str, value)


def _reduce_noop(
    _projection: _Projection,
    _task: Task,
    _record: Mapping[str, Any],
) -> None:
    return None


def _reduce_lifecycle_phase(
    projection: _Projection,
    _task: Task,
    record: Mapping[str, Any],
) -> None:
    phase = record.get("phase")
    if phase in _PHASES:
        projection.phase = str(phase)


def _reduce_started(
    projection: _Projection,
    _task: Task,
    record: Mapping[str, Any],
) -> None:
    if not projection.baseline_applied:
        projection.phase = "executing"
    if record.get("type") == "task_execution_started" and isinstance(record.get("seq"), int):
        projection.last_event_seq = int(record["seq"])


def _reduce_dispatched(
    projection: _Projection,
    task: Task,
    record: Mapping[str, Any],
) -> None:
    run_id = _nonempty_text(record.get("run_id"))
    if run_id is not None:
        projection.executor_run = ExecutorRunInfo(
            adapter=_nonempty_text(record.get("adapter")) or task.executor.adapter,
            run_id=run_id,
            issue_id=_nonempty_text(record.get("issue_id")),
        )
    projection.phase = "executing"
    projection.last_event_seq = None


def _reduce_restored(
    projection: _Projection,
    task: Task,
    record: Mapping[str, Any],
) -> None:
    _reduce_lifecycle_phase(projection, task, record)
    if projection.executor_run is None:
        projection.executor_run = ExecutorRunInfo(
            adapter=task.executor.adapter,
            run_id=f"restored-{task.id}",
        )


def _reduce_checkpoint(
    projection: _Projection,
    _task: Task,
    record: Mapping[str, Any],
) -> None:
    last_seq = record.get("last_seq")
    if isinstance(last_seq, int) and not isinstance(last_seq, bool):
        projection.last_event_seq = last_seq


def _reduce_gate(
    projection: _Projection,
    _task: Task,
    record: Mapping[str, Any],
) -> None:
    gate_id = _nonempty_text(record.get("gate"))
    state = record.get("state")
    if gate_id is not None and state in {"pass", "block"}:
        projection.gates[gate_id] = GateState(
            state=cast(Any, state),
            last_evaluated=_nonempty_text(record.get("ts")),
        )


def _reduce_done(
    projection: _Projection,
    _task: Task,
    _record: Mapping[str, Any],
) -> None:
    projection.phase = "done"


def _reduce_blocked(
    projection: _Projection,
    _task: Task,
    _record: Mapping[str, Any],
) -> None:
    projection.phase = "blocked"


def _reduce_dispatch_failed(
    projection: _Projection,
    _task: Task,
    _record: Mapping[str, Any],
) -> None:
    projection.phase = "blocked"
    projection.last_event_seq = None


def _reduce_task_terminal(
    projection: _Projection,
    task: Task,
    record: Mapping[str, Any],
) -> None:
    run_id = _nonempty_text(record.get("run_id"))
    if run_id is not None:
        mode = record.get("mode")
        projection.executor_run = ExecutorRunInfo(
            adapter=(
                "candidate_pipeline"
                if mode == "candidate"
                else projection.executor_run.adapter
                if projection.executor_run is not None
                else task.executor.adapter
            ),
            run_id=run_id,
            issue_id=(
                projection.executor_run.issue_id
                if projection.executor_run is not None
                else None
            ),
        )
    state = record.get("state")
    if state == "completed":
        projection.phase = "done"
    elif state == "running":
        projection.phase = "executing"
    else:
        projection.phase = "blocked"


def _reduce_baseline(
    projection: _Projection,
    task: Task,
    record: Mapping[str, Any],
) -> None:
    baseline = Status.model_validate(record.get("status"))
    if baseline.task_id != task.id:
        raise ValueError(
            f"baseline task_id {baseline.task_id!r} does not match active task {task.id!r}"
        )
    projection.phase = baseline.phase
    projection.executor_run = (
        baseline.executor_run.model_copy(deep=True)
        if baseline.executor_run is not None
        else None
    )
    projection.gates = {
        gate_id: gate.model_copy(deep=True) for gate_id, gate in baseline.gates.items()
    }
    for gate_id in task.gates:
        projection.gates.setdefault(gate_id, GateState())
    projection.last_event_seq = baseline.last_event_seq
    projection.baseline_applied = True


def _nonempty_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_REQUIRED_EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "task_started": ("task_id",),
    "task_execution_started": ("task_id", "mode", "output_contract"),
    "gates_locked": ("task_id", "gates"),
    "executor_dispatched": ("adapter", "run_id"),
    "checkpoint": ("last_seq",),
    "gate_evaluated": ("gate", "stage", "state", "reason", "evidence_path"),
    "executor_completed": ("run_id", "state"),
    "executor_blocked": ("run_id", "state"),
    "task_execution_completed": ("task_id", "mode", "run_id", "state"),
    "task_execution_blocked": ("task_id", "mode", "run_id", "state"),
    "closeout_completed": ("task_id", "proof_refs", "finding_count"),
    "dashboard_paused": ("phase",),
    "dashboard_resumed": ("phase",),
    "dashboard_restarted": ("phase",),
    "dashboard_archived": ("phase",),
    "dashboard_restored": ("phase",),
    "dashboard_executor_dispatch_failed": ("task_id", "error"),
    "agent_option_dispatch_failed": ("task_id", "error"),
    "executor_dispatch_unreconciled": (
        "task_id",
        "adapter",
        "run_id",
        "triggered_by",
        "stage",
        "evidence_ref",
    ),
    "agent_option_selected": (
        "task_id",
        "option_id",
        "title",
        "summary",
        "source_run_id",
        "mapped_candidate_id",
    ),
    "execution_plan_created": ("task_id", "plan_id", "plan_ref", "subtask_ids"),
    "subtask_workspace_created": (
        "task_id",
        "subtask_id",
        "workspace_ref",
        "base_commit",
    ),
    "candidate_patch_created": (
        "task_id",
        "subtask_id",
        "candidate_id",
        "patch_ref",
        "patch_sha256",
        "provenance_source",
        "source_agent",
        "workspace_ref",
        "base_commit",
        "attestation_ref",
    ),
    "candidate_review_started": (
        "task_id",
        "candidate_id",
        "review_id",
        "packet_ref",
        "patch_ref",
    ),
    "candidate_review_completed": (
        "task_id",
        "candidate_id",
        "review_id",
        "report_ref",
        "open_blocking_count",
    ),
    "candidate_review_failed": ("task_id", "candidate_id", "review_id", "error"),
    "candidate_decision_recorded": (
        "task_id",
        "candidate_id",
        "decision",
        "decision_ref",
        "evidence_refs",
    ),
    "candidate_rejected": ("task_id", "candidate_id"),
    "candidate_superseded": ("task_id", "candidate_id"),
    "candidate_bundle_decided": (
        "task_id",
        "strategy",
        "candidate_ids",
        "decision_ref",
        "evidence_refs",
        "conflict_candidate_ids",
    ),
    "candidate_bundle_applied": ("task_id", "bundle_decision_id", "candidate_ids", "patch_refs"),
    "candidate_apply_blocked": ("task_id", "candidate_id", "evidence_ref"),
    "candidate_applied": ("task_id", "candidate_id", "patch_ref", "decision_ref"),
    "candidate_merge_preview_completed": (
        "task_id",
        "bundle_decision_id",
        "preview_ref",
        "state",
        "evidence_refs",
        "conflict_evidence_refs",
    ),
    "candidate_test_started": ("task_id", "candidate_id", "gate_id"),
    "candidate_test_completed": (
        "task_id",
        "candidate_id",
        "gate_id",
        "state",
        "evidence_ref",
    ),
    "review_started": ("review_id", "task_id", "packet_ref"),
    "finding_opened": (
        "review_id",
        "finding_id",
        "severity",
        "blocking",
        "title",
        "evidence_refs",
    ),
    "review_completed": (
        "review_id",
        "task_id",
        "report_ref",
        "open_blocking_count",
    ),
    "finding_resolved": (
        "finding_id",
        "review_id",
        "status",
        "evidence_refs",
        "rationale",
        "approved_by",
    ),
    "finding_accepted_risk": (
        "finding_id",
        "review_id",
        "status",
        "evidence_refs",
        "rationale",
        "approved_by",
    ),
    "repo_history_drift": ("stage", "checkpoint_repo_head", "current_repo_head"),
}


_EVENT_REGISTRY: dict[str, _EventSpec] = {
    "task_started": _EventSpec("initialization", _reduce_started, _validate_task_started),
    "task_execution_started": _EventSpec(
        "initialization", _reduce_started, _validate_task_execution_started
    ),
    "gates_locked": _EventSpec("initialization", _reduce_noop, _validate_gates_locked),
    "executor_dispatched": _EventSpec(
        "transition", _reduce_dispatched, _validate_dispatched
    ),
    "checkpoint": _EventSpec("fact", _reduce_checkpoint, _validate_checkpoint),
    "gate_evaluated": _EventSpec("fact", _reduce_gate, _validate_gate),
    "executor_completed": _EventSpec(
        "transition", _reduce_done, _validate_executor_completed
    ),
    "executor_blocked": _EventSpec(
        "transition", _reduce_blocked, _validate_executor_blocked
    ),
    "task_execution_completed": _EventSpec(
        "transition", _reduce_task_terminal, _validate_task_execution_terminal
    ),
    "task_execution_blocked": _EventSpec(
        "transition", _reduce_task_terminal, _validate_task_execution_terminal
    ),
    "closeout_completed": _EventSpec("transition", _reduce_done, _validate_closeout),
    "dashboard_paused": _EventSpec(
        "transition", _reduce_lifecycle_phase, _validate_lifecycle_phase
    ),
    "dashboard_resumed": _EventSpec(
        "transition", _reduce_lifecycle_phase, _validate_lifecycle_phase
    ),
    "dashboard_restarted": _EventSpec(
        "transition", _reduce_lifecycle_phase, _validate_lifecycle_phase
    ),
    "dashboard_archived": _EventSpec(
        "transition", _reduce_done, _validate_lifecycle_phase
    ),
    "dashboard_restored": _EventSpec(
        "transition", _reduce_restored, _validate_lifecycle_phase
    ),
    "dashboard_executor_dispatch_failed": _EventSpec(
        "transition", _reduce_dispatch_failed, _validate_noop
    ),
    "agent_option_dispatch_failed": _EventSpec(
        "transition", _reduce_dispatch_failed, _validate_noop
    ),
    "executor_dispatch_unreconciled": _EventSpec(
        "fact", _reduce_noop, _validate_unreconciled_dispatch
    ),
    "task_state_baselined": _EventSpec(
        "fact", _reduce_baseline, _validate_baseline, writable=False
    ),
}

for _event_name in {
    "agent_option_selected",
    "candidate_applied",
    "candidate_apply_blocked",
    "candidate_bundle_applied",
    "candidate_bundle_decided",
    "candidate_decision_recorded",
    "candidate_merge_preview_completed",
    "candidate_patch_created",
    "candidate_rejected",
    "candidate_review_completed",
    "candidate_review_failed",
    "candidate_review_started",
    "candidate_superseded",
    "candidate_test_completed",
    "candidate_test_started",
    "execution_plan_created",
    "finding_accepted_risk",
    "finding_opened",
    "finding_resolved",
    "repo_history_drift",
    "review_completed",
    "review_started",
    "subtask_workspace_created",
}:
    _EVENT_REGISTRY[_event_name] = _EventSpec("fact", _reduce_noop, _validate_noop)
