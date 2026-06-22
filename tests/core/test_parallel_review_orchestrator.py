from __future__ import annotations

from agos.core.review import Finding, ReviewPacket
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus
from agos.core.review_orchestrator import ParallelReviewOrchestrator, ReviewerSpec


class FakeReviewer:
    def __init__(self, name: str, *, state: str = "completed") -> None:
        self.name = name
        self.state = state
        self.started: list[str] = []

    def start(self, request):
        self.started.append(request.reviewer_id)
        return ReviewerRun(
            backend=self.name,
            run_id=f"{request.run_id}:{request.reviewer_id}",
            reviewer_id=request.reviewer_id,
            state="running",
        )

    def poll(self, run_id: str, *, reviewer_id: str):
        if self.state == "failed":
            return ReviewerRunStatus(
                backend=self.name,
                run_id=run_id,
                reviewer_id=reviewer_id,
                state="failed",
                detail="reviewer failed",
            )
        return ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id=reviewer_id,
            state="completed",
            findings=[
                Finding(
                    id=f"finding-{reviewer_id}",
                    review_id="review-01",
                    source_agent=reviewer_id,
                    category="test",
                    severity="medium",
                    blocking=False,
                    title="Observation",
                    body="Reviewer observation.",
                )
            ],
        )

    def cancel(self, run_id: str):
        return ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id="unknown",
            state="cancelled",
        )


def test_parallel_review_orchestrator_runs_multiple_reviewers():
    security = FakeReviewer("security_backend")
    tests = FakeReviewer("test_backend")
    orchestrator = ParallelReviewOrchestrator(
        reviewers={
            "security_backend": security,
            "test_backend": tests,
        }
    )

    result = orchestrator.run(
        run_id="review-run-01",
        packet=_packet(),
        reviewers=[
            ReviewerSpec(id="security", role="security_reviewer", adapter="security_backend"),
            ReviewerSpec(id="tests", role="test_reviewer", adapter="test_backend"),
        ],
        max_parallel=2,
    )

    assert result.state == "completed"
    assert [finding.source_agent for finding in result.findings] == ["security", "tests"]
    assert security.started == ["security"]
    assert tests.started == ["tests"]


def test_required_reviewer_failure_fails_review_run():
    failing = FakeReviewer("security_backend", state="failed")
    optional = FakeReviewer("optional_backend", state="failed")
    orchestrator = ParallelReviewOrchestrator(
        reviewers={
            "security_backend": failing,
            "optional_backend": optional,
        }
    )

    result = orchestrator.run(
        run_id="review-run-01",
        packet=_packet(),
        reviewers=[
            ReviewerSpec(
                id="security",
                role="security_reviewer",
                adapter="security_backend",
                required=True,
            ),
            ReviewerSpec(
                id="product",
                role="product_reviewer",
                adapter="optional_backend",
                required=False,
            ),
        ],
    )

    assert result.state == "failed"
    assert result.failed_reviewers == ("security",)


def _packet() -> ReviewPacket:
    return ReviewPacket(
        review_id="review-01",
        task_id="agos-01",
        task_title="Title",
        task_intent="Intent",
        diff_kind="governed_repo_diff",
        ledger_head_hash="abc123",
    )
