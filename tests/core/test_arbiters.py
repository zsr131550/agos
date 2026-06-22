from __future__ import annotations

import pytest

from agos.core.arbiters import DeterministicReviewArbiter
from agos.core.arbiters import CandidateDecisionArbiter
from agos.core.arbiters import CandidateDecisionSnapshot
from agos.core.arbiters import CandidateMergeArbiter
from agos.core.execution import CandidatePatch
from agos.core.review import Finding


def test_deterministic_review_arbiter_sorts_findings_by_severity_then_id():
    arbiter = DeterministicReviewArbiter()
    findings = [
        Finding(
            id="finding-b",
            review_id="review-01",
            source_agent="reviewer-b",
            category="security",
            severity="medium",
            blocking=False,
            title="Medium risk",
            body="Body",
        ),
        Finding(
            id="finding-a",
            review_id="review-01",
            source_agent="reviewer-a",
            category="security",
            severity="critical",
            blocking=True,
            title="Critical risk",
            body="Body",
        ),
        Finding(
            id="finding-c",
            review_id="review-01",
            source_agent="reviewer-c",
            category="security",
            severity="critical",
            blocking=True,
            title="Another critical risk",
            body="Body",
        ),
    ]

    ordered = arbiter.arbitrate(findings)

    assert [finding.id for finding in ordered] == ["finding-a", "finding-c", "finding-b"]


def test_deterministic_review_arbiter_preserves_input_payloads():
    arbiter = DeterministicReviewArbiter()
    findings = [
        Finding(
            id="finding-01",
            review_id="review-01",
            source_agent="reviewer-a",
            category="correctness",
            severity="low",
            blocking=False,
            title="Nit",
            body="Original body",
            evidence_refs=["reviews/review-01/raw/reviewer-a.json"],
        )
    ]

    ordered = arbiter.arbitrate(findings)

    assert ordered[0].model_dump() == findings[0].model_dump()


def test_candidate_merge_arbiter_rejects_overlapping_accepted_candidates():
    arbiter = CandidateMergeArbiter()
    candidate = CandidatePatch(
        id="candidate-01",
        task_id="agos-01",
        subtask_id="subtask-01",
        source_agent="local_worktree",
        workspace_ref="execution/workspaces/subtask-01.json",
        patch_ref="evidence/candidate_patches/candidate-01.patch",
        patch_sha256="sha",
        base_commit="base",
        summary="summary",
        status="accepted",
    )
    other = CandidatePatch(
        id="candidate-02",
        task_id="agos-01",
        subtask_id="subtask-02",
        source_agent="local_worktree",
        workspace_ref="execution/workspaces/subtask-02.json",
        patch_ref="evidence/candidate_patches/candidate-02.patch",
        patch_sha256="sha",
        base_commit="base",
        summary="summary",
        status="accepted",
    )

    decision = arbiter.decide(
        candidate,
        accepted_candidate_paths={
            other.id: ["src/agos/core/execution.py"],
        },
        dirty_paths=[],
        patch_paths=["src/agos/core/execution.py"],
    )

    assert not decision.allowed


def test_candidate_decision_arbiter_requires_tests_and_review_for_acceptance():
    arbiter = CandidateDecisionArbiter()

    with pytest.raises(ValueError, match="accepted candidate decisions require passed tests"):
        arbiter.decide(
            CandidateDecisionSnapshot(
                candidate_id="candidate-01",
                decision="accepted",
                reason="looks good",
                decided_by="local_user",
                evidence_refs=("evidence/candidate_patches/candidate-01.patch",),
                tests_passed=False,
                review_binding_current=False,
                review_open_blocking_count=1,
                patch_ref="evidence/candidate_patches/candidate-01.patch",
                test_refs=("execution/tests/candidate-test-01.json",),
                review_report_ref="reviews/review-01/findings.json",
            )
        )
