"""Deterministic review arbitration helpers."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agos.core.execution import ArbiterDecision, CandidatePatch, DecisionValue
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


@dataclass(frozen=True)
class MergeCandidateSnapshot:
    candidate_id: str
    patch_ref: str
    patch_sha256: str
    touched_paths: tuple[str, ...]
    tests_passed: bool
    review_open_blocking_count: int
    accepted: bool
    score: int = 0


@dataclass(frozen=True)
class CandidateBundleMergeDecision:
    strategy: str
    candidate_ids: tuple[str, ...]
    reason: str
    evidence_refs: tuple[str, ...] = ()
    conflict_candidate_ids: tuple[str, ...] = ()


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

    def decide_bundle(
        self,
        candidates: list[MergeCandidateSnapshot],
        *,
        dirty_paths: Iterable[str],
        dependency_order: Iterable[str] = (),
    ) -> CandidateBundleMergeDecision:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.accepted
            and candidate.tests_passed
            and candidate.review_open_blocking_count == 0
        ]
        if not eligible:
            return CandidateBundleMergeDecision(
                strategy="manual_merge_required",
                candidate_ids=(),
                reason="No eligible accepted candidates with passing tests and clean reviews.",
            )

        dirty = {_normalize_path(path) for path in dirty_paths}
        dirty_conflicts = [
            candidate.candidate_id
            for candidate in eligible
            if dirty & {_normalize_path(path) for path in candidate.touched_paths}
        ]
        if dirty_conflicts:
            return CandidateBundleMergeDecision(
                strategy="manual_merge_required",
                candidate_ids=tuple(candidate.candidate_id for candidate in eligible),
                reason="Dirty governed repo paths overlap candidate patches.",
                conflict_candidate_ids=tuple(dirty_conflicts),
            )

        touched_by: dict[str, str] = {}
        conflicts: set[str] = set()
        for candidate in eligible:
            for path in candidate.touched_paths:
                normalized = _normalize_path(path)
                if normalized in touched_by:
                    conflicts.update({touched_by[normalized], candidate.candidate_id})
                touched_by[normalized] = candidate.candidate_id

        eligible_by_id = {candidate.candidate_id: candidate for candidate in eligible}
        dependency_ids = tuple(candidate_id for candidate_id in dependency_order if candidate_id in eligible_by_id)
        if conflicts:
            conflict_ids = tuple(sorted(conflicts))
            if set(conflict_ids).issubset(set(dependency_ids)):
                remaining = tuple(
                    candidate.candidate_id
                    for candidate in sorted(eligible, key=lambda item: (-item.score, item.candidate_id))
                    if candidate.candidate_id not in dependency_ids
                )
                ordered_ids = dependency_ids + remaining
                return CandidateBundleMergeDecision(
                    strategy="ordered_patch_stack",
                    candidate_ids=ordered_ids,
                    reason="Eligible overlapping candidates have an explicit dependency order and require stack dry-run.",
                    evidence_refs=tuple(eligible_by_id[candidate_id].patch_ref for candidate_id in ordered_ids),
                )
            return CandidateBundleMergeDecision(
                strategy="manual_merge_required",
                candidate_ids=tuple(candidate.candidate_id for candidate in eligible),
                reason="Candidate patches overlap and require manual merge.",
                conflict_candidate_ids=conflict_ids,
            )

        ordered_candidates = sorted(eligible, key=lambda item: (-item.score, item.candidate_id))
        ordered_ids = tuple(candidate.candidate_id for candidate in ordered_candidates)
        evidence_refs = tuple(candidate.patch_ref for candidate in ordered_candidates)
        if len(ordered_ids) == 1:
            return CandidateBundleMergeDecision(
                strategy="single_candidate",
                candidate_ids=ordered_ids,
                reason="Exactly one eligible accepted candidate.",
                evidence_refs=evidence_refs,
            )
        return CandidateBundleMergeDecision(
            strategy="non_overlapping_bundle",
            candidate_ids=ordered_ids,
            reason="Eligible candidates touch disjoint paths and can be applied as a bundle.",
            evidence_refs=evidence_refs,
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
