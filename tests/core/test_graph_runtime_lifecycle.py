from __future__ import annotations

from agos.core.orchestration.graph_runtime import GraphRuntime, RuntimePolicy
from agos.core.orchestration.models import AgentJobHandle, NodeRunStatus, NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.registry import OrchestrationRegistry


class PollingBackend:
    name = "backend"

    def __init__(self) -> None:
        self.started: list[str] = []
        self.polls: dict[str, int] = {}
        self.cancelled: list[str] = []

    def start(self, run, node):
        self.started.append(node.id)
        return AgentJobHandle(
            backend=self.name,
            job_id=f"job-{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )

    def poll(self, handle):
        count = self.polls.get(handle.node_id, 0) + 1
        self.polls[handle.node_id] = count
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state="completed",
            output_refs={handle.node_id: f"evidence/{handle.node_id}.json"},
        )

    def cancel(self, handle):
        self.cancelled.append(handle.node_id)
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state="cancelled",
        )

    def collect(self, handle):
        return {"output_refs": {handle.node_id: f"evidence/{handle.node_id}.json"}}


def test_graph_runtime_polls_and_unblocks_dependent_nodes(tmp_path):
    backend = PollingBackend()
    registry = OrchestrationRegistry()
    registry.register_worker(backend)
    registry.register_reviewer(backend)
    registry.register_arbiter(backend)
    runtime = GraphRuntime(registry=registry, state_dir=tmp_path, policy=RuntimePolicy(max_parallel=2))

    first = runtime.tick(_spec())
    second = runtime.tick(_spec())
    third = runtime.tick(_spec())

    assert first.running_nodes == ("worker-a",)
    assert second.completed_nodes == ("worker-a",)
    assert second.running_nodes == ("review-a",)
    assert third.completed_nodes == ("worker-a", "review-a")
    assert third.running_nodes == ("arbiter",)
    assert backend.started == ["worker-a", "review-a", "arbiter"]


def test_graph_runtime_cancel_delegates_to_backend(tmp_path):
    backend = PollingBackend()
    registry = OrchestrationRegistry()
    registry.register_worker(backend)
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=(NodeSpec(id="worker-a", kind="worker", backend="backend"),),
    )
    runtime = GraphRuntime(registry=registry, state_dir=tmp_path)

    runtime.tick(spec)
    snapshot = runtime.cancel(spec)

    assert snapshot.cancelled_nodes == ("worker-a",)
    assert backend.cancelled == ["worker-a"]


def _spec() -> OrchestrationRunSpec:
    return OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=(
            NodeSpec(id="worker-a", kind="worker", backend="backend"),
            NodeSpec(id="review-a", kind="reviewer", backend="backend", depends_on=("worker-a",)),
            NodeSpec(id="arbiter", kind="arbiter", backend="backend", depends_on=("review-a",)),
        ),
    )
