"""Manual reviewer adapter for human-in-the-loop review steps."""
from __future__ import annotations

from pydantic import BaseModel, Field

from agos.core.orchestration.models import AgentJobHandle


class ManualReviewRequest(BaseModel):
    """Submission payload for a manual review request."""

    review_id: str
    node_id: str
    run_id: str
    metadata: dict[str, str] = Field(default_factory=dict)


class ManualReviewerAdapter:
    """Adapter that records a waiting manual review job."""

    name = "manual"

    def submit(self, request: ManualReviewRequest) -> AgentJobHandle:
        return AgentJobHandle(
            backend=self.name,
            job_id=request.review_id,
            node_id=request.node_id,
            run_id=request.run_id,
        )
