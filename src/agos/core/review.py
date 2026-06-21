"""Review packet, finding, resolution, and proof models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


ReviewSeverity = Literal["low", "medium", "high", "critical"]
ReviewFindingStatus = Literal[
    "open",
    "resolved",
    "accepted_risk",
    "false_positive",
    "superseded",
]
ReviewResolutionStatus = Literal["resolved", "accepted_risk", "false_positive", "superseded"]
ReviewDiffKind = Literal["governed_repo_diff", "candidate_patch", "pr_diff"]


class FindingLocation(BaseModel):
    file: str
    line: int | None = None


class FindingResolution(BaseModel):
    status: ReviewResolutionStatus
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str
    approved_by: str | None = None

    @model_validator(mode="after")
    def _validate_resolution(self) -> "FindingResolution":
        if self.status == "resolved" and not self.evidence_refs:
            raise ValueError("resolved findings require at least one evidence ref")
        if self.status == "accepted_risk" and not self.approved_by:
            raise ValueError("accepted risk requires approved_by")
        if not self.rationale.strip():
            raise ValueError("resolution rationale must be non-empty")
        return self


class Finding(BaseModel):
    id: str
    review_id: str
    source_agent: str
    category: str
    severity: ReviewSeverity
    blocking: bool
    title: str
    body: str
    location: FindingLocation | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    suggested_fix: str | None = None
    status: ReviewFindingStatus = "open"
    resolution: FindingResolution | None = None

    @model_validator(mode="after")
    def _validate_open_resolution(self) -> "Finding":
        if self.status == "open" and self.resolution is not None:
            raise ValueError("open findings cannot have a resolution")
        if self.status != "open" and self.resolution is None:
            raise ValueError("closed findings require a resolution")
        return self

    def with_resolution(self, resolution: FindingResolution) -> "Finding":
        return self.model_copy(update={"status": resolution.status, "resolution": resolution})


class ReviewPacket(BaseModel):
    review_id: str
    task_id: str
    task_title: str
    task_intent: str = ""
    acceptance: list[str] = Field(default_factory=list)
    diff_kind: ReviewDiffKind
    diff_evidence_ref: str | None = None
    ledger_head_hash: str
    checkpoint_refs: list[str] = Field(default_factory=list)
    gate_refs: dict[str, str] = Field(default_factory=dict)


class ReviewReport(BaseModel):
    review_id: str
    task_id: str
    packet_ref: str
    findings: list[Finding] = Field(default_factory=list)

    def open_blocking_findings(self) -> list[Finding]:
        return [
            finding for finding in self.findings if finding.blocking and finding.status == "open"
        ]


class CloseoutProof(BaseModel):
    task_id: str
    ledger_head_hash: str
    review_refs: list[str] = Field(default_factory=list)
    gate_refs: dict[str, str] = Field(default_factory=dict)
    finding_count: int
    blocking_open_count: int
