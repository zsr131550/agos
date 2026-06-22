from __future__ import annotations

import json

from agos.adapters.reviewers.manual import ManualReviewRequest, ManualReviewerAdapter
from agos.backends.native_async import NativeAsyncBackend
from agos.core.orchestration.models import AgentJobHandle, NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.runtime import PersistedNodeState, load_node_state, save_node_state
from agos.core.orchestration.scheduler import runnable_nodes


def _node(
    node_id: str,
    *,
    kind: str = "worker",
    backend: str = "native_async",
    depends_on: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        kind=kind,
        backend=backend,
        depends_on=depends_on or [],
        metadata=metadata or {},
    )


def test_native_backend_waits_for_manual_reviewer_node():
    backend = NativeAsyncBackend()
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[
            _node(
                "manual-review",
                kind="reviewer",
                backend="manual",
                metadata={"mode": "wait_for_manual_input"},
            )
        ],
    )

    handle = backend.start(spec)
    state = backend.poll(handle, spec)

    assert state.run_id == "run-01"
    assert state.state == "waiting"
    assert state.waiting_nodes == ("manual-review",)
    assert state.completed_nodes == ()
    assert state.failed_nodes == ()


def test_native_backend_collects_waiting_snapshot():
    backend = NativeAsyncBackend()
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[_node("manual-review", kind="reviewer", backend="manual")],
    )

    handle = backend.start(spec)

    assert backend.collect(handle, spec) == {
        "run_id": "run-01",
        "state": "waiting",
        "waiting_nodes": ["manual-review"],
        "completed_nodes": [],
        "failed_nodes": [],
    }


def test_manual_reviewer_adapter_submit_returns_waiting_job_handle():
    adapter = ManualReviewerAdapter()

    handle = adapter.submit(ManualReviewRequest(review_id="review-01", node_id="manual-review", run_id="run-01"))

    assert handle == AgentJobHandle(
        backend="manual",
        job_id="review-01",
        node_id="manual-review",
        run_id="run-01",
    )


def test_save_node_state_persists_json_payload(tmp_path):
    state = PersistedNodeState(
        node_id="manual-review",
        state="waiting",
        attempts=1,
        output_refs={"raw": "reviews/review-01/raw/manual.json"},
    )
    destination = tmp_path / "orchestration" / "node_states" / "manual-review.json"

    save_node_state(destination, state)

    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "node_id": "manual-review",
        "state": "waiting",
        "attempts": 1,
        "output_refs": {"raw": "reviews/review-01/raw/manual.json"},
        "error": None,
    }
    assert load_node_state(destination) == state


def test_runnable_nodes_only_include_unblocked_nodes():
    nodes = (
        _node("worker-01"),
        _node("reviewer-01", kind="reviewer", backend="manual", depends_on=["worker-01"]),
        _node("arbiter-01", kind="arbiter", depends_on=["reviewer-01"]),
    )
    states = {
        "worker-01": PersistedNodeState(node_id="worker-01", state="completed"),
        "reviewer-01": PersistedNodeState(node_id="reviewer-01", state="waiting"),
    }

    assert runnable_nodes(nodes, states) == ()


def test_runnable_nodes_return_ready_nodes_in_spec_order():
    nodes = (
        _node("worker-01"),
        _node("worker-02"),
        _node("reviewer-01", kind="reviewer", backend="manual", depends_on=["worker-01"]),
    )
    states = {
        "worker-01": PersistedNodeState(node_id="worker-01", state="completed"),
    }

    assert runnable_nodes(nodes, states) == ("worker-02", "reviewer-01")
