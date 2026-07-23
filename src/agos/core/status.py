"""`status.json`: a derived cache of the ledger, never an independent truth source."""
from __future__ import annotations

import os
import tempfile
from typing import Any, Literal

from pydantic import BaseModel

from agos.core.adapter import ExecutorRun
from agos.core.file_lock import exclusive_file_lock
from agos.core.ledger import Ledger
from agos.core.repo import AgosPaths
from agos.core.task import Task


class ExecutorRunInfo(BaseModel):
    """Pydantic mirror of `ExecutorRun` for `status.json` serialization."""

    adapter: str
    run_id: str
    issue_id: str | None = None


class GateState(BaseModel):
    """Derived state for one configured gate."""

    state: Literal["unknown", "pass", "block"] = "unknown"
    last_evaluated: str | None = None


class Status(BaseModel):
    """Current derived view of the active task."""

    task_id: str
    phase: Literal["executing", "gated", "done", "blocked"] = "executing"
    executor_run: ExecutorRunInfo | None = None
    gates: dict[str, GateState]
    ledger_head_hash: str
    last_event_seq: int | None = None

    @classmethod
    def for_started_task(
        cls,
        *,
        task: Task,
        run: ExecutorRun,
        ledger_head_hash: str,
    ) -> "Status":
        return cls(
            task_id=task.id,
            phase="executing",
            executor_run=ExecutorRunInfo(
                adapter=run.adapter,
                run_id=run.run_id,
                issue_id=run.issue_id,
            ),
            gates={gate_id: GateState() for gate_id in task.gates},
            ledger_head_hash=ledger_head_hash,
            last_event_seq=None,
        )


TaskStatus = Status


def load_status(paths: AgosPaths) -> Status | None:
    """Compatibility adapter for the authoritative TaskState snapshot."""

    if not paths.task_yaml.is_file() or not paths.ledger.is_file():
        cached, cache_error = _read_cached_status(paths)
        if cache_error is not None:
            raise cache_error
        return cached
    if paths.ledger.stat().st_size == 0:
        cached, cache_error = _read_cached_status(paths)
        if cache_error is not None:
            raise cache_error
        return cached

    from agos.core.task_state import TaskState

    snapshot = TaskState(paths).current()
    return snapshot.status if snapshot is not None else None


def load_status_from_verified_records(
    paths: AgosPaths,
    task: Task,
    records: list[dict[str, Any]],
) -> Status | None:
    """Load or repair status using one caller-verified ledger snapshot."""

    cached, cache_error = _read_cached_status(paths)
    if not records:
        if cache_error is not None:
            raise cache_error
        return cached
    return repair_status_from_verified_records(paths, task, records, cached=cached)


def repair_status_from_verified_records(
    paths: AgosPaths,
    task: Task,
    records: list[dict[str, Any]],
    *,
    cached: Status | None,
) -> Status:
    """Reconcile a cache against a non-empty, already verified ledger snapshot."""

    if not records:
        raise ValueError("cannot repair status from an empty ledger")
    recovered = replay_status(task, records)
    # The caller's read can be stale by the time this audit path writes the
    # derived cache, so compare both cache and ledger again under one lock.
    del cached
    with exclusive_file_lock(paths.ledger):
        current_records = Ledger(paths.ledger)._records_unlocked()
        Ledger._verify_records(current_records)
        if current_records != records:
            return recovered
        current_cached, _cache_error = _read_cached_status(paths)
        if status_cache_requires_baseline(task, current_records, current_cached, recovered):
            return recovered
        if current_cached != recovered:
            save_status(recovered, paths)
    return recovered


def status_cache_requires_baseline(
    task: Task,
    records: list[dict[str, Any]],
    cached: Status | None,
    recovered: Status,
) -> bool:
    """Whether compatible legacy cache facts must survive until a write journals them."""

    if cached is None or cached.task_id != task.id:
        return False
    if any(record.get("type") == "task_state_baselined" for record in records):
        return False
    head_hash = str(records[-1]["hash"]) if records else ""
    if cached.ledger_head_hash != head_hash:
        return False
    return _status_facts(cached) != _status_facts(recovered)


def _status_facts(status: Status) -> dict[str, Any]:
    payload = status.model_dump(mode="json")
    payload.pop("ledger_head_hash", None)
    return payload


def _read_cached_status(paths: AgosPaths) -> tuple[Status | None, ValueError | None]:
    try:
        return read_status_cache(paths), None
    except ValueError as exc:
        return None, exc


def read_status_cache(paths: AgosPaths) -> Status | None:
    """Read `status.json` without ledger verification or automatic repair."""

    if not paths.status_json.exists():
        return None
    payload = paths.status_json.read_text(encoding="utf-8")
    return Status.model_validate_json(payload)


def save_status(status: Status, paths: AgosPaths) -> None:
    """Persist `status.json` under the current task directory."""

    paths.status_json.parent.mkdir(parents=True, exist_ok=True)
    payload = status.model_dump_json(indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{paths.status_json.name}.",
        suffix=".tmp",
        dir=paths.status_json.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, paths.status_json)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def derive_status(
    paths: AgosPaths,
    task_id: str,
    gates: list[str],
    ledger: Ledger,
    executor_run: ExecutorRun | None,
    last_event_seq: int | None,
    gate_states: dict[str, GateState] | None,
) -> Status:
    """Rebuild status from ledger state plus the current gate results."""

    del paths

    states = {gate_id: (gate_states or {}).get(gate_id, GateState()) for gate_id in gates}
    run_info = None
    if executor_run is not None:
        run_info = ExecutorRunInfo(
            adapter=executor_run.adapter,
            run_id=executor_run.run_id,
            issue_id=executor_run.issue_id,
        )
    return Status(
        task_id=task_id,
        phase="executing",
        executor_run=run_info,
        gates=states,
        ledger_head_hash=ledger.head_hash(),
        last_event_seq=last_event_seq,
    )


def replay_status(
    task: Task,
    records: list[dict[str, Any]],
    *,
    cached: Status | None = None,
) -> Status:
    """Rebuild the current task view from ordered, verified ledger records."""

    del cached
    from agos.core.task_state import _project_status

    status, _warnings = _project_status(task, records)
    return status
