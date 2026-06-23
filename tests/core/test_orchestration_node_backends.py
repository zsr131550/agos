from __future__ import annotations

from agos.core.execution_worker import WorkerHealth, WorkerHealthCheck, WorkerRun, WorkerRunStatus
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.node_backends import ArbiterNodeBackend, WorkerNodeBackend


class FakeExecutionWorker:
    name = "fake-worker"

    def health(self):
        return WorkerHealth(
            name=self.name,
            adapter="fake",
            checks=[WorkerHealthCheck(name="fake_worker", state="passed", detail="ready")],
        )

    def start(self, request):
        return WorkerRun(
            backend=self.name,
            run_id=request.run_id,
            subtask_id=request.subtask_id,
            state="running",
        )

    def poll(self, run_id: str, *, subtask_id: str):
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state="completed",
            output_refs=["evidence/worker.json"],
        )

    def cancel(self, run_id: str):
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id="subtask-a",
            state="cancelled",
        )


def test_worker_node_backend_maps_worker_lifecycle():
    backend = WorkerNodeBackend(FakeExecutionWorker())
    spec = OrchestrationRunSpec(
        run_id="graph-run",
        task_id="agos-01",
        nodes=(
            NodeSpec(
                id="worker-a",
                kind="worker",
                backend="fake-worker",
                inputs={"prompt": "Do the work"},
                metadata={"workspace_path": "C:/w", "subtask_id": "subtask-a"},
            ),
        ),
    )

    handle = backend.start(spec, spec.nodes[0])
    status = backend.poll(handle)

    assert handle.job_id == "graph-run:worker-a"
    assert status.state == "completed"
    assert status.output_refs == {"worker-a": "evidence/worker.json"}


def test_worker_node_backend_requires_ready_worker_before_start():
    class UnreadyWorker(FakeExecutionWorker):
        def health(self):
            return WorkerHealth(
                name=self.name,
                adapter="fake",
                checks=[WorkerHealthCheck(name="fake_worker", state="failed", detail="offline")],
            )

        def start(self, request):  # pragma: no cover - should not be called
            raise AssertionError("start should not be reached")

    backend = WorkerNodeBackend(UnreadyWorker())
    spec = OrchestrationRunSpec(
        run_id="graph-run",
        task_id="agos-01",
        nodes=(
            NodeSpec(
                id="worker-a",
                kind="worker",
                backend="fake-worker",
                inputs={"prompt": "Do the work"},
                metadata={"workspace_path": "C:/w", "subtask_id": "subtask-a"},
            ),
        ),
    )

    try:
        backend.start(spec, spec.nodes[0])
    except Exception as exc:
        message = str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected readiness failure")

    assert "offline" in message
    assert "fake-worker" in message


def test_arbiter_node_backend_completes_deterministically():
    backend = ArbiterNodeBackend(name="merge_arbiter")
    spec = OrchestrationRunSpec(
        run_id="graph-run",
        task_id="agos-01",
        nodes=(NodeSpec(id="arbiter", kind="arbiter", backend="merge_arbiter"),),
    )

    handle = backend.start(spec, spec.nodes[0])
    status = backend.poll(handle)

    assert status.state == "completed"
    assert backend.collect(handle)["node_id"] == "arbiter"
