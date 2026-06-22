"""Reviewer adapter lifecycle seam for review orchestration."""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from agos.core.review import Finding, ReviewPacket


ReviewerRunState = Literal["queued", "running", "completed", "failed", "cancelled"]


class ReviewerStartRequest(BaseModel):
    run_id: str
    reviewer_id: str
    role: str
    packet: ReviewPacket
    metadata: dict[str, str] = Field(default_factory=dict)


class ReviewerRun(BaseModel):
    backend: str
    run_id: str
    reviewer_id: str
    state: ReviewerRunState
    metadata: dict[str, str] = Field(default_factory=dict)


class ReviewerRunStatus(BaseModel):
    backend: str
    run_id: str
    reviewer_id: str
    state: ReviewerRunState
    findings: list[Finding] = Field(default_factory=list)
    raw_ref: str | None = None
    detail: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in {"completed", "failed", "cancelled"}


class ReviewerAdapter(Protocol):
    name: str

    def start(self, request: ReviewerStartRequest) -> ReviewerRun: ...

    def poll(self, run_id: str, *, reviewer_id: str) -> ReviewerRunStatus: ...

    def cancel(self, run_id: str) -> ReviewerRunStatus: ...
