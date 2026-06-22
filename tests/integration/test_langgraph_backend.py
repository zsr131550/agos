from __future__ import annotations

import pytest

from agos.backends.langgraph_backend import LangGraphBackend
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


pytestmark = pytest.mark.integration


def test_real_langgraph_backend_executes_simple_graph():
    if not LangGraphBackend.is_available():
        pytest.skip("langgraph is not installed")

    backend = LangGraphBackend()
    spec = OrchestrationRunSpec(
        run_id="langgraph-run-01",
        task_id="agos-01",
        nodes=[
            NodeSpec(id="worker-a", kind="worker", backend="langgraph"),
            NodeSpec(id="worker-b", kind="worker", backend="langgraph"),
            NodeSpec(
                id="merge",
                kind="arbiter",
                backend="langgraph",
                depends_on=["worker-a", "worker-b"],
            ),
        ],
        entry_nodes=["worker-a", "worker-b"],
    )

    handle = backend.start(spec)
    status = backend.poll(handle)
    snapshot = backend.collect(handle)

    assert status.state in {"running", "completed"}
    assert snapshot["backend"] == "langgraph"
    assert snapshot["run_id"] == "langgraph-run-01"


def test_langgraph_backend_dispatches_node_actions_when_available():
    if not LangGraphBackend.is_available():
        pytest.skip("langgraph is not installed")

    calls: list[str] = []

    def dispatch(node, state):
        calls.append(node.id)
        return {
            "visited_nodes": [node.id],
            "output_refs": {node.id: f"evidence/{node.id}.json"},
        }

    backend = LangGraphBackend(node_dispatch=dispatch)
    spec = OrchestrationRunSpec(
        run_id="langgraph-dispatch-run-01",
        task_id="agos-01",
        nodes=[
            NodeSpec(id="worker-01", kind="worker", backend="langgraph"),
            NodeSpec(id="reviewer-01", kind="reviewer", backend="langgraph", depends_on=["worker-01"]),
            NodeSpec(id="arbiter-01", kind="arbiter", backend="langgraph", depends_on=["reviewer-01"]),
        ],
    )

    handle = backend.start(spec)
    snapshot = backend.collect(handle)

    assert calls == ["worker-01", "reviewer-01", "arbiter-01"]
    assert snapshot["output_refs"]["arbiter-01"] == "evidence/arbiter-01.json"
