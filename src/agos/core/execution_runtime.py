"""Runtime scheduler for execution-plan worker attempts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from agos.core.execution import ExecutionPlan, ExecutionSubtask, utc_now_iso
from agos.core.execution_worker import (
    ExecutionWorkerAdapter,
    WorkerRunState,
    WorkerStartRequest,
)


class WorkerAttempt(BaseModel):
    subtask_id: str
    adapter: str
    worker_run_id: str
    state: WorkerRunState
    attempts: int = 1
    detail: str | None = None
    output_refs: list[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    retry_after: str | None = None
    terminal_reason: str | None = None


@dataclass(frozen=True)
class ExecutionRuntimeSnapshot:
    run_id: str
    running_subtasks: tuple[str, ...] = ()
    completed_subtasks: tuple[str, ...] = ()
    failed_subtasks: tuple[str, ...] = ()
    cancelled_subtasks: tuple[str, ...] = ()


class ExecutionRuntime:
    """Run execution subtasks through configured worker adapter lifecycles."""

    def __init__(
        self,
        *,
        state_dir: Path,
        worker_adapters: dict[str, ExecutionWorkerAdapter],
        workspace_paths: dict[str, str] | None = None,
        max_retries: int = 0,
        retry_backoff_seconds: int = 0,
        worker_timeout_seconds: int | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.worker_adapters = dict(worker_adapters)
        self.workspace_paths = dict(workspace_paths or {})
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.worker_timeout_seconds = worker_timeout_seconds

    def tick(self, plan: ExecutionPlan, *, run_id: str) -> ExecutionRuntimeSnapshot:
        attempts = self._load_attempts(plan, run_id)
        retryable_failed = {
            subtask_id
            for subtask_id, attempt in attempts.items()
            if _can_retry(attempt, self.max_retries)
        }
        attempts = self._poll_running(plan, run_id, attempts)
        capacity = max(0, plan.max_parallel - _count_state(attempts, "running"))

        for subtask in _ready_subtasks(plan, attempts, retryable_failed=retryable_failed)[:capacity]:
            attempt = self._start_subtask(plan, run_id, subtask, attempts.get(subtask.id))
            attempts[subtask.id] = attempt
            self._write_attempt(run_id, attempt)

        snapshot = _execution_snapshot(run_id, plan, attempts)
        self._write_run_status(snapshot)
        return snapshot

    def status(self, plan: ExecutionPlan, *, run_id: str) -> ExecutionRuntimeSnapshot:
        snapshot = _execution_snapshot(run_id, plan, self._load_attempts(plan, run_id))
        self._write_run_status(snapshot)
        return snapshot

    def cancel(self, plan: ExecutionPlan, *, run_id: str) -> ExecutionRuntimeSnapshot:
        attempts = self._load_attempts(plan, run_id)
        for subtask in plan.subtasks:
            attempt = attempts.get(subtask.id)
            if attempt is None or attempt.state != "running":
                continue
            adapter = self.worker_adapters[attempt.adapter]
            status = adapter.cancel(attempt.worker_run_id)
            updated = attempt.model_copy(
                update={
                    "state": status.state,
                    "detail": status.detail,
                    "output_refs": status.output_refs,
                    "terminal_reason": status.detail if status.is_terminal else None,
                    "updated_at": utc_now_iso(),
                }
            )
            attempts[subtask.id] = updated
            self._write_attempt(run_id, updated)
        snapshot = _execution_snapshot(run_id, plan, attempts)
        self._write_run_status(snapshot)
        return snapshot

    def _poll_running(
        self,
        plan: ExecutionPlan,
        run_id: str,
        attempts: dict[str, WorkerAttempt],
    ) -> dict[str, WorkerAttempt]:
        for subtask in plan.subtasks:
            attempt = attempts.get(subtask.id)
            if attempt is None or attempt.state != "running":
                continue
            adapter = self.worker_adapters[attempt.adapter]
            status = adapter.poll(attempt.worker_run_id, subtask_id=subtask.id)
            updated = attempt.model_copy(
                update={
                    "state": status.state,
                    "detail": status.detail,
                    "output_refs": status.output_refs,
                    "terminal_reason": status.detail if status.state == "failed" else None,
                    "updated_at": utc_now_iso(),
                }
            )
            attempts[subtask.id] = updated
            self._write_attempt(run_id, updated)
        return attempts

    def _start_subtask(
        self,
        plan: ExecutionPlan,
        run_id: str,
        subtask: ExecutionSubtask,
        previous: WorkerAttempt | None,
    ) -> WorkerAttempt:
        adapter = self.worker_adapters[subtask.worker.adapter]
        attempts = (previous.attempts if previous else 0) + 1
        prompt = "\n\n".join(part for part in [subtask.title, subtask.intent] if part)
        try:
            worker_run = adapter.start(
                WorkerStartRequest(
                    run_id=f"{run_id}:{subtask.id}:{attempts}",
                    subtask_id=subtask.id,
                    prompt=prompt,
                    workspace_path=self.workspace_paths.get(subtask.id, subtask.workspace_ref or ""),
                    metadata={"plan_id": plan.id, "execution_run_id": run_id},
                )
            )
        except Exception as exc:
            state: WorkerRunState = "failed"
            return WorkerAttempt(
                subtask_id=subtask.id,
                adapter=subtask.worker.adapter,
                worker_run_id=f"{run_id}:{subtask.id}:{attempts}",
                state=state,
                attempts=attempts,
                detail=str(exc),
                terminal_reason=str(exc),
            )
        return WorkerAttempt(
            subtask_id=subtask.id,
            adapter=subtask.worker.adapter,
            worker_run_id=worker_run.run_id,
            state=worker_run.state,
            attempts=attempts,
        )

    def _load_attempts(self, plan: ExecutionPlan, run_id: str) -> dict[str, WorkerAttempt]:
        attempts: dict[str, WorkerAttempt] = {}
        for subtask in plan.subtasks:
            path = self._attempt_path(run_id, subtask.id)
            if path.exists():
                attempts[subtask.id] = WorkerAttempt.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
        return attempts

    def _write_attempt(self, run_id: str, attempt: WorkerAttempt) -> None:
        path = self._attempt_path(run_id, attempt.subtask_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(attempt.model_dump_json(indent=2), encoding="utf-8")

    def _write_run_status(self, snapshot: ExecutionRuntimeSnapshot) -> None:
        path = self.state_dir / snapshot.run_id / "status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "run_id": snapshot.run_id,
                    "running_subtasks": list(snapshot.running_subtasks),
                    "completed_subtasks": list(snapshot.completed_subtasks),
                    "failed_subtasks": list(snapshot.failed_subtasks),
                    "cancelled_subtasks": list(snapshot.cancelled_subtasks),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _attempt_path(self, run_id: str, subtask_id: str) -> Path:
        return self.state_dir / run_id / "attempts" / f"{subtask_id}.json"


def _ready_subtasks(
    plan: ExecutionPlan,
    attempts: dict[str, WorkerAttempt],
    *,
    retryable_failed: set[str] | None = None,
) -> list[ExecutionSubtask]:
    retryable_failed = retryable_failed or set()
    ready: list[ExecutionSubtask] = []
    for subtask in plan.subtasks:
        attempt = attempts.get(subtask.id)
        if attempt is not None:
            if attempt.state == "failed" and subtask.id in retryable_failed:
                pass
            else:
                continue
        if all(attempts.get(dep) is not None and attempts[dep].state == "completed" for dep in subtask.depends_on):
            ready.append(subtask)
    return ready


def _execution_snapshot(
    run_id: str,
    plan: ExecutionPlan,
    attempts: dict[str, WorkerAttempt],
) -> ExecutionRuntimeSnapshot:
    return ExecutionRuntimeSnapshot(
        run_id=run_id,
        running_subtasks=_subtasks_in_state(plan, attempts, "running"),
        completed_subtasks=_subtasks_in_state(plan, attempts, "completed"),
        failed_subtasks=_subtasks_in_state(plan, attempts, "failed"),
        cancelled_subtasks=_subtasks_in_state(plan, attempts, "cancelled"),
    )


def _subtasks_in_state(
    plan: ExecutionPlan,
    attempts: dict[str, WorkerAttempt],
    state: str,
) -> tuple[str, ...]:
    return tuple(
        subtask.id
        for subtask in plan.subtasks
        if attempts.get(subtask.id) is not None and attempts[subtask.id].state == state
    )


def _count_state(attempts: dict[str, WorkerAttempt], state: str) -> int:
    return sum(1 for attempt in attempts.values() if attempt.state == state)


def _can_retry(attempt: WorkerAttempt, max_retries: int) -> bool:
    return attempt.state == "failed" and attempt.attempts <= max_retries
