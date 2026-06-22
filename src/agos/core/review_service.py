"""Review orchestration service for packets, findings, and resolutions."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from ulid import ULID

from agos.core.ledger import Ledger
from agos.core.repo import AgosPaths
from agos.core.review import (
    CloseoutProof,
    Finding,
    FindingResolution,
    ReviewDiffKind,
    ReviewPacket,
    ReviewReport,
)
from agos.core.review_store import ReviewStore
from agos.core.status import TaskStatus, load_status, save_status
from agos.core.task import load_task


def new_review_id() -> str:
    """Return a fresh review id."""

    return f"review-{ULID()}"


class ReviewService:
    """Coordinate review artifacts with the task ledger."""

    def __init__(self, paths: AgosPaths) -> None:
        self.paths = paths
        self.store = ReviewStore(paths)
        self.ledger = Ledger(paths.ledger)

    def create_packet(
        self,
        *,
        diff_kind: str,
        diff_evidence_ref: str | None = None,
        subject: dict[str, str] | None = None,
        context_refs: list[str] | None = None,
    ) -> tuple[str, ReviewPacket]:
        status = _load_active_status(self.paths)
        task = load_task(self.paths.task_yaml)
        packet = ReviewPacket(
            review_id=new_review_id(),
            task_id=task.id,
            task_title=task.title,
            task_intent=task.intent,
            acceptance=list(task.acceptance),
            subject=dict(subject or {}),
            context_refs=list(context_refs or []),
            diff_kind=cast(ReviewDiffKind, diff_kind),
            diff_evidence_ref=diff_evidence_ref,
            ledger_head_hash=status.ledger_head_hash,
            checkpoint_refs=self._checkpoint_refs(),
            gate_refs=self._gate_refs(),
        )
        packet_ref = self.store.write_packet(packet)
        self._append_and_save_status(
            status,
            {
                "type": "review_started",
                "review_id": packet.review_id,
                "task_id": task.id,
                "packet_ref": packet_ref,
            },
        )
        return packet_ref, packet

    def start_manual_review_packet(self, *, diff_kind: str) -> tuple[str, ReviewPacket]:
        """Compatibility helper for orchestration-backed manual review flows."""

        return self.create_packet(diff_kind=diff_kind)

    def ingest_findings(
        self,
        review_id: str,
        findings: Iterable[Finding],
    ) -> tuple[str, ReviewReport]:
        status = _load_active_status(self.paths)
        task = load_task(self.paths.task_yaml)
        normalized = [finding.model_copy(update={"review_id": review_id}) for finding in findings]
        packet_path = self._packet_path(review_id)
        if not packet_path.is_file():
            raise ValueError(f"review packet not found: {review_id}")
        self._validate_report_not_existing(review_id)
        self._validate_unique_finding_ids(normalized)
        self._validate_ingested_findings_are_open(normalized)
        report = ReviewReport(
            review_id=review_id,
            task_id=task.id,
            packet_ref=f"reviews/{review_id}/packet.json",
            findings=normalized,
        )
        report_ref = self.store.write_report(report)
        self.store.write_markdown_report(report)

        for finding in normalized:
            self._append_and_save_status(
                status,
                {
                    "type": "finding_opened",
                    "review_id": review_id,
                    "finding_id": finding.id,
                    "severity": finding.severity,
                    "blocking": finding.blocking,
                    "title": finding.title,
                    "evidence_refs": list(finding.evidence_refs),
                },
            )
        self._append_and_save_status(
            status,
            {
                "type": "review_completed",
                "review_id": review_id,
                "task_id": task.id,
                "report_ref": report_ref,
                "open_blocking_count": len(report.open_blocking_findings()),
            },
        )
        return report_ref, report

    def resolve_finding(self, finding_id: str, resolution: FindingResolution) -> Finding:
        status = _load_active_status(self.paths)
        for report in self.store.read_reports():
            for index, finding in enumerate(report.findings):
                if finding.id != finding_id:
                    continue

                updated = finding.with_resolution(resolution)
                findings = list(report.findings)
                findings[index] = updated
                updated_report = report.model_copy(update={"findings": findings})
                self.store.write_report(updated_report)
                self.store.write_markdown_report(updated_report)

                event_type = (
                    "finding_accepted_risk"
                    if resolution.status == "accepted_risk"
                    else "finding_resolved"
                )
                self._append_and_save_status(
                    status,
                    {
                        "type": event_type,
                        "finding_id": finding_id,
                        "review_id": report.review_id,
                        "status": resolution.status,
                        "evidence_refs": list(resolution.evidence_refs),
                        "rationale": resolution.rationale,
                        "approved_by": resolution.approved_by,
                    },
                )
                return updated
        raise ValueError(f"finding not found: {finding_id}")

    def closeout(self) -> CloseoutProof:
        status = _load_active_status(self.paths)
        if status.phase == "done":
            raise ValueError("task is already done")

        task = load_task(self.paths.task_yaml)
        reports = self.store.read_reports()
        if not reports:
            raise ValueError("at least one completed review report is required")

        findings = [finding for report in reports for finding in report.findings]
        open_blocking = [
            finding for finding in findings if finding.blocking and finding.status == "open"
        ]
        if open_blocking:
            ids = ", ".join(finding.id for finding in open_blocking)
            raise ValueError(f"open blocking findings: {ids}")

        proof = CloseoutProof(
            task_id=task.id,
            ledger_head_hash=status.ledger_head_hash,
            review_refs=[
                f"reviews/{report.review_id}/findings.json" for report in reports
            ],
            gate_refs=self._gate_refs(),
            finding_count=len(findings),
            blocking_open_count=0,
        )
        proof_json_ref, proof_md_ref = self.store.write_proof(proof)
        appended = self.ledger.append(
            {
                "type": "closeout_completed",
                "task_id": task.id,
                "proof_refs": {
                    "json": proof_json_ref,
                    "md": proof_md_ref,
                },
                "finding_count": len(findings),
            }
        )
        status.phase = "done"
        status.ledger_head_hash = appended["hash"]
        save_status(status, self.paths)
        return proof

    def open_blocking_findings(self) -> list[Finding]:
        findings: list[Finding] = []
        for report in self.store.read_reports():
            findings.extend(report.open_blocking_findings())
        return findings

    def _checkpoint_refs(self) -> list[str]:
        refs: list[str] = []
        for record in self.ledger.read_all():
            if record.get("type") != "checkpoint":
                continue
            evidence_refs = record.get("evidence_refs", [])
            if isinstance(evidence_refs, list):
                refs.extend(str(ref) for ref in evidence_refs)
        return refs

    def _gate_refs(self) -> dict[str, str]:
        gate_dir = self.paths.evidence / "gates"
        if not gate_dir.exists():
            return {}
        return {
            gate_log.name.split("-", 1)[0]: f"gates/{gate_log.name}"
            for gate_log in sorted(gate_dir.glob("*.log"))
            if gate_log.is_file()
        }

    def _append_and_save_status(self, status: TaskStatus, record: dict[str, Any]) -> None:
        appended = self.ledger.append(record)
        status.ledger_head_hash = appended["hash"]
        save_status(status, self.paths)

    def _packet_path(self, review_id: str) -> Path:
        return self.paths.reviews / review_id / "packet.json"

    def _report_path(self, review_id: str) -> Path:
        return self.paths.reviews / review_id / "findings.json"

    def _validate_report_not_existing(self, review_id: str) -> None:
        if self._report_path(review_id).is_file():
            raise ValueError(f"review report already exists: {review_id}")

    def _validate_ingested_findings_are_open(self, findings: list[Finding]) -> None:
        for finding in findings:
            if finding.status != "open" or finding.resolution is not None:
                raise ValueError("ingested findings must be open and unresolved")

    def _validate_unique_finding_ids(self, findings: list[Finding]) -> None:
        incoming_ids = [finding.id for finding in findings]
        if len(set(incoming_ids)) != len(incoming_ids):
            raise ValueError("duplicate finding id in incoming findings")

        existing_ids: set[str] = set()
        for report in self.store.read_reports():
            existing_ids.update(finding.id for finding in report.findings)

        for finding_id in incoming_ids:
            if finding_id in existing_ids:
                raise ValueError(f"duplicate finding id: {finding_id}")


def _load_active_status(paths: AgosPaths) -> TaskStatus:
    status = load_status(paths)
    if status is None:
        raise ValueError("No active AGOS task found")
    return status
