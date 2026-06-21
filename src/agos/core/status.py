"""status.json: a derived cache of the ledger, never an independent truth source."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import AgosPaths


class ExecutorRunInfo(BaseModel):
    """Pydantic mirror of ExecutorRun for status.json serialization."""

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


def load_status(paths: AgosPaths) -> Status | None:
    """Read status.json, or return None when no status exists."""

    if not paths.status_json.exists():
        return None
    return Status.model_validate_json(paths.status_json.read_text(encoding="utf-8"))


def save_status(status: Status, paths: AgosPaths) -> None:
    """Persist status.json under the current task directory."""

    paths.status_json.parent.mkdir(parents=True, exist_ok=True)
    paths.status_json.write_text(status.model_dump_json(indent=2), encoding="utf-8")


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
