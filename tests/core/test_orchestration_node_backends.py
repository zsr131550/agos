from __future__ import annotations

from agos.core.execution_worker import WorkerHealth, WorkerHealthCheck, WorkerRun, WorkerRunStatus
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.node_backends import (
    ArbiterNodeBackend,
    ReviewerNodeBackend,
    WorkerNodeBackend,
)
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus


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


class FakeReviewer:
    name = "fake-reviewer"

    def __init__(self) -> None:
        self.request = None

    def start(self, request):
        self.request = request
        return ReviewerRun(
            backend=self.name,
            run_id=request.run_id,
            reviewer_id=request.reviewer_id,
            state="running",
        )

    def poll(self, run_id: str, *, reviewer_id: str):
        return ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id=reviewer_id,
            state="completed",
            raw_ref="reviews/reviewer.json",
            detail="review complete",
        )

    def cancel(self, run_id: str):
        return ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id="reviewer-a",
            state="cancelled",
            detail="cancelled by test",
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


def test_worker_node_backend_uses_defaults_and_maps_empty_outputs():
    backend = WorkerNodeBackend(FakeExecutionWorker())
    spec = OrchestrationRunSpec(
        run_id="graph-run",
        task_id="agos-01",
        nodes=(
            NodeSpec(
                id="worker-default",
                kind="worker",
                backend="fake-worker",
                metadata={"prompt": "Prompt from metadata"},
            ),
        ),
    )

    handle = backend.start(spec, spec.nodes[0])
    status = backend.cancel(handle)

    assert handle.job_id == "graph-run:worker-default"
    assert status.state == "cancelled"
    assert status.output_refs == {}
    assert backend.collect(handle) == {
        "run_id": "graph-run",
        "node_id": "worker-default",
        "job_id": "graph-run:worker-default",
    }


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


def test_arbiter_node_backend_cancel_reports_cancelled_state():
    backend = ArbiterNodeBackend(name="merge_arbiter", output_refs={"arbiter": "summary.json"})
    spec = OrchestrationRunSpec(
        run_id="graph-run",
        task_id="agos-01",
        nodes=(NodeSpec(id="arbiter", kind="arbiter", backend="merge_arbiter"),),
    )

    handle = backend.start(spec, spec.nodes[0])
    status = backend.cancel(handle)

    assert status.state == "cancelled"
    assert status.output_refs is None


def test_reviewer_node_backend_maps_review_lifecycle():
    reviewer = FakeReviewer()
    backend = ReviewerNodeBackend(reviewer)
    spec = OrchestrationRunSpec(
        run_id="graph-run",
        task_id="agos-01",
        nodes=(
            NodeSpec(
                id="review-node",
                kind="reviewer",
                backend="fake-reviewer",
                metadata={
                    "reviewer_id": "reviewer-a",
                    "role": "security",
                    "review_id": "review-01",
                    "task_title": "Review target",
                    "task_intent": "Check safety",
                    "diff_kind": "candidate_patch",
                    "diff_evidence_ref": "evidence/diff.patch",
                    "ledger_head_hash": "ledger-hash",
                    "context_ref": "evidence/context.md",
                },
            ),
        ),
    )

    handle = backend.start(spec, spec.nodes[0])
    status = backend.poll(handle)
    cancelled = backend.cancel(handle)

    assert reviewer.request is not None
    assert reviewer.request.run_id == "graph-run:review-node"
    assert reviewer.request.reviewer_id == "reviewer-a"
    assert reviewer.request.role == "security"
    assert reviewer.request.packet.review_id == "review-01"
    assert reviewer.request.packet.task_id == "agos-01"
    assert reviewer.request.packet.context_refs == ["evidence/context.md"]
    assert handle.job_id == "graph-run:review-node"
    assert status.state == "completed"
    assert status.detail == "review complete"
    assert status.output_refs == {"review-node": "reviews/reviewer.json"}
    assert cancelled.state == "cancelled"
    assert backend.collect(handle) == {
        "run_id": "graph-run",
        "node_id": "review-node",
        "job_id": "graph-run:review-node",
    }


def test_worker_node_backend_maps_blocked_worker_state_to_waiting():
    class BlockedWorker(FakeExecutionWorker):
        def poll(self, run_id: str, *, subtask_id: str):
            return WorkerRunStatus(
                backend=self.name,
                run_id=run_id,
                subtask_id=subtask_id,
                state="blocked",
                detail="waiting for input",
            )

    backend = WorkerNodeBackend(BlockedWorker())
    spec = OrchestrationRunSpec(
        run_id="graph-run",
        task_id="agos-01",
        nodes=(NodeSpec(id="worker-blocked", kind="worker", backend="fake-worker"),),
    )

    handle = backend.start(spec, spec.nodes[0])
    status = backend.poll(handle)

    assert status.state == "waiting"
    assert status.detail == "waiting for input"


def test_worker_node_backend_maps_unknown_worker_state_to_failed():
    class UnknownStateWorker(FakeExecutionWorker):
        def poll(self, run_id: str, *, subtask_id: str):
            return WorkerRunStatus.model_construct(
                backend=self.name,
                run_id=run_id,
                subtask_id=subtask_id,
                state="mystery",
                detail="unexpected state",
                output_refs=[],
            )

    backend = WorkerNodeBackend(UnknownStateWorker())
    spec = OrchestrationRunSpec(
        run_id="graph-run",
        task_id="agos-01",
        nodes=(NodeSpec(id="worker-mystery", kind="worker", backend="fake-worker"),),
    )

    handle = backend.start(spec, spec.nodes[0])
    status = backend.poll(handle)

    assert status.state == "failed"
    assert status.detail == "unexpected state"
