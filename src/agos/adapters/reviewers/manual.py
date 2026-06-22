"""Manual reviewer adapter for human-in-the-loop review steps."""
from __future__ import annotations

from pydantic import BaseModel, Field

from agos.core.orchestration.models import AgentJobHandle
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus, ReviewerStartRequest


class ManualReviewRequest(BaseModel):
    """Submission payload for a manual review request."""

    review_id: str
    node_id: str
    run_id: str
    metadata: dict[str, str] = Field(default_factory=dict)


class ManualReviewerAdapter:
    """Adapter that records a waiting manual review job."""

    def __init__(self, *, name: str = "manual") -> None:
        self.name = name
        self._runs: dict[str, ReviewerRunStatus] = {}

    def start(self, request_or_run, node=None) -> AgentJobHandle | ReviewerRun:
        """Start a manual review through either legacy or reviewer lifecycle seam."""

        if node is not None:
            run = request_or_run
            return AgentJobHandle(
                backend=self.name,
                job_id=f"{run.run_id}:{node.id}",
                node_id=node.id,
                run_id=run.run_id,
            )

        request: ReviewerStartRequest = request_or_run
        self._runs[request.run_id] = ReviewerRunStatus(
            backend=self.name,
            run_id=request.run_id,
            reviewer_id=request.reviewer_id,
            state="running",
            detail="waiting for manual review",
        )
        return ReviewerRun(
            backend=self.name,
            run_id=request.run_id,
            reviewer_id=request.reviewer_id,
            state="running",
        )

    def poll(self, run_id: str, *, reviewer_id: str) -> ReviewerRunStatus:
        return self._runs.get(
            run_id,
            ReviewerRunStatus(
                backend=self.name,
                run_id=run_id,
                reviewer_id=reviewer_id,
                state="failed",
                detail="unknown manual review run",
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

    def submit(self, request: ManualReviewRequest) -> AgentJobHandle:
        return AgentJobHandle(
            backend=self.name,
            job_id=request.review_id,
            node_id=request.node_id,
            run_id=request.run_id,
        )
