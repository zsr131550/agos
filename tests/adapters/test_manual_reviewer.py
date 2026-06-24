from __future__ import annotations

from agos.adapters.reviewers.manual import ManualReviewRequest, ManualReviewerAdapter
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec
from agos.core.review import ReviewPacket
from agos.core.review_adapter import ReviewerStartRequest


def _packet() -> ReviewPacket:
    return ReviewPacket(
        review_id="review-01",
        task_id="agos-01",
        task_title="Review task",
        diff_kind="candidate_patch",
        ledger_head_hash="a" * 64,
    )


def test_manual_reviewer_lifecycle():
    adapter = ManualReviewerAdapter(name="manual_security")
    request = ReviewerStartRequest(
        run_id="review-run-01",
        reviewer_id="security",
        role="security_reviewer",
        packet=_packet(),
    )

    run = adapter.start(request)
    running = adapter.poll(run.run_id, reviewer_id=run.reviewer_id)
    cancelled = adapter.cancel(run.run_id)
    after_cancel = adapter.poll(run.run_id, reviewer_id=run.reviewer_id)
    unknown = adapter.poll("missing", reviewer_id="security")

    assert run.state == "running"
    assert running.detail == "waiting for manual review"
    assert cancelled.state == "cancelled"
    assert after_cancel.state == "cancelled"
    assert unknown.state == "failed"


def test_manual_reviewer_orchestration_node_lifecycle():
    adapter = ManualReviewerAdapter(name="manual")
    spec = OrchestrationRunSpec(
        run_id="orch-run-01",
        task_id="agos-01",
        backend="native_async",
        nodes=[NodeSpec(id="review", kind="reviewer", backend="manual")],
    )
    node = spec.nodes[0]

    handle = adapter.start(spec, node=node)
    status = adapter.poll(handle)
    cancelled = adapter.cancel(handle)
    collected = adapter.collect(handle)
    submitted = adapter.submit(
        ManualReviewRequest(review_id="review-01", node_id="review", run_id="orch-run-01")
    )

    assert handle.job_id == "orch-run-01:review"
    assert status.state == "waiting"
    assert cancelled.state == "cancelled"
    assert collected == {
        "run_id": "orch-run-01",
        "node_id": "review",
        "job_id": "orch-run-01:review",
    }
    assert submitted.job_id == "review-01"
