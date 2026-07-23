from __future__ import annotations

import inspect
import json

import pytest

from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.review import Finding, FindingResolution
from agos.core.review_service import ReviewService
from agos.core.status import TaskStatus, load_status, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.core.task_state import TaskStateConflict


def _active_task(tmp_repo):
    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-01",
        title="Review task",
        intent="Add review support",
        acceptance=["review findings are ledgered"],
        gates=["tests_pass"],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    status = TaskStatus.for_started_task(
        task=task,
        run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
        ledger_head_hash=started["hash"],
    )
    save_status(status, paths)
    return paths


def _ledger_records(paths):
    return [
        json.loads(line)
        for line in paths.ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ledger_types(paths):
    return [record["type"] for record in _ledger_records(paths)]


def test_create_packet_writes_packet_and_review_started(tmp_repo):
    paths = _active_task(tmp_repo)
    gate_dir = paths.evidence / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "tests_pass-20260622.log").write_text("ok\n", encoding="utf-8")
    checkpoint = Ledger(paths.ledger).append(
        {
            "type": "checkpoint",
            "task_id": "agos-01",
            "evidence_refs": ["messages/run-1.jsonl"],
        }
    )
    status = load_status(paths)
    assert status is not None
    status.ledger_head_hash = checkpoint["hash"]
    save_status(status, paths)
    service = ReviewService(paths)

    packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")

    assert packet_ref == f"reviews/{packet.review_id}/packet.json"
    assert packet.task_id == "agos-01"
    assert packet.task_title == "Review task"
    assert packet.subject == {}
    assert packet.context_refs == []
    assert packet.gate_refs == {"tests_pass": "gates/tests_pass-20260622.log"}
    assert packet.checkpoint_refs == ["messages/run-1.jsonl"]
    assert _ledger_types(paths)[-1] == "review_started"
    assert load_status(paths).ledger_head_hash == _ledger_records(paths)[-1]["hash"]


