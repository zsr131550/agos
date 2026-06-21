# AGOS Review Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build AGOS v0.2 Review orchestration: deterministic review packets, normalized findings, evidence-backed resolution, and closeout proof gating.

**Architecture:** Add a read-only Review layer above the existing task/ledger/evidence core. Review artifacts live under `.agos/tasks/current/reviews/`, state-changing actions append hash-chained ledger events, and CLI commands expose `review`, `resolve`, and `closeout`.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, JSON/YAML filesystem storage, existing AGOS Ledger/Evidence/Repo APIs, pytest.

---

## Scope

This plan implements the v0.2 Review layer only. It does not implement the v0.3 Execution layer, candidate patches, worker workspaces, or merge arbitration.

The implementation must preserve the current dependency direction:

```text
cli -> core
core must not import adapters or CLI modules
```

## File Structure

- Create `src/agos/core/review.py`
  - Pydantic models for review packets, findings, review reports, and closeout proof.
- Create `src/agos/core/review_store.py`
  - Filesystem writer/reader for `.agos/tasks/current/reviews/` and proof files.
- Create `src/agos/core/review_service.py`
  - Pure core service for packet creation, ingesting findings, resolving findings, and checking closeout readiness.
- Create `src/agos/cli/cmd_review.py`
  - `agos review --packet-only` and `agos review --ingest <file>`.
- Create `src/agos/cli/cmd_resolve.py`
  - `agos resolve <finding-id> --evidence <ref> --status <resolved|accepted-risk|false-positive>`.
- Create `src/agos/cli/cmd_closeout.py`
  - `agos closeout`, generating `proof.json` and `proof.md` only when blocking findings are closed.
- Modify `src/agos/cli/main.py`
  - Register the new CLI commands.
- Modify `src/agos/core/repo.py`
  - Add review/proof paths to `AgosPaths`.
- Create tests:
  - `tests/core/test_review.py`
  - `tests/core/test_review_store.py`
  - `tests/core/test_review_service.py`
  - `tests/cli/test_review.py`
  - `tests/cli/test_resolve.py`
  - `tests/cli/test_closeout.py`
- Modify docs:
  - `README.md` command list and v0.2 loop notes.

## Task 1: Review Models

**Files:**
- Create: `src/agos/core/review.py`
- Test: `tests/core/test_review.py`

- [ ] **Step 1: Write model tests first**

Create `tests/core/test_review.py`:

```python
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

    assert [finding.id for finding in report.open_blocking_findings()] == ["finding-01"]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
python -m pytest tests/core/test_review.py -q
```

Expected: failure with `ModuleNotFoundError: No module named 'agos.core.review'`.

- [ ] **Step 3: Implement review models**

Create `src/agos/core/review.py`:

