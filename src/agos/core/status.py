"""`status.json`: a derived cache of the ledger, never an independent truth source."""
from __future__ import annotations

import os
import tempfile
from typing import Any, Literal

from pydantic import BaseModel

from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import AgosPaths
from agos.core.task import Task, load_task


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
    """Load the cache and repair it from verified ledger events when stale."""

    cached, cache_error = _read_cached_status(paths)
    if not paths.task_yaml.is_file() or not paths.ledger.is_file():
        if cache_error is not None:
            raise cache_error
        return cached

    ledger = Ledger(paths.ledger)
    records = ledger.read_verified()
    if not records:
        if cache_error is not None:
            raise cache_error
        return cached

    task = load_task(paths.task_yaml)
    ledger_head = str(records[-1]["hash"])
    if (
        cached is not None
        and cached.task_id == task.id
        and cached.ledger_head_hash == ledger_head
    ):
        return cached

    recovered = replay_status(task, records, cached=cached)
    save_status(recovered, paths)
    return recovered


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

    phase: Literal["executing", "gated", "done", "blocked"] = (
        cached.phase if cached is not None else "executing"
    )
    executor_run = cached.executor_run if cached is not None else None
    last_event_seq = cached.last_event_seq if cached is not None else None
    gate_states = dict(cached.gates) if cached is not None else {}
    for gate_id in task.gates:
        gate_states.setdefault(gate_id, GateState())

    for record in records:
        event_type = str(record.get("type", ""))
        explicit_phase = record.get("phase")
        if explicit_phase in {"executing", "gated", "done", "blocked"}:
            phase = explicit_phase

        if event_type in {"task_started", "task_execution_started"}:
            phase = "executing"
        elif event_type == "executor_dispatched":
            run_id = _nonempty_text(record.get("run_id"))
            if run_id is not None:
                executor_run = ExecutorRunInfo(
                    adapter=_nonempty_text(record.get("adapter")) or task.executor.adapter,
                    run_id=run_id,
                    issue_id=_nonempty_text(record.get("issue_id")),
                )
            phase = "executing"
            last_event_seq = None
        elif event_type == "dashboard_restored" and executor_run is None:
            executor_run = ExecutorRunInfo(
                adapter=task.executor.adapter,
                run_id=f"restored-{task.id}",
            )
        elif event_type == "checkpoint":
            checkpoint_seq = record.get("last_seq")
            if isinstance(checkpoint_seq, int):
                last_event_seq = checkpoint_seq
        elif event_type == "gate_evaluated":
            gate_id = _nonempty_text(record.get("gate"))
            gate_state = record.get("state")
            if gate_id is not None and gate_state in {"pass", "block"}:
                gate_states[gate_id] = GateState(
                    state=gate_state,
                    last_evaluated=_nonempty_text(record.get("ts")),
                )

        if event_type == "executor_completed":
            phase = "done"
        elif event_type in {
            "executor_blocked",
            "dashboard_executor_dispatch_failed",
            "agent_option_dispatch_failed",
        }:
            phase = "blocked"
        elif event_type in {"closeout_completed", "dashboard_archived"}:
            phase = "done"
        elif event_type in {"task_execution_completed", "task_execution_blocked"}:
            run_id = _nonempty_text(record.get("run_id"))
            mode = record.get("mode")
            if run_id is not None:
                executor_run = ExecutorRunInfo(
                    adapter=(
                        "candidate_pipeline"
                        if mode == "candidate"
                        else executor_run.adapter
                        if executor_run is not None
                        else task.executor.adapter
                    ),
                    run_id=run_id,
                    issue_id=executor_run.issue_id if executor_run is not None else None,
                )
            state = record.get("state")
            if state == "completed":
                phase = "done"
            elif state == "running":
                phase = "executing"
            else:
                phase = "blocked"

    ledger_head = str(records[-1]["hash"]) if records else ""
    return Status(
        task_id=task.id,
        phase=phase,
        executor_run=executor_run,
        gates=gate_states,
        ledger_head_hash=ledger_head,
        last_event_seq=last_event_seq,
    )


def _nonempty_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
