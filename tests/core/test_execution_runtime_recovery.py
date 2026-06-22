from __future__ import annotations

import json

from agos.core.execution import ExecutionPlan, ExecutionSubtask, ExecutionWorker
from agos.core.execution_runtime import ExecutionRuntime
from agos.core.execution_worker import WorkerRun, WorkerRunStatus


class FlakyWorker:
    name = "flaky"

    def __init__(self) -> None:
        self.starts = 0
        self.polls = 0

    def start(self, request):
        self.starts += 1
        return WorkerRun(
            backend=self.name,
            run_id=f"{request.run_id}:worker:{self.starts}",
            subtask_id=request.subtask_id,
            state="running",
        )

    def poll(self, run_id: str, *, subtask_id: str):
        self.polls += 1
        state = "failed" if self.polls == 1 else "completed"
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state=state,
            detail=state,
        )

    def cancel(self, run_id: str):
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id="subtask-a",
            state="cancelled",
        )


def test_runtime_retries_failed_attempt_once(tmp_path):
    worker = FlakyWorker()
    runtime = ExecutionRuntime(
        state_dir=tmp_path,
        worker_adapters={"flaky": worker},
        max_retries=1,
        retry_backoff_seconds=0,
    )

    first = runtime.tick(_plan(), run_id="run-01")
    second = runtime.tick(_plan(), run_id="run-01")
    third = runtime.tick(_plan(), run_id="run-01")

    assert first.running_subtasks == ("subtask-a",)
    assert second.failed_subtasks == ("subtask-a",)
    assert third.running_subtasks == ("subtask-a",)
    assert worker.starts == 2


def test_runtime_status_persists_snapshot_without_duplicate_start(tmp_path):
    worker = FlakyWorker()
    runtime = ExecutionRuntime(
        state_dir=tmp_path,
        worker_adapters={"flaky": worker},
        max_retries=1,
    )

    runtime.tick(_plan(), run_id="run-01")
    snapshot = runtime.status(_plan(), run_id="run-01")

    status_path = tmp_path / "run-01" / "status.json"
    assert worker.starts == 1
    assert snapshot.running_subtasks == ("subtask-a",)
    assert json.loads(status_path.read_text(encoding="utf-8"))["running_subtasks"] == ["subtask-a"]


def test_runtime_retry_backoff_delays_restart(tmp_path):
    worker = FlakyWorker()
    runtime = ExecutionRuntime(
        state_dir=tmp_path,
        worker_adapters={"flaky": worker},
        max_retries=1,
        retry_backoff_seconds=60,
    )

    runtime.tick(_plan(), run_id="run-01")
    failed = runtime.tick(_plan(), run_id="run-01")
    delayed = runtime.tick(_plan(), run_id="run-01")

    attempt = json.loads((tmp_path / "run-01" / "attempts" / "subtask-a.json").read_text())
    assert failed.failed_subtasks == ("subtask-a",)
    assert delayed.failed_subtasks == ("subtask-a",)
    assert attempt["retry_after"] is not None
    assert worker.starts == 1


def test_runtime_times_out_running_attempt(tmp_path):
    worker = FlakyWorker()
    runtime = ExecutionRuntime(
        state_dir=tmp_path,
        worker_adapters={"flaky": worker},
        worker_timeout_seconds=1,
    )

    runtime.tick(_plan(), run_id="run-01")
    attempt_path = tmp_path / "run-01" / "attempts" / "subtask-a.json"
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    attempt["started_at"] = "2000-01-01T00:00:00Z"
    attempt_path.write_text(json.dumps(attempt), encoding="utf-8")

    timed_out = runtime.tick(_plan(), run_id="run-01")
    updated = json.loads(attempt_path.read_text(encoding="utf-8"))

    assert timed_out.failed_subtasks == ("subtask-a",)
    assert updated["terminal_reason"] == "worker timed out after 1 seconds"
    assert worker.polls == 0

def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        id="plan-01",
        task_id="agos-01",
        max_parallel=1,
        subtasks=[
            ExecutionSubtask(
                id="subtask-a",
                title="A",
                write_scope=["README.md"],
                worker=ExecutionWorker(adapter="flaky"),
            )
        ],
    )
