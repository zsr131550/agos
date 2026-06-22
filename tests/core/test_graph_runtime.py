from __future__ import annotations

from agos.core.orchestration.graph_runtime import GraphRuntime, RuntimePolicy
from agos.core.orchestration.models import AgentJobHandle, NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.registry import OrchestrationRegistry


class RecordingBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.started: list[str] = []

    def start(self, run, node):
        self.started.append(node.id)
        return AgentJobHandle(
            backend=self.name,
            job_id=f"{run.run_id}:{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )


class FlakyBackend:
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def start(self, run, node):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary failure")
        return AgentJobHandle(
            backend=self.name,
            job_id=f"{run.run_id}:{node.id}:{self.calls}",
            node_id=node.id,
            run_id=run.run_id,
        )


def test_graph_runtime_respects_max_parallel_and_dependencies(tmp_path):
    registry = OrchestrationRegistry()
    worker = RecordingBackend("worker")
    reviewer = RecordingBackend("reviewer")
    arbiter = RecordingBackend("arbiter")
    registry.register_worker(worker)
    registry.register_reviewer(reviewer)
    registry.register_arbiter(arbiter)

    runtime = GraphRuntime(
        registry=registry,
        state_dir=tmp_path,
        policy=RuntimePolicy(max_parallel=2),
    )
    snapshot = runtime.tick(_spec())

    assert snapshot.running_nodes == ("worker-a", "worker-b")
    assert worker.started == ["worker-a", "worker-b"]
    assert reviewer.started == []


def test_graph_runtime_retries_failed_start(tmp_path):
    registry = OrchestrationRegistry()
    backend = FlakyBackend()
    registry.register_worker(backend)
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[NodeSpec(id="worker-a", kind="worker", backend="flaky")],
    )
    runtime = GraphRuntime(
        registry=registry,
        state_dir=tmp_path,
        policy=RuntimePolicy(max_retries=1),
    )

    failed = runtime.tick(spec)
    recovered = runtime.tick(spec)

    assert failed.failed_nodes == ("worker-a",)
    assert recovered.running_nodes == ("worker-a",)


def test_graph_runtime_cancel_marks_running_nodes(tmp_path):
    registry = OrchestrationRegistry()
    worker = RecordingBackend("worker")
    registry.register_worker(worker)
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[NodeSpec(id="worker-a", kind="worker", backend="worker")],
    )
    runtime = GraphRuntime(registry=registry, state_dir=tmp_path)

    runtime.tick(spec)
    snapshot = runtime.cancel(spec)

    assert snapshot.cancelled_nodes == ("worker-a",)


def _spec() -> OrchestrationRunSpec:
    return OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[
            NodeSpec(id="worker-a", kind="worker", backend="worker"),
            NodeSpec(id="worker-b", kind="worker", backend="worker"),
            NodeSpec(
                id="review",
                kind="reviewer",
                backend="reviewer",
                depends_on=["worker-a", "worker-b"],
            ),
            NodeSpec(id="merge", kind="arbiter", backend="arbiter", depends_on=["review"]),
        ],
        limits={"max_parallel": 2},
    )
