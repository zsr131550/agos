from __future__ import annotations

from agos.core.execution_worker import (
    WorkerReadinessError,
    WorkerHealth,
    WorkerHealthCheck,
    WorkerRun,
    WorkerRunStatus,
    WorkerStartRequest,
    ensure_worker_ready,
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


def test_worker_health_models_report_aggregate_state():
    health = WorkerHealth(
        name="codex",
        adapter="codex_cli",
        checks=[
            WorkerHealthCheck(name="command_available", state="passed", detail="codex"),
            WorkerHealthCheck(name="artifact_contract", state="passed", detail=".agos-worker/*.json"),
        ],
        metadata={"timeout_seconds": "30"},
    )
    unhealthy = WorkerHealth(
        name="multica",
        adapter="multica",
        checks=[WorkerHealthCheck(name="daemon_status", state="failed", detail="daemon down")],
    )

    assert health.state == "healthy"
    assert health.is_healthy
    assert unhealthy.state == "unhealthy"
    assert not unhealthy.is_healthy


def test_ensure_worker_ready_allows_passed_and_warning_checks():
    class _Worker:
        name = "codex-prod"

        def health(self):
            return WorkerHealth(
                name=self.name,
                adapter="codex_cli",
                checks=[
                    WorkerHealthCheck(name="command_available", state="passed", detail="codex"),
                    WorkerHealthCheck(name="artifact_contract", state="warning", detail="no globs"),
                ],
            )

    health = ensure_worker_ready(_Worker())

    assert health.name == "codex-prod"


def test_ensure_worker_ready_raises_clear_error_for_failed_checks():
    class _Worker:
        name = "multica-prod"

        def health(self):
            return WorkerHealth(
                name=self.name,
                adapter="multica",
                checks=[
                    WorkerHealthCheck(name="daemon_status", state="failed", detail="daemon down"),
                    WorkerHealthCheck(name="workspace_list", state="passed", detail="ok"),
                ],
            )

    try:
        ensure_worker_ready(_Worker())
    except WorkerReadinessError as exc:
        message = str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected readiness failure")

    assert "multica-prod" in message
    assert "daemon_status" in message
    assert "daemon down" in message
