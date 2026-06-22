"""Deterministic reviewer adapter for tests and local dry runs."""
from __future__ import annotations

from agos.core.review import Finding
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus, ReviewerStartRequest


class FakeReviewerAdapter:
    name = "fake"

    def __init__(
        self,
        *,
        name: str = "fake",
        findings: list[Finding] | None = None,
        state: str = "completed",
    ) -> None:
        self.name = name
        self.findings = list(findings or [])
        self.state = state
        self._runs: dict[str, ReviewerRunStatus] = {}

    def start(self, request: ReviewerStartRequest) -> ReviewerRun:
        status = ReviewerRunStatus(
            backend=self.name,
            run_id=request.run_id,
            reviewer_id=request.reviewer_id,
            state=self.state,
            findings=self.findings if self.state == "completed" else [],
        )
        self._runs[request.run_id] = status
        return ReviewerRun(
            backend=self.name,
            run_id=request.run_id,
            reviewer_id=request.reviewer_id,
            state="running" if self.state == "completed" else self.state,
        )

    def poll(self, run_id: str, *, reviewer_id: str) -> ReviewerRunStatus:
        return self._runs.get(
            run_id,
            ReviewerRunStatus(
                backend=self.name,
                run_id=run_id,
                reviewer_id=reviewer_id,
                state="failed",
                detail="unknown fake reviewer run",
            ),
        )

    def cancel(self, run_id: str) -> ReviewerRunStatus:
        previous = self._runs.get(run_id)
        status = ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id=previous.reviewer_id if previous else "unknown",
            state="cancelled",
        )
        self._runs[run_id] = status
        return status
