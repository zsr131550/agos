from __future__ import annotations

from agos.core.arbiters import DeterministicReviewArbiter
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
