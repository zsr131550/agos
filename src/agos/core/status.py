"""Task status models."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from agos.core.adapter import ExecutorRun
from agos.core.task import Task


class GateState(BaseModel):
    state: str = "unknown"
    last_evaluated: str | None = None


class ExecutorRunState(BaseModel):
    adapter: str
    run_id: str
    issue_id: str | None = None


class TaskStatus(BaseModel):
    task_id: str
    phase: str
    executor_run: ExecutorRunState
    gates: dict[str, GateState] = Field(default_factory=dict)
    ledger_head_hash: str

    @classmethod
    def for_started_task(
        cls, *, task: Task, run: ExecutorRun, ledger_head_hash: str
    ) -> "TaskStatus":
        return cls(
            task_id=task.id,
            phase="executing",
            executor_run=ExecutorRunState(
                adapter=run.adapter,
                run_id=run.run_id,
                issue_id=run.issue_id,
            ),
            gates={gate.id: GateState() for gate in task.gates},
            ledger_head_hash=ledger_head_hash,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.model_dump(mode="python"), indent=2) + "\n",
            encoding="utf-8",
        )

