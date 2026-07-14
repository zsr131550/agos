"""Execution orchestration models for isolated candidate patches."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


SubtaskStatus = Literal["pending", "workspace_ready", "running", "completed", "blocked", "cancelled"]
CandidateStatus = Literal[
    "proposed",
    "testing",
    "tested",
    "reviewing",
    "reviewed",
    "accepted",
    "rejected",
    "applied",
    "superseded",
]
CandidateTestState = Literal["running", "passed", "failed"]
ReviewBindingState = Literal["started", "completed", "failed"]
DecisionValue = Literal["accepted", "rejected", "superseded", "needs_changes"]
CandidateProvenanceSource = Literal[
    "worker_export",
    "external_attested",
    "ci_reconstructed",
    "legacy_unattested",
]
ApplyStrategy = Literal["direct_patch"]
MergeStrategy = Literal[
    "single_candidate",
    "non_overlapping_bundle",
    "ordered_patch_stack",
    "manual_merge_required",
]


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_scope_entry(value: str) -> str:
    normalized = str(PurePosixPath(value.replace("\\", "/")))
    if normalized in {"", "."} or normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError(f"invalid relative path: {value!r}")
    return normalized


class ExecutionWorker(BaseModel):
    adapter: str
    role: str = "worker_agent"

    @field_validator("adapter", "role")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("worker fields must be non-empty")
        return value


class ExecutionSubtask(BaseModel):
    id: str
    title: str
    intent: str = ""
    depends_on: list[str] = Field(default_factory=list)
    write_scope: list[str]
    worker: ExecutionWorker
    status: SubtaskStatus = "pending"
    workspace_ref: str | None = None

    @field_validator("id", "title")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("subtask id and title must be non-empty")
        return value

    @field_validator("write_scope")
    @classmethod
    def _validate_write_scope(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("write_scope must be non-empty")
        normalized = [_normalize_scope_entry(entry) for entry in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("write_scope entries must be unique")
        return normalized


class ExecutionPlan(BaseModel):
    id: str
    task_id: str
    max_parallel: int = Field(default=1, ge=1)
    requires_candidate_review: bool = True
    subtasks: list[ExecutionSubtask]

    @model_validator(mode="after")
    def _validate_plan(self) -> "ExecutionPlan":
        if not self.subtasks:
            raise ValueError("execution plan requires at least one subtask")

        ids = [subtask.id for subtask in self.subtasks]
        if len(set(ids)) != len(ids):
            raise ValueError("subtask ids must be unique")

        id_set = set(ids)
        for subtask in self.subtasks:
            for dep in subtask.depends_on:
                if dep not in id_set:
                    raise ValueError(f"unknown dependency {dep!r} for subtask {subtask.id!r}")
                if dep == subtask.id:
                    raise ValueError(f"subtask {subtask.id!r} cannot depend on itself")

        for left_index, left in enumerate(self.subtasks):
            for right in self.subtasks[left_index + 1 :]:
                overlap = set(left.write_scope) & set(right.write_scope)
                if overlap and not self._is_serialized(left.id, right.id):
                    joined = ", ".join(sorted(overlap))
                    raise ValueError(
                        "overlapping write_scope requires dependency serialization: "
                        f"{left.id!r}, {right.id!r}: {joined}"
                    )
        return self

    def _is_serialized(self, left: str, right: str) -> bool:
        return self._depends_on(left, right) or self._depends_on(right, left)

    def _depends_on(self, subtask_id: str, dependency_id: str) -> bool:
        by_id = {subtask.id: subtask for subtask in self.subtasks}
        seen: set[str] = set()
        stack = list(by_id[subtask_id].depends_on)
        while stack:
            current = stack.pop()
            if current == dependency_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(by_id[current].depends_on)
        return False


class WorkspaceBinding(BaseModel):
    subtask_id: str
    kind: Literal["git_worktree"] = "git_worktree"
    path: str
    base_ref: str
    base_commit: str
    worker_handle_metadata: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)

    @property
    def ref(self) -> str:
        return f"execution/workspaces/{self.subtask_id}.json"


class ReviewBinding(BaseModel):
    review_id: str
    packet_ref: str
    report_ref: str | None = None
    raw_refs: list[str] = Field(default_factory=list)
    patch_sha256: str | None = None
    base_commit: str | None = None
    write_scope: list[str] = Field(default_factory=list)
    test_refs: list[str] = Field(default_factory=list)
    ledger_head_at_start: str | None = None
    ledger_head_at_completion: str | None = None
    open_blocking_count: int | None = None
    state: ReviewBindingState = "started"
    created_at: str = Field(default_factory=utc_now_iso)
    completed_at: str | None = None

    @model_validator(mode="after")
    def _validate_completed(self) -> "ReviewBinding":
        if self.state == "completed" and self.report_ref is None:
            raise ValueError("completed review bindings require report_ref")
        return self


class CandidateProvenance(BaseModel):
    """Descriptive source metadata; cryptographic proof is verified separately."""

    source: CandidateProvenanceSource
    ledger_head_hash: str | None = None
    attestation_ref: str | None = None


class CandidatePatch(BaseModel):
    id: str
    task_id: str
    subtask_id: str
    source_agent: str
    workspace_ref: str
    patch_ref: str
    patch_sha256: str
    base_commit: str
    summary: str
    status: CandidateStatus = "proposed"
    test_refs: list[str] = Field(default_factory=list)
    review_refs: list[ReviewBinding] = Field(default_factory=list)
    decision_ref: str | None = None
    provenance: CandidateProvenance | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class CandidateTestRun(BaseModel):
    id: str
    candidate_id: str
    gate_id: str
    stage: Literal["candidate"] = "candidate"
    command: str | None = None
    state: CandidateTestState
    evidence_ref: str
    workspace_ref: str
    started_at: str = Field(default_factory=utc_now_iso)
    completed_at: str | None = None


class ArbiterDecision(BaseModel):
    id: str
    candidate_id: str
    decision: DecisionValue
    reason: str
    apply_strategy: ApplyStrategy = "direct_patch"
    evidence_refs: list[str] = Field(default_factory=list)
    conflict_evidence_refs: list[str] = Field(default_factory=list)
    decided_by: str
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def _validate_decision(self) -> "ArbiterDecision":
        if not self.reason.strip():
            raise ValueError("decision reason must be non-empty")
        if not self.decided_by.strip():
            raise ValueError("decided_by must be non-empty")
        if self.decision == "accepted":
            if not self.evidence_refs:
                raise ValueError("accepted decisions require evidence_refs")
            if self.apply_strategy != "direct_patch":
                raise ValueError("v0.3 accepted decisions require direct_patch")
            if self.conflict_evidence_refs:
                raise ValueError("accepted direct_patch decisions cannot include conflict evidence")
        return self


class CandidateBundleDecision(BaseModel):
    id: str
    strategy: MergeStrategy
    candidate_ids: list[str] = Field(default_factory=list)
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    conflict_candidate_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def _validate_decision(self) -> "CandidateBundleDecision":
        if not self.reason.strip():
            raise ValueError("bundle decision reason must be non-empty")
        if self.strategy != "manual_merge_required" and not self.candidate_ids:
            raise ValueError("automatic bundle decisions require candidate_ids")
        return self


class CandidateMergePreview(BaseModel):
    id: str
    decision_id: str
    strategy: MergeStrategy
    candidate_ids: list[str] = Field(default_factory=list)
    state: Literal["passed", "failed"]
    evidence_refs: list[str] = Field(default_factory=list)
    conflict_evidence_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
