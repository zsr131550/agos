from __future__ import annotations

import json
import pytest

from agos.core.repo import repo_paths
from agos.core.review import Finding, ReviewPacket, ReviewReport
from agos.core.review_store import ReviewStore


def test_review_store_writes_packet_raw_findings_and_report(tmp_repo):
    paths = repo_paths(tmp_repo)
    store = ReviewStore(paths)
    packet = ReviewPacket(
        review_id="review-01",
        task_id="agos-01",
        task_title="Task",
        diff_kind="governed_repo_diff",
        ledger_head_hash="head",
    )
    finding = Finding(
        id="finding-01",
        review_id="review-01",
        source_agent="security_reviewer",
        category="security",
        severity="high",
        blocking=True,
        title="Risk",
        body="A risk exists.",
    )
    report = ReviewReport(
        review_id="review-01",
        task_id="agos-01",
        packet_ref="reviews/review-01/packet.json",
        findings=[finding],
    )

    assert store.write_packet(packet) == "reviews/review-01/packet.json"
    assert store.write_raw_output("review-01", "security_reviewer", {"ok": True}) == (
        "reviews/review-01/raw/security_reviewer.json"
    )
    raw_output_path = paths.reviews / "review-01" / "raw" / "security_reviewer.json"
    assert raw_output_path.exists()
    assert json.loads(raw_output_path.read_text(encoding="utf-8")) == {"ok": True}
    assert store.write_report(report) == "reviews/review-01/findings.json"
    assert store.write_markdown_report(report) == "reviews/review-01/report.md"

    packet_data = json.loads((paths.reviews / "review-01" / "packet.json").read_text(encoding="utf-8"))
    assert packet_data["task_id"] == "agos-01"
    assert "Risk" in (paths.reviews / "review-01" / "report.md").read_text(encoding="utf-8")


def test_review_store_reads_latest_reports(tmp_repo):
    paths = repo_paths(tmp_repo)
    store = ReviewStore(paths)
    for review_id in ["review-01", "review-02"]:
        report = ReviewReport(
            review_id=review_id,
            task_id="agos-01",
            packet_ref=f"reviews/{review_id}/packet.json",
            findings=[],
        )
        store.write_report(report)

    assert [report.review_id for report in store.read_reports()] == ["review-01", "review-02"]


def test_review_store_rejects_unsafe_review_id(tmp_repo):
    paths = repo_paths(tmp_repo)
    store = ReviewStore(paths)
    packet = ReviewPacket(
        review_id="../escape",
        task_id="agos-01",
        task_title="Task",
        diff_kind="governed_repo_diff",
        ledger_head_hash="head",
    )

    with pytest.raises(ValueError, match="review_id"):
        store.write_packet(packet)

    assert not (paths.reviews.parent / "escape").exists()


def test_review_store_rejects_unsafe_reviewer(tmp_repo):
    paths = repo_paths(tmp_repo)
    store = ReviewStore(paths)

    with pytest.raises(ValueError, match="reviewer"):
        store.write_raw_output("review-01", "../../pwn", {"ok": True})

    assert not (paths.reviews.parent.parent / "pwn.json").exists()
