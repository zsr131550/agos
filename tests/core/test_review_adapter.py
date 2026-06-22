from __future__ import annotations

from agos.core.review import Finding, ReviewPacket
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus, ReviewerStartRequest


def test_reviewer_lifecycle_models_round_trip():
    packet = ReviewPacket(
        review_id="review-01",
        task_id="agos-01",
        task_title="Title",
        task_intent="Intent",
        diff_kind="governed_repo_diff",
        ledger_head_hash="abc123",
    )
    request = ReviewerStartRequest(
        run_id="review-run-01",
        reviewer_id="security",
        role="security_reviewer",
        packet=packet,
        metadata={"required": "true"},
    )
    run = ReviewerRun(
        backend="fake_reviewer",
        run_id=request.run_id,
        reviewer_id=request.reviewer_id,
        state="running",
    )
    status = ReviewerRunStatus(
        backend=run.backend,
        run_id=run.run_id,
        reviewer_id=run.reviewer_id,
        state="completed",
        findings=[
            Finding(
                id="finding-01",
                review_id="review-01",
                source_agent="security",
                category="security",
                severity="high",
                blocking=True,
                title="Unsafe command",
                body="Unsafe shell use.",
            )
        ],
    )

    assert request.model_dump()["role"] == "security_reviewer"
    assert status.is_terminal
    assert status.findings[0].source_agent == "security"
