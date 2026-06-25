"""CLI-backed review adapter that produces normalized findings."""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from agos.adapters.workers.transport import run_worker_command
from agos.core.command import run_command
from agos.core.json_text import load_json_object_from_text
from agos.core.review import Finding, ReviewSeverity
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus, ReviewerStartRequest
from agos.core.review_store import ReviewStore


class LlmCliReviewerAdapter:
    """Run `codex` or `claude` as a synchronous reviewer."""

    def __init__(
        self,
        *,
        name: str,
        executor: str,
        command: str | None = None,
        role: str,
        timeout_seconds: int = 120,
        blocking_severity: ReviewSeverity = "high",
        review_store: ReviewStore | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.name = name
        self.executor = executor
        self.command = command
        self.role = role
        self.timeout_seconds = timeout_seconds
        self.blocking_severity = blocking_severity
        self.review_store = review_store
        self.cwd = cwd or (review_store.paths.current_task if review_store is not None else None)
        self._runs: dict[str, ReviewerRunStatus] = {}

    def start(self, request: ReviewerStartRequest) -> ReviewerRun:
        stdout = ""
        status: ReviewerRunStatus
        raw_ref: str | None = None
        try:
            proc = run_worker_command(
                self._args(_prompt(request)),
                action=f"{self.executor} reviewer",
                cwd=self.cwd,
                timeout_seconds=self.timeout_seconds,
                runner=run_command,
            )
            stdout = proc.stdout
            payload = load_json_object_from_text(stdout)
            if payload is None:
                raise ValueError("reviewer output did not contain JSON")
            findings, warnings = _normalize_findings(
                payload,
                review_id=request.packet.review_id,
                source_agent=self.name,
                blocking_severity=self.blocking_severity,
            )
            raw_ref = self._write_raw_output(
                request,
                payload={
                    "review_run_id": request.run_id,
                    "reviewer_id": request.reviewer_id,
                    "role": request.role,
                    "stdout": stdout,
                    "warnings": warnings,
                    "findings": [finding.model_dump(mode="python") for finding in findings],
                },
            )
            status = ReviewerRunStatus(
                backend=self.name,
                run_id=request.run_id,
                reviewer_id=request.reviewer_id,
                state="completed",
                findings=findings,
                raw_ref=raw_ref,
                detail="; ".join(warnings) if warnings else None,
            )
            self._runs[request.run_id] = status
            return ReviewerRun(
                backend=self.name,
                run_id=request.run_id,
                reviewer_id=request.reviewer_id,
                state="running",
            )
        except Exception as exc:
            raw_ref = self._write_raw_output(
                request,
                payload={
                    "review_run_id": request.run_id,
                    "reviewer_id": request.reviewer_id,
                    "role": request.role,
                    "stdout": stdout,
                    "error": str(exc),
                },
            )
            status = ReviewerRunStatus(
                backend=self.name,
                run_id=request.run_id,
                reviewer_id=request.reviewer_id,
                state="failed",
                raw_ref=raw_ref,
                detail=str(exc),
            )
            self._runs[request.run_id] = status
            return ReviewerRun(
                backend=self.name,
                run_id=request.run_id,
                reviewer_id=request.reviewer_id,
                state="failed",
            )

    def poll(self, run_id: str, *, reviewer_id: str) -> ReviewerRunStatus:
        return self._runs.get(
            run_id,
            ReviewerRunStatus(
                backend=self.name,
                run_id=run_id,
                reviewer_id=reviewer_id,
                state="failed",
                detail="unknown reviewer run",
            ),
        )

    def cancel(self, run_id: str) -> ReviewerRunStatus:
        previous = self._runs.get(run_id)
        status = ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id=previous.reviewer_id if previous else "unknown",
            state="cancelled",
            raw_ref=previous.raw_ref if previous else None,
        )
        self._runs[run_id] = status
        return status

    def _args(self, prompt: str) -> list[str]:
        command = self.command or _default_command(self.executor)
        if self.executor == "codex_cli":
            return [command, "exec", "--json", prompt]
        if self.executor == "claude_code":
            return [command, "-p", "--output-format", "json", prompt]
        raise ValueError(f"unsupported reviewer executor: {self.executor}")

    def _write_raw_output(self, request: ReviewerStartRequest, *, payload: dict[str, object]) -> str | None:
        if self.review_store is None:
            return None
        return self.review_store.write_raw_output(request.packet.review_id, self.name, payload)


