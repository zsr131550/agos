"""Deterministic reviewer adapter for tests and local dry runs."""
from __future__ import annotations

from agos.core.review import Finding
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus, ReviewerStartRequest
from agos.core.review_store import ReviewStore


class FakeReviewerAdapter:
    name = "fake"

    def __init__(
        self,
        *,
        name: str = "fake",
        findings: list[Finding] | None = None,
        state: str = "completed",
        review_store: ReviewStore | None = None,
    ) -> None:
        self.name = name
        self.findings = list(findings or [])
        self.state = state
        self.review_store = review_store
        self._runs: dict[str, ReviewerRunStatus] = {}

    def start(self, request: ReviewerStartRequest) -> ReviewerRun:
        raw_ref = self._write_raw_output(request)
        status = ReviewerRunStatus(
            backend=self.name,
            run_id=request.run_id,
            reviewer_id=request.reviewer_id,
            state=self.state,
            findings=self.findings if self.state == "completed" else [],
            raw_ref=raw_ref,
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
            raw_ref=previous.raw_ref if previous else None,
        )
        self._runs[run_id] = status
        return status

    def _write_raw_output(self, request: ReviewerStartRequest) -> str | None:
        if self.review_store is None:
            return None
        return self.review_store.write_raw_output(
            request.packet.review_id,
            self.name,
            {
                "review_run_id": request.run_id,
                "reviewer_id": request.reviewer_id,
                "state": self.state,
                "dev_only": True,
                "findings": [finding.model_dump(mode="python") for finding in self.findings],
            },
        )
