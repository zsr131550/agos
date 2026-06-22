"""Deterministic review arbitration helpers."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agos.core.execution import ArbiterDecision, CandidatePatch, DecisionValue, utc_now_iso
from agos.core.review import Finding
from uuid import uuid4


class DeterministicReviewArbiter:
    """Rule-based arbiter that produces stable ordering for review findings."""

    name = "deterministic_review"

    def arbitrate(self, findings: Iterable[Finding]) -> list[Finding]:
        return sorted(findings, key=_finding_sort_key)


@dataclass(frozen=True)
class CandidateDecisionSnapshot:
    candidate_id: str
    decision: DecisionValue
    reason: str
    decided_by: str
    evidence_refs: tuple[str, ...]
    tests_passed: bool
    review_binding_current: bool
    review_open_blocking_count: int
    patch_ref: str
    test_refs: tuple[str, ...]
    review_report_ref: str | None
    conflict_evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateDecisionResult:
    decision: ArbiterDecision
    candidate_status: str


class CandidateDecisionArbiter:
    """Build candidate decisions from a normalized snapshot."""

    name = "candidate_decision"

    def decide(self, snapshot: CandidateDecisionSnapshot) -> CandidateDecisionResult:
        if snapshot.decision == "accepted":
            if not snapshot.tests_passed:
                raise ValueError("accepted candidate decisions require passed tests")
            if not snapshot.review_binding_current:
                raise ValueError("accepted candidate decisions require current candidate-bound review")
            if snapshot.review_open_blocking_count:
                raise ValueError("accepted candidate decisions require no open blocking findings")
            if snapshot.review_report_ref is None:
                raise ValueError("accepted candidate decisions require candidate review report")
            required_refs = {snapshot.patch_ref, *snapshot.test_refs, snapshot.review_report_ref}
            missing_refs = sorted(required_refs - set(snapshot.evidence_refs))
            if missing_refs:
                raise ValueError(
                    "accepted candidate decisions require evidence refs for patch, tests, and review"
                )
        decision = ArbiterDecision(
            id=_new_id("decision"),
            candidate_id=snapshot.candidate_id,
            decision=snapshot.decision,
            reason=snapshot.reason,
            evidence_refs=list(snapshot.evidence_refs),
            conflict_evidence_refs=list(snapshot.conflict_evidence_refs),
            decided_by=snapshot.decided_by,
        )
        return CandidateDecisionResult(
            decision=decision,
            candidate_status=_candidate_status_for_decision(snapshot.decision),
        )


@dataclass(frozen=True)
class CandidateMergeDecision:
    allowed: bool
    conflict_candidate_ids: tuple[str, ...] = ()
    dirty_paths: tuple[str, ...] = ()


class CandidateMergeArbiter:
    """Detect basic apply-time merge conflicts."""

    name = "candidate_merge"

    def decide(
        self,
        candidate: CandidatePatch,
        *,
        accepted_candidate_paths: dict[str, Iterable[str]],
        dirty_paths: Iterable[str],
        patch_paths: Iterable[str],
    ) -> CandidateMergeDecision:
        candidate_paths = {_normalize_path(path) for path in patch_paths}
        dirty = sorted(candidate_paths & {_normalize_path(path) for path in dirty_paths})
        conflicts = []
        for other_id, other_paths in accepted_candidate_paths.items():
            if other_id == candidate.id:
                continue
            overlap = candidate_paths & {_normalize_path(path) for path in other_paths}
            if overlap:
                conflicts.append(other_id)
        return CandidateMergeDecision(
            allowed=not dirty and not conflicts,
            conflict_candidate_ids=tuple(conflicts),
            dirty_paths=tuple(dirty),
        )


@dataclass(frozen=True)
class ReviewDecisionSnapshot:
    review_id: str
    findings: tuple[Finding, ...]


class ReviewDecisionArbiter:
    """Normalize candidate review findings before they are stored."""

    name = "review_decision"

    def decide(self, snapshot: ReviewDecisionSnapshot) -> tuple[Finding, ...]:
        return tuple(sorted(snapshot.findings, key=_finding_sort_key))


def _finding_sort_key(finding: Finding) -> tuple[int, str]:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return (order[finding.severity], finding.id)


def _candidate_status_for_decision(decision: DecisionValue) -> str:
    if decision == "accepted":
        return "accepted"
    if decision == "rejected":
        return "rejected"
    if decision == "superseded":
        return "superseded"
    return "reviewed"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/").strip("/")