def test_create_packet_round_trips_candidate_context(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    signature = inspect.signature(service.create_packet)
    assert "subject" in signature.parameters
    assert "context_refs" in signature.parameters

    packet_ref, packet = service.create_packet(
        diff_kind="candidate_patch",
        diff_evidence_ref="patches/candidate.diff",
        subject={"kind": "candidate", "id": "candidate-01"},
        context_refs=["runs/candidate-01.json", "messages/candidate-01.jsonl"],
    )

    packet_json = json.loads((paths.current_task / packet_ref).read_text(encoding="utf-8"))
    assert packet.subject == {"kind": "candidate", "id": "candidate-01"}
    assert packet.context_refs == ["runs/candidate-01.json", "messages/candidate-01.jsonl"]
    assert packet_json["subject"] == {"kind": "candidate", "id": "candidate-01"}
    assert packet_json["context_refs"] == [
        "runs/candidate-01.json",
        "messages/candidate-01.jsonl",
    ]


def test_ingest_findings_writes_report_and_ledger_events(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    _packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")
    finding = Finding(
        id="finding-01",
        review_id="different-review",
        source_agent="security_reviewer",
        category="security",
        severity="high",
        blocking=True,
        title="Risk",
        body="Risk body.",
        evidence_refs=["reviews/source.json"],
    )

    report_ref, report = service.ingest_findings(packet.review_id, [finding])

    assert report_ref == f"reviews/{packet.review_id}/findings.json"
    assert report.open_blocking_findings()[0].id == "finding-01"
    assert report.findings[0].review_id == packet.review_id
    assert (paths.reviews / packet.review_id / "report.md").exists()
    assert _ledger_types(paths)[-2:] == ["finding_opened", "review_completed"]
    opened = _ledger_records(paths)[-2]
    assert opened["finding_id"] == "finding-01"
    assert opened["evidence_refs"] == ["reviews/source.json"]
    assert opened["task_id"] == "agos-01"
    assert load_status(paths).ledger_head_hash == _ledger_records(paths)[-1]["hash"]


def test_ingest_findings_rejects_missing_packet_review_id(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    finding = Finding(
        id="finding-01",
        review_id="missing-review",
        source_agent="security_reviewer",
        category="security",
        severity="high",
        blocking=True,
        title="Risk",
        body="Risk body.",
    )

    with pytest.raises(ValueError, match="review packet not found"):
        service.ingest_findings("missing-review", [finding])

    assert not (paths.reviews / "missing-review" / "findings.json").exists()
    assert "review_completed" not in _ledger_types(paths)


def test_ingest_findings_rejects_non_open_finding_without_ledger_events(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    _packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")
    original_ledger_types = _ledger_types(paths)
    finding = Finding(
        id="finding-01",
        review_id=packet.review_id,
        source_agent="security_reviewer",
        category="security",
        severity="high",
        blocking=True,
        title="Risk",
        body="Risk body.",
        status="false_positive",
        resolution=FindingResolution(
            status="false_positive",
            rationale="Reviewer says this is not exploitable.",
        ),
    )

    with pytest.raises(ValueError, match="ingested findings must be open"):
        service.ingest_findings(packet.review_id, [finding])

    assert not (paths.reviews / packet.review_id / "findings.json").exists()
    assert not (paths.reviews / packet.review_id / "report.md").exists()
    assert _ledger_types(paths) == original_ledger_types


def test_ingest_findings_rejects_accepted_risk_resolution_without_ledger_events(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    _packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")
    original_ledger_types = _ledger_types(paths)
    finding = Finding(
        id="finding-01",
        review_id=packet.review_id,
        source_agent="security_reviewer",
        category="security",
        severity="high",
        blocking=True,
        title="Risk",
        body="Risk body.",
        status="accepted_risk",
        resolution=FindingResolution(
            status="accepted_risk",
            rationale="Reviewer accepted the risk.",
            approved_by="reviewer",
        ),
    )

    with pytest.raises(ValueError, match="ingested findings must be open"):
        service.ingest_findings(packet.review_id, [finding])

    assert not (paths.reviews / packet.review_id / "findings.json").exists()
    assert _ledger_types(paths) == original_ledger_types


def test_ingest_findings_rejects_duplicate_review_report_and_keeps_open_blocker(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    _packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")
    original_finding = Finding(
        id="finding-01",
        review_id=packet.review_id,
        source_agent="test_reviewer",
        category="test",
        severity="medium",
        blocking=True,
        title="Missing test",
        body="A test is missing.",
    )
    service.ingest_findings(packet.review_id, [original_finding])
    original_report = (paths.reviews / packet.review_id / "findings.json").read_text(
        encoding="utf-8"
    )
    original_ledger_types = _ledger_types(paths)
    replacement_finding = Finding(
        id="finding-02",
        review_id=packet.review_id,
        source_agent="test_reviewer",
        category="test",
        severity="low",
        blocking=False,
        title="Typo",
        body="A typo is present.",
    )

    with pytest.raises(ValueError, match="review report already exists"):
        service.ingest_findings(packet.review_id, [replacement_finding])

    assert (paths.reviews / packet.review_id / "findings.json").read_text(
        encoding="utf-8"
    ) == original_report
    assert service.open_blocking_findings()[0].id == "finding-01"
    with pytest.raises(ValueError, match="open blocking findings: finding-01"):
        service.closeout()
    assert _ledger_types(paths) == original_ledger_types


def test_ingest_findings_rejects_duplicate_finding_ids_across_reports(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    _packet_ref, first_packet = service.create_packet(diff_kind="governed_repo_diff")
    original_finding = Finding(
        id="finding-01",
        review_id=first_packet.review_id,
        source_agent="test_reviewer",
        category="test",
        severity="medium",
        blocking=True,
        title="Missing test",
        body="A test is missing.",
    )
    service.ingest_findings(first_packet.review_id, [original_finding])
    original_report = (paths.reviews / first_packet.review_id / "findings.json").read_text(
        encoding="utf-8"
    )
    original_ledger_types = _ledger_types(paths)
    _packet_ref, second_packet = service.create_packet(diff_kind="governed_repo_diff")
    duplicate_finding = original_finding.model_copy(update={"review_id": second_packet.review_id})

    with pytest.raises(ValueError, match="duplicate finding id"):
        service.ingest_findings(second_packet.review_id, [duplicate_finding])

    assert (paths.reviews / first_packet.review_id / "findings.json").read_text(
        encoding="utf-8"
    ) == original_report
    assert not (paths.reviews / second_packet.review_id / "findings.json").exists()
    assert _ledger_types(paths) == original_ledger_types + ["review_started"]


def test_resolve_finding_updates_report_and_appends_event(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    _packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")
    finding = Finding(
        id="finding-01",
        review_id=packet.review_id,
        source_agent="test_reviewer",
        category="test",
        severity="medium",
        blocking=True,
        title="Missing test",
        body="A test is missing.",
    )
    service.ingest_findings(packet.review_id, [finding])

    updated = service.resolve_finding(
        "finding-01",
        FindingResolution(
            status="resolved",
            evidence_refs=["gates/tests_pass.log"],
            rationale="Regression test added and passing.",
        ),
    )

    assert updated.status == "resolved"
    assert service.store.read_report(packet.review_id).findings[0].status == "resolved"
    assert "- Status: resolved" in (paths.reviews / packet.review_id / "report.md").read_text(
        encoding="utf-8"
    )
    assert _ledger_types(paths)[-1] == "finding_resolved"
    event = _ledger_records(paths)[-1]
    assert event["finding_id"] == "finding-01"
    assert event["evidence_refs"] == ["gates/tests_pass.log"]
    assert load_status(paths).ledger_head_hash == event["hash"]


def test_resolve_finding_rejects_absent_finding_id(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)

    with pytest.raises(ValueError, match="finding not found: finding-404"):
        service.resolve_finding(
            "finding-404",
            FindingResolution(
                status="resolved",
                evidence_refs=["gates/tests_pass.log"],
                rationale="Regression test added and passing.",
            ),
        )


def test_closeout_rejects_stale_entry_revision(tmp_repo, monkeypatch):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    _packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")
    service.ingest_findings(packet.review_id, [])
    write_proof = service.store.write_proof

    def write_proof_after_concurrent_fact(proof):
        refs = write_proof(proof)
        Ledger(paths.ledger).append(
            {
                "type": "review_started",
                "task_id": "agos-01",
                "review_id": "review-concurrent",
                "packet_ref": "reviews/review-concurrent/packet.json",
            }
        )
        return refs

    monkeypatch.setattr(service.store, "write_proof", write_proof_after_concurrent_fact)

    with pytest.raises(TaskStateConflict, match="revision mismatch"):
        service.closeout()

    assert _ledger_types(paths)[-1] == "review_started"
    assert "closeout_completed" not in _ledger_types(paths)
