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