```python
"""Review packet, finding, resolution, and proof models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


ReviewSeverity = Literal["low", "medium", "high", "critical"]
ReviewFindingStatus = Literal["open", "resolved", "accepted_risk", "false_positive", "superseded"]
ReviewResolutionStatus = Literal["resolved", "accepted_risk", "false_positive", "superseded"]
ReviewDiffKind = Literal["governed_repo_diff", "candidate_patch", "pr_diff"]


class FindingLocation(BaseModel):
    file: str
    line: int | None = None


class FindingResolution(BaseModel):
    status: ReviewResolutionStatus
    evidence_refs: list[str] = Field(default_factory=list)
    rationale: str
    approved_by: str | None = None

    @model_validator(mode="after")
    def _validate_resolution(self) -> "FindingResolution":
        if self.status == "resolved" and not self.evidence_refs:
            raise ValueError("resolved findings require at least one evidence ref")
        if self.status == "accepted_risk" and not self.approved_by:
            raise ValueError("accepted risk requires approved_by")
        if not self.rationale.strip():
            raise ValueError("resolution rationale must be non-empty")
        return self


class Finding(BaseModel):
    id: str
    review_id: str
    source_agent: str
    category: str
    severity: ReviewSeverity
    blocking: bool
    title: str
    body: str
    location: FindingLocation | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    suggested_fix: str | None = None
    status: ReviewFindingStatus = "open"
    resolution: FindingResolution | None = None

    @model_validator(mode="after")
    def _validate_open_resolution(self) -> "Finding":
        if self.status == "open" and self.resolution is not None:
            raise ValueError("open findings cannot have a resolution")
        if self.status != "open" and self.resolution is None:
            raise ValueError("closed findings require a resolution")
        return self

    def with_resolution(self, resolution: FindingResolution) -> "Finding":
        return self.model_copy(update={"status": resolution.status, "resolution": resolution})


class ReviewPacket(BaseModel):
    review_id: str
    task_id: str
    task_title: str
    task_intent: str = ""
    acceptance: list[str] = Field(default_factory=list)
    diff_kind: ReviewDiffKind
    diff_evidence_ref: str | None = None
    ledger_head_hash: str
    checkpoint_refs: list[str] = Field(default_factory=list)
    gate_refs: dict[str, str] = Field(default_factory=dict)


class ReviewReport(BaseModel):
    review_id: str
    task_id: str
    packet_ref: str
    findings: list[Finding] = Field(default_factory=list)

    def open_blocking_findings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.blocking and finding.status == "open"]


class CloseoutProof(BaseModel):
    task_id: str
    ledger_head_hash: str
    review_refs: list[str] = Field(default_factory=list)
    gate_refs: dict[str, str] = Field(default_factory=dict)
    finding_count: int
    blocking_open_count: int
```

- [ ] **Step 4: Run model tests**

Run:

```bash
python -m pytest tests/core/test_review.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/agos/core/review.py tests/core/test_review.py
git commit -m "feat: add review data models"
```

## Task 2: Review Paths and Store

**Files:**
- Modify: `src/agos/core/repo.py`
- Create: `src/agos/core/review_store.py`
- Test: `tests/core/test_review_store.py`

- [ ] **Step 1: Write store tests**

Create `tests/core/test_review_store.py`:

```python
from __future__ import annotations

import json

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
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
python -m pytest tests/core/test_review_store.py -q
```

Expected: failure because `AgosPaths.reviews` and `ReviewStore` do not exist.

- [ ] **Step 3: Add review/proof paths**

Modify `src/agos/core/repo.py`:

```python
@dataclass(frozen=True)
class AgosPaths:
    root: Path
    agos_dir: Path
    agos_yaml: Path
    repo_ledger: Path
    tasks: Path
    current_task: Path
    task_yaml: Path
    status_json: Path
    ledger: Path
    evidence: Path
    reviews: Path
    proof_json: Path
    proof_md: Path
    hooks: Path
```

Update `task_paths()` return value:

```python
return AgosPaths(
    root=repo_root,
    agos_dir=agos,
    agos_yaml=agos / "agos.yaml",
    repo_ledger=agos / "repo_ledger.jsonl",
    tasks=agos / "tasks",
    current_task=task_dir,
    task_yaml=task_dir / "task.yaml",
    status_json=task_dir / "status.json",
    ledger=task_dir / "ledger.jsonl",
    evidence=task_dir / "evidence",
    reviews=task_dir / "reviews",
    proof_json=task_dir / "proof.json",
    proof_md=task_dir / "proof.md",
    hooks=agos / "hooks",
)
```

- [ ] **Step 4: Implement ReviewStore**

Create `src/agos/core/review_store.py`:

