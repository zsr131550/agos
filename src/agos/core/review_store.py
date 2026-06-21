"""Storage helpers for review artifacts."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath

from agos.core.repo import AgosPaths
from agos.core.review import ReviewPacket, ReviewReport


class ReviewStore:
    def __init__(self, paths: AgosPaths):
        self.paths = paths

    def write_packet(self, packet: ReviewPacket) -> str:
        ref = self._review_ref(packet.review_id, "packet.json")
        self._write_model(self.paths.current_task / ref, packet)
        return ref

    def write_raw_output(self, review_id: str, reviewer: str, payload: dict) -> str:
        safe_reviewer = _safe_component(reviewer, "reviewer")
        ref = self._review_ref(review_id, "raw", f"{safe_reviewer}.json")
        path = self.paths.current_task / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return ref

    def write_report(self, report: ReviewReport) -> str:
        ref = self._review_ref(report.review_id, "findings.json")
        self._write_model(self.paths.current_task / ref, report)
        return ref

    def write_markdown_report(self, report: ReviewReport) -> str:
        ref = self._review_ref(report.review_id, "report.md")
        path = self.paths.current_task / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._render_markdown_report(report), encoding="utf-8")
        return ref

    def read_report(self, review_id: str) -> ReviewReport:
        path = self.paths.reviews / _safe_component(review_id, "review_id") / "findings.json"
        return ReviewReport.model_validate_json(path.read_text(encoding="utf-8"))

    def read_reports(self) -> list[ReviewReport]:
        if not self.paths.reviews.exists():
            return []
        return [
            self.read_report(review_dir.name)
            for review_dir in sorted(self.paths.reviews.iterdir())
            if review_dir.is_dir() and (review_dir / "findings.json").is_file()
        ]

    def _write_model(self, path: Path, model: ReviewPacket | ReviewReport) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def _review_ref(self, review_id: str, *parts: str) -> str:
        return "/".join(("reviews", _safe_component(review_id, "review_id"), *parts))

    def _render_markdown_report(self, report: ReviewReport) -> str:
        lines = [
            f"# Review {report.review_id}",
            "",
            f"Task: {report.task_id}",
            f"Packet: {report.packet_ref}",
            "",
        ]
        if not report.findings:
            lines.append("No findings.")
            return "\n".join(lines) + "\n"

        lines.append("## Findings")
        lines.append("")
        for finding in report.findings:
            lines.extend(
                [
                    f"### {finding.title}",
                    "",
                    f"- ID: {finding.id}",
                    f"- Source: {finding.source_agent}",
                    f"- Category: {finding.category}",
                    f"- Severity: {finding.severity}",
                    f"- Blocking: {finding.blocking}",
                    f"- Status: {finding.status}",
                    "",
                    finding.body,
                    "",
                ]
            )
        return "\n".join(lines)


def _safe_component(name: str, label: str) -> str:
    if not name:
        raise ValueError(f"{label} must be a non-empty path component")
    if "/" in name or "\\" in name:
        raise ValueError(f"{label} must not contain path separators")
    if any(char in name for char in '<>:"/\\|?*'):
        raise ValueError(f"{label} must not contain reserved filename characters")
    if name == "." or ".." in name:
        raise ValueError(f"{label} must not contain special path components")
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute():
        raise ValueError(f"{label} must not be an absolute path")
    return name
