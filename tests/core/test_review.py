from __future__ import annotations

import pytest
from pydantic import ValidationError

from agos.core.review import (
    Finding,
    FindingLocation,
    FindingResolution,
    ReviewFindingStatus,
    ReviewPacket,
    ReviewReport,
)


def test_review_packet_round_trips_with_stable_defaults():
    packet = ReviewPacket(
        review_id="review-01",
        task_id="agos-01",
        task_title="Add login rate limiting",
        task_intent="Protect /login from brute force",
        acceptance=["5 failures lock account"],
        diff_kind="governed_repo_diff",
        diff_evidence_ref="repo.diff",
        ledger_head_hash="abc123",
        checkpoint_refs=["messages/run-1.jsonl"],
        gate_refs={"tests_pass": "gates/tests.log"},
    )

    data = packet.model_dump()

    assert data["review_id"] == "review-01"
    assert data["acceptance"] == ["5 failures lock account"]
    assert data["gate_refs"] == {"tests_pass": "gates/tests.log"}


def test_finding_requires_human_approval_for_accepted_risk():
    finding = Finding(
        id="finding-01",
        review_id="review-01",
        source_agent="security_reviewer",
        category="security",
        severity="high",
        blocking=True,
        title="Unsafe command execution",
        body="User input reaches shell=True.",
        location=FindingLocation(file="src/agos/core/gate.py", line=68),
        evidence_refs=["reviews/review-01/packet.json"],
        suggested_fix="Use argv execution.",
    )

    with pytest.raises(ValidationError):
        finding.with_resolution(
            FindingResolution(
                status="accepted_risk",
                evidence_refs=["reviews/review-01/packet.json"],
                rationale="Risk accepted for compatibility.",
                approved_by=None,
            )
        )


def test_resolved_finding_requires_evidence():
    finding = Finding(
        id="finding-01",
        review_id="review-01",
        source_agent="test_reviewer",
        category="test",
        severity="medium",
        blocking=True,
        title="Missing regression test",
        body="The new behavior is not covered.",
    )

    with pytest.raises(ValidationError):
        finding.with_resolution(
            FindingResolution(
                status="resolved",
                evidence_refs=[],
                rationale="Added regression test.",
            )
        )


def test_review_report_lists_blocking_open_findings():
    expected_status: ReviewFindingStatus = "open"
    report = ReviewReport(
        review_id="review-01",
        task_id="agos-01",
        packet_ref="reviews/review-01/packet.json",
        findings=[
            Finding(
                id="finding-01",
                review_id="review-01",
                source_agent="security_reviewer",
                category="security",
                severity="high",
                blocking=True,
                title="Unsafe command execution",
                body="User input reaches shell=True.",
            )
        ],
    )

    assert report.findings[0].status == expected_status
    assert [finding.id for finding in report.open_blocking_findings()] == ["finding-01"]