```python
"""Filesystem storage for review artifacts."""
from __future__ import annotations

import json

from agos.core.repo import AgosPaths
from agos.core.review import ReviewPacket, ReviewReport


class ReviewStore:
    def __init__(self, paths: AgosPaths) -> None:
        self.paths = paths

    def _review_dir(self, review_id: str):
        return self.paths.reviews / review_id

    def write_packet(self, packet: ReviewPacket) -> str:
        path = self._review_dir(packet.review_id) / "packet.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")
        return f"reviews/{packet.review_id}/packet.json"

    def write_raw_output(self, review_id: str, reviewer: str, payload: dict) -> str:
        path = self._review_dir(review_id) / "raw" / f"{reviewer}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"reviews/{review_id}/raw/{reviewer}.json"

    def write_report(self, report: ReviewReport) -> str:
        path = self._review_dir(report.review_id) / "findings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return f"reviews/{report.review_id}/findings.json"

    def write_markdown_report(self, report: ReviewReport) -> str:
        path = self._review_dir(report.review_id) / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# Review {report.review_id}", ""]
        if not report.findings:
            lines.append("No findings.")
        for finding in report.findings:
            marker = "BLOCKING" if finding.blocking else "NON-BLOCKING"
            lines.extend(
                [
                    f"## {finding.id}: {finding.title}",
                    "",
                    f"- Severity: `{finding.severity}`",
                    f"- Status: `{finding.status}`",
                    f"- Type: `{marker}`",
                    "",
                    finding.body,
                    "",
                ]
            )
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return f"reviews/{report.review_id}/report.md"

    def read_report(self, review_id: str) -> ReviewReport:
        path = self._review_dir(review_id) / "findings.json"
        return ReviewReport.model_validate_json(path.read_text(encoding="utf-8"))

    def read_reports(self) -> list[ReviewReport]:
        if not self.paths.reviews.exists():
            return []
        reports = []
        for path in sorted(self.paths.reviews.glob("*/findings.json")):
            reports.append(ReviewReport.model_validate_json(path.read_text(encoding="utf-8")))
        return reports
```

- [ ] **Step 5: Run store tests and existing path tests**

Run:

```bash
python -m pytest tests/core/test_review_store.py tests/core/test_repo.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/agos/core/repo.py src/agos/core/review_store.py tests/core/test_review_store.py
git commit -m "feat: store review artifacts"
```

## Task 3: Review Service and Ledger Events

**Files:**
- Create: `src/agos/core/review_service.py`
- Test: `tests/core/test_review_service.py`

- [ ] **Step 1: Write service tests**

Create `tests/core/test_review_service.py`:

```python
from __future__ import annotations

import json

from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.review import Finding, FindingResolution
from agos.core.review_service import ReviewService
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


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


def _ledger_types(paths):
    return [json.loads(line)["type"] for line in paths.ledger.read_text(encoding="utf-8").splitlines()]


def test_create_packet_writes_packet_and_review_started(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)

    packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")

    assert packet_ref == f"reviews/{packet.review_id}/packet.json"
    assert packet.task_id == "agos-01"
    assert packet.task_title == "Review task"
    assert _ledger_types(paths)[-1] == "review_started"


def test_ingest_findings_writes_report_and_ledger_events(tmp_repo):
    paths = _active_task(tmp_repo)
    service = ReviewService(paths)
    _packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")
    finding = Finding(
        id="finding-01",
        review_id=packet.review_id,
        source_agent="security_reviewer",
        category="security",
        severity="high",
        blocking=True,
        title="Risk",
        body="Risk body.",
    )

    report_ref, report = service.ingest_findings(packet.review_id, [finding])

    assert report_ref == f"reviews/{packet.review_id}/findings.json"
    assert report.open_blocking_findings()[0].id == "finding-01"
    assert _ledger_types(paths)[-2:] == ["finding_opened", "review_completed"]


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
    assert _ledger_types(paths)[-1] == "finding_resolved"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run:

```bash
python -m pytest tests/core/test_review_service.py -q
```

Expected: failure because `agos.core.review_service` does not exist.

- [ ] **Step 3: Implement ReviewService**

Create `src/agos/core/review_service.py`:

```python
"""Review orchestration service for packets, findings, and resolutions."""
from __future__ import annotations

from collections.abc import Iterable

from ulid import ULID

from agos.core.ledger import Ledger
from agos.core.repo import AgosPaths
from agos.core.review import Finding, FindingResolution, ReviewPacket, ReviewReport
from agos.core.review_store import ReviewStore
from agos.core.status import load_status, save_status
from agos.core.task import load_task


