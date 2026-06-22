from __future__ import annotations

from agos.core.execution import ExecutionPlan, ExecutionSubtask, ExecutionWorker
from agos.core.execution_runtime import ExecutionRuntime
from agos.core.execution_worker import WorkerRun, WorkerRunStatus


class CompletingWorker:
    name = "fake"

    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, request):
        self.started.append(request.subtask_id)
        return WorkerRun(
            backend=self.name,
            run_id=f"worker-{request.subtask_id}",
            subtask_id=request.subtask_id,
            state="running",
        )

    def poll(self, run_id: str, *, subtask_id: str):
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state="completed",
        )

    def cancel(self, run_id: str):
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id="unknown",
            state="cancelled",
        )


def test_execution_runtime_starts_only_ready_subtasks(tmp_path):
    worker = CompletingWorker()
    runtime = ExecutionRuntime(state_dir=tmp_path, worker_adapters={"fake": worker})

    snapshot = runtime.tick(_plan(), run_id="execution-run-01")

    assert snapshot.running_subtasks == ("a",)
    assert worker.started == ["a"]


def test_execution_runtime_resume_polls_running_attempt_and_starts_dependent_subtask(tmp_path):
    worker = CompletingWorker()
    runtime = ExecutionRuntime(state_dir=tmp_path, worker_adapters={"fake": worker})
    runtime.tick(_plan(), run_id="execution-run-01")

    snapshot = runtime.tick(_plan(), run_id="execution-run-01")

    assert snapshot.completed_subtasks == ("a",)
    assert snapshot.running_subtasks == ("b",)
    assert worker.started == ["a", "b"]


def test_execution_runtime_cancel_stops_running_attempts(tmp_path):
    worker = CompletingWorker()
    runtime = ExecutionRuntime(state_dir=tmp_path, worker_adapters={"fake": worker})
    runtime.tick(_plan(), run_id="execution-run-01")

    snapshot = runtime.cancel(_plan(), run_id="execution-run-01")

    assert snapshot.cancelled_subtasks == ("a",)


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        id="plan-01",
        task_id="agos-01",
        max_parallel=2,
        subtasks=[
            ExecutionSubtask(
                id="a",
                title="A",
                write_scope=["a.py"],
                worker=ExecutionWorker(adapter="fake"),
            ),
            ExecutionSubtask(
                id="b",
                title="B",
                depends_on=["a"],
                write_scope=["b.py"],
                worker=ExecutionWorker(adapter="fake"),
            ),
        ],
    )
