from __future__ import annotations

from agos.core.execution_worker import (
    WorkerRun,
    WorkerRunStatus,
    WorkerStartRequest,
)


def test_worker_run_models_round_trip_lifecycle_metadata():
    request = WorkerStartRequest(
        run_id="execution-run-01",
        subtask_id="subtask-01",
        prompt="Implement the scoped change.",
        workspace_path="C:/work/subtask-01",
        metadata={"plan_ref": "execution/plan.json"},
    )
    run = WorkerRun(
        backend="codex",
        run_id="worker-run-01",
        subtask_id=request.subtask_id,
        state="running",
        metadata={"pid": "123"},
    )
    status = WorkerRunStatus(
        backend=run.backend,
        run_id=run.run_id,
        subtask_id=run.subtask_id,
        state="completed",
        detail="done",
        output_refs=["workers/worker-run-01.log"],
    )

    assert request.model_dump()["workspace_path"] == "C:/work/subtask-01"
    assert run.model_dump()["metadata"] == {"pid": "123"}
    assert status.model_dump()["output_refs"] == ["workers/worker-run-01.log"]


def test_worker_run_status_identifies_terminal_states():
    assert WorkerRunStatus(
        backend="codex",
        run_id="run-01",
        subtask_id="subtask-01",
        state="completed",
    ).is_terminal
    assert not WorkerRunStatus(
        backend="codex",
        run_id="run-01",
        subtask_id="subtask-01",
        state="running",
    ).is_terminal