def new_review_id() -> str:
    return f"review-{ULID()}"


class ReviewService:
    def __init__(self, paths: AgosPaths) -> None:
        self.paths = paths
        self.store = ReviewStore(paths)
        self.ledger = Ledger(paths.ledger)

    def create_packet(self, *, diff_kind: str, diff_evidence_ref: str | None = None) -> tuple[str, ReviewPacket]:
        task = load_task(self.paths.task_yaml)
        status = load_status(self.paths)
        if status is None:
            raise ValueError("No active AGOS task found")

        gate_refs = {}
        gate_dir = self.paths.evidence / "gates"
        if gate_dir.exists():
            for gate_log in sorted(gate_dir.glob("*.log")):
                gate_id = gate_log.name.split("-", 1)[0]
                gate_refs[gate_id] = f"gates/{gate_log.name}"

        packet = ReviewPacket(
            review_id=new_review_id(),
            task_id=task.id,
            task_title=task.title,
            task_intent=task.intent,
            acceptance=task.acceptance,
            diff_kind=diff_kind,
            diff_evidence_ref=diff_evidence_ref,
            ledger_head_hash=status.ledger_head_hash,
            checkpoint_refs=self._checkpoint_refs(),
            gate_refs=gate_refs,
        )
        packet_ref = self.store.write_packet(packet)
        record = self.ledger.append(
            {
                "type": "review_started",
                "review_id": packet.review_id,
                "task_id": task.id,
                "packet_ref": packet_ref,
            }
        )
        status.ledger_head_hash = record["hash"]
        save_status(status, self.paths)
        return packet_ref, packet

    def ingest_findings(self, review_id: str, findings: Iterable[Finding]) -> tuple[str, ReviewReport]:
        task = load_task(self.paths.task_yaml)
        normalized = [finding.model_copy(update={"review_id": review_id}) for finding in findings]
        report = ReviewReport(
            review_id=review_id,
            task_id=task.id,
            packet_ref=f"reviews/{review_id}/packet.json",
            findings=normalized,
        )
        report_ref = self.store.write_report(report)
        self.store.write_markdown_report(report)

        status = load_status(self.paths)
        if status is None:
            raise ValueError("No active AGOS task found")

        for finding in normalized:
            record = self.ledger.append(
                {
                    "type": "finding_opened",
                    "review_id": review_id,
                    "finding_id": finding.id,
                    "severity": finding.severity,
                    "blocking": finding.blocking,
                    "title": finding.title,
                    "evidence_refs": finding.evidence_refs,
                }
            )
            status.ledger_head_hash = record["hash"]

        record = self.ledger.append(
            {
                "type": "review_completed",
                "review_id": review_id,
                "task_id": task.id,
                "report_ref": report_ref,
                "open_blocking_count": len(report.open_blocking_findings()),
            }
        )
        status.ledger_head_hash = record["hash"]
        save_status(status, self.paths)
        return report_ref, report

    def resolve_finding(self, finding_id: str, resolution: FindingResolution) -> Finding:
        reports = self.store.read_reports()
        for report in reports:
            for index, finding in enumerate(report.findings):
                if finding.id != finding_id:
                    continue
                updated = finding.with_resolution(resolution)
                report.findings[index] = updated
                self.store.write_report(report)
                self.store.write_markdown_report(report)
                event_type = "finding_accepted_risk" if resolution.status == "accepted_risk" else "finding_resolved"
                status = load_status(self.paths)
                if status is None:
                    raise ValueError("No active AGOS task found")
                record = self.ledger.append(
                    {
                        "type": event_type,
                        "finding_id": finding_id,
                        "review_id": report.review_id,
                        "status": resolution.status,
                        "evidence_refs": resolution.evidence_refs,
                        "rationale": resolution.rationale,
                        "approved_by": resolution.approved_by,
                    }
                )
                status.ledger_head_hash = record["hash"]
                save_status(status, self.paths)
                return updated
        raise ValueError(f"finding not found: {finding_id}")

    def open_blocking_findings(self) -> list[Finding]:
        findings: list[Finding] = []
        for report in self.store.read_reports():
            findings.extend(report.open_blocking_findings())
        return findings

    def _checkpoint_refs(self) -> list[str]:
        refs: list[str] = []
        if not self.paths.ledger.exists():
            return refs
        for record in self.ledger.read_all():
            if record.get("type") == "checkpoint":
                refs.extend(record.get("evidence_refs", []))
        return refs