def _default_command(executor: str) -> str:
    if executor == "codex_cli":
        return "codex"
    if executor == "claude_code":
        return "claude"
    raise ValueError(f"unsupported reviewer executor: {executor}")


def _normalize_findings(
    payload: dict[str, object],
    *,
    review_id: str,
    source_agent: str,
    blocking_severity: ReviewSeverity,
) -> tuple[list[Finding], list[str]]:
    findings_payload = payload.get("findings")
    if not isinstance(findings_payload, list):
        raise ValueError("reviewer output must include a findings array")

    findings: list[Finding] = []
    warnings: list[str] = []
    for index, raw_finding in enumerate(findings_payload, start=1):
        if not isinstance(raw_finding, dict):
            warnings.append(f"finding #{index} skipped: not an object")
            continue
        # The adapter owns review_id/source_agent; the CLI output omits them, so
        # inject defaults before validation (model_copy below overrides anyway).
        normalized = {**raw_finding, "review_id": review_id, "source_agent": source_agent}
        try:
            finding = Finding.model_validate(normalized)
        except ValidationError as exc:
            warnings.append(f"finding #{index} skipped: {exc.errors()[0]['msg']}")
            continue
        findings.append(
            finding.model_copy(
                update={
                    "review_id": review_id,
                    "source_agent": source_agent,
                    "status": "open",
                    "resolution": None,
                    "blocking": finding.blocking or _severity_rank(finding.severity)
                    >= _severity_rank(blocking_severity),
                }
            )
        )
    return findings, warnings


def _severity_rank(value: ReviewSeverity) -> int:
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}[value]


def _prompt(request: ReviewerStartRequest) -> str:
    packet = request.packet
    acceptance = "\n".join(f"- {item}" for item in packet.acceptance) or "- <none>"
    context_refs = "\n".join(f"- {ref}" for ref in packet.context_refs) or "- <none>"
    diff_text = _read_diff_text(request)
    return (
        "You are an AGOS reviewer. Return JSON only.\n\n"
        f"Review id: {packet.review_id}\n"
        f"Task title: {packet.task_title}\n"
        f"Task intent: {packet.task_intent or '<none>'}\n"
        f"Reviewer role: {request.role}\n"
        f"Acceptance:\n{acceptance}\n"
        f"Context refs:\n{context_refs}\n\n"
        f"Diff:\n{diff_text}\n\n"
        'Return exactly: {"findings":[{"id":"...","category":"...","severity":"low|medium|high|critical","blocking":true,"title":"...","body":"...","location":{"file":"...","line":1},"suggested_fix":"..."}]}\n'
        "If there are no findings, return {\"findings\":[]}. "
        "Do not add markdown or commentary."
    )


def _read_diff_text(request: ReviewerStartRequest) -> str:
    if request.packet.diff_evidence_ref is None:
        return "<no diff evidence>"
    if request.packet.diff_kind == "candidate_patch" and request.packet.diff_evidence_ref:
        if request.packet.diff_evidence_ref.startswith("execution/"):
            return request.packet.diff_evidence_ref
    if request.packet.diff_evidence_ref and request.packet.diff_evidence_ref.strip():
        if request.packet.diff_evidence_ref.startswith("reviews/"):
            return request.packet.diff_evidence_ref
    if request.packet.diff_evidence_ref:
        path = _current_task_path(request).joinpath(*request.packet.diff_evidence_ref.split("/"))
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return request.packet.diff_evidence_ref or "<unavailable diff evidence>"


def _current_task_path(request: ReviewerStartRequest) -> Path:
    if hasattr(request, "_review_store_paths"):
        return getattr(request, "_review_store_paths").current_task
    return Path.cwd()
