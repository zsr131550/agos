from __future__ import annotations

import json
import subprocess

from agos.adapters.reviewers.llm_cli import LlmCliReviewerAdapter
from agos.core.review import ReviewPacket
from agos.core.review_adapter import ReviewerStartRequest


def _packet() -> ReviewPacket:
    return ReviewPacket(
        review_id="review-01",
        task_id="agos-01",
        task_title="Title",
        task_intent="Intent",
        diff_kind="governed_repo_diff",
        ledger_head_hash="abc123",
    )


def _request() -> ReviewerStartRequest:
    return ReviewerStartRequest(
        run_id="review-run-01",
        reviewer_id="security",
        role="security_reviewer",
        packet=_packet(),
    )


def _completed(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["codex"], returncode=0, stdout=stdout, stderr="")


def _adapter(**overrides) -> LlmCliReviewerAdapter:
    kwargs: dict[str, object] = dict(
        name="security",
        executor="codex_cli",
        role="security_reviewer",
        timeout_seconds=5,
        blocking_severity="high",
    )
    kwargs.update(overrides)
    return LlmCliReviewerAdapter(**kwargs)  # type: ignore[arg-type]


def test_llm_cli_reviewer_parses_findings(monkeypatch):
    payload = {
        "findings": [
            {
                "id": "finding-01",
                "category": "security",
                "severity": "high",
                "blocking": True,
                "title": "Unsafe command",
                "body": "Shell injection risk.",
                "location": {"file": "src/app.py", "line": 10},
                "suggested_fix": "Use shlex.quote.",
            }
        ]
    }
    monkeypatch.setattr(
        "agos.adapters.reviewers.llm_cli.run_command",
        lambda args, **kwargs: _completed(json.dumps(payload)),
    )

    adapter = _adapter()
    run = adapter.start(_request())

    assert run.state == "running"
    status = adapter.poll(run.run_id, reviewer_id="security")
    assert status.state == "completed"
    assert len(status.findings) == 1
    finding = status.findings[0]
    assert finding.id == "finding-01"
    assert finding.review_id == "review-01"
    assert finding.source_agent == "security"
    assert finding.status == "open"
    assert finding.blocking is True


def test_llm_cli_reviewer_cli_failure_returns_failed(monkeypatch):
    monkeypatch.setattr(
        "agos.adapters.reviewers.llm_cli.run_command",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args, returncode=1, stdout="", stderr="boom"
        ),
    )

    adapter = _adapter()
    run = adapter.start(_request())

    assert run.state == "failed"
    status = adapter.poll(run.run_id, reviewer_id="security")
    assert status.state == "failed"
    assert status.detail is not None
    assert "failed" in status.detail


def test_llm_cli_reviewer_invalid_json_returns_failed(monkeypatch):
    monkeypatch.setattr(
        "agos.adapters.reviewers.llm_cli.run_command",
        lambda args, **kwargs: _completed("not json at all"),
    )

    adapter = _adapter()
    run = adapter.start(_request())

    assert run.state == "failed"
    status = adapter.poll(run.run_id, reviewer_id="security")
    assert status.state == "failed"


def test_llm_cli_reviewer_blocking_severity_override(monkeypatch):
    payload = {
        "findings": [
            {
                "id": "finding-01",
                "category": "style",
                "severity": "high",
                "blocking": False,
                "title": "Style issue",
                "body": "Bad naming.",
            }
        ]
    }
    monkeypatch.setattr(
        "agos.adapters.reviewers.llm_cli.run_command",
        lambda args, **kwargs: _completed(json.dumps(payload)),
    )

    adapter = _adapter(blocking_severity="medium")
    adapter.start(_request())
    status = adapter.poll("review-run-01", reviewer_id="security")

    assert status.state == "completed"
    # severity high >= threshold medium -> blocking forced True even though the
    # reviewer reported blocking=false.
    assert status.findings[0].blocking is True


def test_llm_cli_reviewer_partial_finding_failure_keeps_valid(monkeypatch):
    payload = {
        "findings": [
            {
                "id": "finding-01",
                "category": "security",
                "severity": "high",
                "blocking": True,
                "title": "Unsafe command",
                "body": "Shell injection risk.",
            },
            {"id": "broken"},  # missing required fields
            "not-a-dict",
        ]
    }
    monkeypatch.setattr(
        "agos.adapters.reviewers.llm_cli.run_command",
        lambda args, **kwargs: _completed(json.dumps(payload)),
    )

    adapter = _adapter()
    adapter.start(_request())
    status = adapter.poll("review-run-01", reviewer_id="security")

    assert status.state == "completed"
    assert len(status.findings) == 1
    assert status.findings[0].id == "finding-01"
    assert status.detail is not None
    assert "skipped" in status.detail


def test_llm_cli_reviewer_reads_candidate_patch_into_prompt(monkeypatch, tmp_path):
    # The reviewer must resolve the candidate patch ref against the task store
    # (the adapter's cwd) and embed the actual diff in its prompt, not the ref
    # string. Regression guard for the path-resolution defect.
    current_task = tmp_path
    patch_ref = "evidence/candidates/cand-01.patch"
    patch_path = current_task.joinpath(*patch_ref.split("/"))
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    diff_content = (
        "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-print('hi')\n+print('hello')\n"
    )
    patch_path.write_text(diff_content, encoding="utf-8")

    captured: dict[str, str] = {}

    def fake_run_command(args, **kwargs):
        captured["prompt"] = args[-1]
        return _completed(json.dumps({"findings": []}))

    monkeypatch.setattr("agos.adapters.reviewers.llm_cli.run_command", fake_run_command)

    adapter = _adapter(cwd=current_task)
    request = ReviewerStartRequest(
        run_id="review-run-01",
        reviewer_id="security",
        role="security_reviewer",
        packet=ReviewPacket(
            review_id="review-01",
            task_id="agos-01",
            task_title="Title",
            task_intent="Intent",
            diff_kind="candidate_patch",
            diff_evidence_ref=patch_ref,
            ledger_head_hash="abc123",
        ),
    )
    adapter.start(request)

    assert diff_content in captured["prompt"]
    # The bare ref string must not stand in for the missing diff.
    assert "Diff:\n" + patch_ref + "\n" not in captured["prompt"]


def test_llm_cli_reviewer_falls_back_to_ref_when_patch_missing(monkeypatch, tmp_path):
    # When the patch file cannot be resolved, degrade to the ref string rather
    # than crashing or silently dropping the diff section.
    captured: dict[str, str] = {}

    def fake_run_command(args, **kwargs):
        captured["prompt"] = args[-1]
        return _completed(json.dumps({"findings": []}))

    monkeypatch.setattr("agos.adapters.reviewers.llm_cli.run_command", fake_run_command)

    patch_ref = "evidence/candidates/missing.patch"
    adapter = _adapter(cwd=tmp_path)
    request = ReviewerStartRequest(
        run_id="review-run-01",
        reviewer_id="security",
        role="security_reviewer",
        packet=ReviewPacket(
            review_id="review-01",
            task_id="agos-01",
            task_title="Title",
            diff_kind="candidate_patch",
            diff_evidence_ref=patch_ref,
            ledger_head_hash="abc123",
        ),
    )
    adapter.start(request)

    assert patch_ref in captured["prompt"]