```

- [ ] **Step 4: Run service tests**

Run:

```bash
python -m pytest tests/core/test_review_service.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/agos/core/review_service.py tests/core/test_review_service.py
git commit -m "feat: orchestrate review findings"
```

## Task 4: `agos review` CLI

**Files:**
- Create: `src/agos/cli/cmd_review.py`
- Modify: `src/agos/cli/main.py`
- Test: `tests/cli/test_review.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/cli/test_review.py`:

```python
from __future__ import annotations

import json

from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task

runner = CliRunner()


def _active_task(tmp_repo):
    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-01",
        title="Review CLI task",
        gates=[],
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


def test_review_packet_only_writes_packet(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["review", "--packet-only"])

    assert result.exit_code == 0
    assert "reviews/" in result.stdout
    assert list(paths.reviews.glob("*/packet.json"))


def test_review_ingest_writes_findings(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    packet_result = runner.invoke(app, ["review", "--packet-only"])
    review_id = packet_result.stdout.strip().split("/")[1]
    ingest_path = tmp_repo / "findings.json"
    ingest_path.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "id": "finding-01",
                        "review_id": review_id,
                        "source_agent": "security_reviewer",
                        "category": "security",
                        "severity": "high",
                        "blocking": True,
                        "title": "Risk",
                        "body": "Risk body.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["review", "--ingest", str(ingest_path), "--review-id", review_id])

    assert result.exit_code == 0
    assert "finding-01" in result.stdout
    assert (paths.reviews / review_id / "findings.json").exists()
```

- [ ] **Step 2: Run CLI tests and confirm they fail**

Run:

```bash
python -m pytest tests/cli/test_review.py -q
```

Expected: failure because `review` command is not registered.

- [ ] **Step 3: Implement `cmd_review.py`**

Create `src/agos/cli/cmd_review.py`:

```python
"""`agos review` command."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review import Finding
from agos.core.review_service import ReviewService


def review_command(
    packet_only: bool = typer.Option(False, "--packet-only", help="Create a review packet and exit."),
    ingest: Path | None = typer.Option(None, "--ingest", help="Ingest normalized findings JSON."),
    review_id: str | None = typer.Option(None, "--review-id", help="Review id for ingested findings."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    service = ReviewService(repo_paths(repo_root))

    if packet_only:
        packet_ref, _packet = service.create_packet(diff_kind="governed_repo_diff")
        typer.echo(packet_ref)
        return

    if ingest is None:
        typer.echo("Use --packet-only or --ingest <file>", err=True)
        raise typer.Exit(code=2)

    if review_id is None:
        typer.echo("--review-id is required with --ingest", err=True)
        raise typer.Exit(code=2)

    payload = json.loads(ingest.read_text(encoding="utf-8"))
    findings = [Finding.model_validate(item) for item in payload.get("findings", [])]
    report_ref, report = service.ingest_findings(review_id, findings)
    typer.echo(report_ref)
    for finding in report.findings:
        typer.echo(f"{finding.id}: {finding.title}")
```

- [ ] **Step 4: Register command**

Modify `src/agos/cli/main.py`:

```python
from agos.cli.cmd_review import review_command
```

Register below existing commands:

```python
app.command("review")(review_command)
```

- [ ] **Step 5: Run review CLI tests**

Run:

```bash
python -m pytest tests/cli/test_review.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/agos/cli/cmd_review.py src/agos/cli/main.py tests/cli/test_review.py
git commit -m "feat: add review CLI"
```

## Task 5: `agos resolve` CLI

**Files:**
- Create: `src/agos/cli/cmd_resolve.py`
- Modify: `src/agos/cli/main.py`
- Test: `tests/cli/test_resolve.py`

- [ ] **Step 1: Write resolve CLI tests**

Create `tests/cli/test_resolve.py`:

```python
from __future__ import annotations

import json

from typer.testing import CliRunner

from agos.cli.main import app
from tests.cli.test_review import _active_task

runner = CliRunner()


def _open_finding(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    _active_task(tmp_repo)
    packet_result = runner.invoke(app, ["review", "--packet-only"])
    review_id = packet_result.stdout.strip().split("/")[1]
    ingest_path = tmp_repo / "findings.json"
    ingest_path.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "id": "finding-01",
                        "review_id": review_id,
                        "source_agent": "test_reviewer",
                        "category": "test",
                        "severity": "medium",
                        "blocking": True,
                        "title": "Missing test",
                        "body": "A test is missing.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runner.invoke(app, ["review", "--ingest", str(ingest_path), "--review-id", review_id])
    return review_id


def test_resolve_requires_evidence(monkeypatch, tmp_repo):
    _open_finding(monkeypatch, tmp_repo)

    result = runner.invoke(
        app,
        ["resolve", "finding-01", "--status", "resolved", "--rationale", "Fixed"],
    )

    assert result.exit_code == 1
    assert "evidence" in result.stderr.lower()


def test_resolve_finding_with_evidence(monkeypatch, tmp_repo):
    _open_finding(monkeypatch, tmp_repo)

    result = runner.invoke(
        app,
        [
            "resolve",
            "finding-01",
            "--status",
            "resolved",
            "--evidence",
            "gates/tests_pass.log",
            "--rationale",
            "Regression test added.",
        ],
    )

    assert result.exit_code == 0
    assert "finding-01 resolved" in result.stdout
```

- [ ] **Step 2: Run resolve tests and confirm they fail**

Run:

```bash
python -m pytest tests/cli/test_resolve.py -q
```

Expected: failure because `resolve` command is not registered.

- [ ] **Step 3: Implement `cmd_resolve.py`**

Create `src/agos/cli/cmd_resolve.py`:

```python
"""`agos resolve` command."""
from __future__ import annotations

import typer

from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review import FindingResolution
from agos.core.review_service import ReviewService


def resolve_command(
    finding_id: str,
    status: str = typer.Option(..., "--status", help="resolved, accepted-risk, false-positive, or superseded."),
    evidence: list[str] | None = typer.Option(None, "--evidence", help="Evidence ref supporting resolution."),
    rationale: str = typer.Option(..., "--rationale", help="Resolution rationale."),
    approved_by: str | None = typer.Option(None, "--approved-by", help="Required for accepted-risk."),
) -> None:
    mapped_status = status.replace("-", "_")
    try:
        repo_root = find_initialized_repo_root()
        resolution = FindingResolution(
            status=mapped_status,
            evidence_refs=evidence or [],
            rationale=rationale,
            approved_by=approved_by,
        )
        finding = ReviewService(repo_paths(repo_root)).resolve_finding(finding_id, resolution)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"{finding.id} {finding.status}")
```

- [ ] **Step 4: Register command**

Modify `src/agos/cli/main.py`:

```python
from agos.cli.cmd_resolve import resolve_command
```

Register below `review`:

```python
app.command("resolve")(resolve_command)
```

- [ ] **Step 5: Run resolve tests**

Run:

```bash
python -m pytest tests/cli/test_resolve.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/agos/cli/cmd_resolve.py src/agos/cli/main.py tests/cli/test_resolve.py
git commit -m "feat: resolve review findings"
```

## Task 6: Closeout Proof

**Files:**
- Extend: `src/agos/core/review_store.py`
- Extend: `src/agos/core/review_service.py`
- Create: `src/agos/cli/cmd_closeout.py`
- Modify: `src/agos/cli/main.py`
- Test: `tests/cli/test_closeout.py`

- [ ] **Step 1: Write closeout CLI tests**

Create `tests/cli/test_closeout.py`:

```python
from __future__ import annotations

import json

from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.repo import repo_paths
from tests.cli.test_resolve import _open_finding
from tests.cli.test_review import _active_task

runner = CliRunner()


def test_closeout_blocks_with_open_blocking_finding(monkeypatch, tmp_repo):
    _open_finding(monkeypatch, tmp_repo)

    result = runner.invoke(app, ["closeout"])

    assert result.exit_code == 1
    assert "open blocking findings" in result.stderr


def test_closeout_writes_proof_when_findings_are_closed(monkeypatch, tmp_repo):
    _open_finding(monkeypatch, tmp_repo)
    runner.invoke(
        app,
        [
            "resolve",
            "finding-01",
            "--status",
            "resolved",
            "--evidence",
            "gates/tests_pass.log",
            "--rationale",
            "Regression test added.",
        ],
    )
    paths = repo_paths(tmp_repo)

    result = runner.invoke(app, ["closeout"])

    assert result.exit_code == 0
    assert paths.proof_json.exists()
    assert paths.proof_md.exists()
    proof = json.loads(paths.proof_json.read_text(encoding="utf-8"))
    assert proof["blocking_open_count"] == 0
```

- [ ] **Step 2: Run closeout tests and confirm they fail**

Run:

```bash
python -m pytest tests/cli/test_closeout.py -q
```

Expected: failure because `closeout` command is not registered.

- [ ] **Step 3: Add proof writing to ReviewStore**

Append to `src/agos/core/review_store.py`:

```python
from agos.core.review import CloseoutProof
```

Add methods inside `ReviewStore`:

```python
    def write_proof(self, proof: CloseoutProof) -> tuple[str, str]:
        self.paths.proof_json.parent.mkdir(parents=True, exist_ok=True)
        self.paths.proof_json.write_text(proof.model_dump_json(indent=2), encoding="utf-8")
        lines = [
            f"# AGOS Proof for {proof.task_id}",
            "",
            f"- Ledger head: `{proof.ledger_head_hash}`",
            f"- Review refs: {len(proof.review_refs)}",
            f"- Finding count: {proof.finding_count}",
            f"- Open blocking findings: {proof.blocking_open_count}",
            "",
        ]
        self.paths.proof_md.write_text("\n".join(lines), encoding="utf-8")
        return "proof.json", "proof.md"
```

- [ ] **Step 4: Add closeout service**

Append to `ReviewService` in `src/agos/core/review_service.py`:

```python
from agos.core.review import CloseoutProof
```

Add method inside `ReviewService`:

```python
    def closeout(self) -> CloseoutProof:
        status = load_status(self.paths)
        if status is None:
            raise ValueError("No active AGOS task found")
        task = load_task(self.paths.task_yaml)
        reports = self.store.read_reports()
        all_findings = [finding for report in reports for finding in report.findings]
        open_blocking = [finding for finding in all_findings if finding.blocking and finding.status == "open"]
        if open_blocking:
            ids = ", ".join(finding.id for finding in open_blocking)
            raise ValueError(f"open blocking findings: {ids}")
        proof = CloseoutProof(
            task_id=task.id,
            ledger_head_hash=status.ledger_head_hash,
            review_refs=[f"reviews/{report.review_id}/findings.json" for report in reports],
            gate_refs={},
            finding_count=len(all_findings),
            blocking_open_count=0,
        )
        proof_json_ref, proof_md_ref = self.store.write_proof(proof)
        record = self.ledger.append(
            {
                "type": "closeout_completed",
                "task_id": task.id,
                "proof_refs": [proof_json_ref, proof_md_ref],
                "finding_count": proof.finding_count,
            }
        )
        status.phase = "done"
        status.ledger_head_hash = record["hash"]
        save_status(status, self.paths)
        return proof
```

- [ ] **Step 5: Implement `cmd_closeout.py`**

Create `src/agos/cli/cmd_closeout.py`:

```python
"""`agos closeout` command."""
from __future__ import annotations

import typer

from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.review_service import ReviewService


def closeout_command() -> None:
    try:
        repo_root = find_initialized_repo_root()
        proof = ReviewService(repo_paths(repo_root)).closeout()
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"proof.json written for {proof.task_id}")
```

- [ ] **Step 6: Register command**

Modify `src/agos/cli/main.py`:

```python
from agos.cli.cmd_closeout import closeout_command
```

Register below `resolve`:

```python
app.command("closeout")(closeout_command)
```

- [ ] **Step 7: Run closeout tests**

Run:

```bash
python -m pytest tests/cli/test_closeout.py -q
```

Expected: `2 passed`.

- [ ] **Step 8: Commit Task 6**

```bash
git add src/agos/core/review_store.py src/agos/core/review_service.py src/agos/cli/cmd_closeout.py src/agos/cli/main.py tests/cli/test_closeout.py
git commit -m "feat: close out reviewed tasks"
```

## Task 7: Documentation and Full Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README command list**

Modify the command block in `README.md`:

```text
agos init [--executor multica] [--agent "Lambda"]
agos start --title "..." [--intent "..."] [--workflow feature] [--gate tests_pass,...]
agos checkpoint [--follow] [--once]
agos review --packet-only
agos review --ingest findings.json --review-id review-...
agos resolve <finding-id> --status resolved --evidence <ref> --rationale "..."
agos closeout
agos ci --local --stage <pre-commit|pre-push>
agos task status
agos task clear --force
```

Add this paragraph under the v0.1 loop:

```markdown
The v0.2 review loop adds evidence-backed review findings:
`review --packet-only -> external or human review -> review --ingest -> resolve -> closeout`.
Blocking findings prevent closeout until they are resolved with evidence or explicitly accepted by a human.
```

- [ ] **Step 2: Run targeted tests**

Run:

```bash
python -m pytest tests/core/test_review.py tests/core/test_review_store.py tests/core/test_review_service.py tests/cli/test_review.py tests/cli/test_resolve.py tests/cli/test_closeout.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run full unit suite**

Run:

```bash
python -m pytest -q
```

Expected: all non-integration tests pass, with the existing integration test skipped unless `AGOS_INTEGRATION=1`.

- [ ] **Step 4: Run coverage gate**

Run:

```bash
python -m pytest --cov=agos --cov-report=term-missing -q
```

Expected: total coverage is at least `90%`.

- [ ] **Step 5: Run lint and compile checks**

Run:

```bash
python -m ruff check src tests
python -m compileall -q src tests
```

Expected: Ruff prints `All checks passed!`; compileall exits with code 0.

- [ ] **Step 6: Run opt-in Multica integration if available**

Run:

```bash
$env:AGOS_INTEGRATION='1'; $env:AGOS_INTEGRATION_AGENT='codex-gpt-5.4 xhigh'; python -m pytest tests\integration\test_round_trip.py -q -s
```

Expected: `1 passed` when the local Multica daemon and configured agent are reachable.

- [ ] **Step 7: Commit Task 7**

```bash
git add README.md
git commit -m "docs: document review orchestration loop"
```

## Final Verification Checklist

- [ ] `python -m pytest -q`
- [ ] `python -m pytest --cov=agos --cov-report=term-missing -q`
- [ ] `python -m ruff check src tests`
- [ ] `python -m compileall -q src tests`
- [ ] Opt-in integration test if Multica is reachable.
- [ ] `git status --short` shows only intentional files.

## Spec Coverage Review

- Review packet generation is covered by Task 3 and exposed by Task 4.
- Normalized findings are covered by Task 1, Task 3, and Task 4.
- Evidence-backed resolution is covered by Task 5.
- Closeout blocking behavior and proof generation are covered by Task 6.
- Ledgered review lifecycle events are covered by Task 3, Task 5, and Task 6.
- Execution-layer candidate patches are intentionally excluded from this v0.2 plan and should receive a separate v0.3 plan.
