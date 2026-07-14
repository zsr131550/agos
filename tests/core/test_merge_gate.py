from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from agos.core.adapter import ExecutorRun
from agos.core.config import AGOSConfig, GateSpec, WorkflowConfig
from agos.core.execution import (
    ArbiterDecision,
    CandidateBundleDecision,
    CandidateMergePreview,
    CandidatePatch,
    CandidateProvenance,
    CandidateTestRun,
    ReviewBinding,
)
from agos.core.execution_store import ExecutionStore
from agos.core.gate import gates_locked_payload
from agos.core.ledger import Ledger
from agos.core.merge_gate import _clean_review_issue, verify_merge_gate
from agos.core.provenance import CandidateAttestationPayload, sign_candidate_attestation
from agos.core.repo import repo_paths
from agos.core.review import Finding, ReviewReport
from agos.core.review_store import ReviewStore
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.core.trust_anchor import (
    FileTrustAnchorStore,
    SignedFileTrustAnchorStore,
    publish_current_anchor,
    publish_current_signed_anchor,
)


PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIJ1hsZ3v/VpguoRK9JLsLMREScVpezJpGXA7rAMcrn9g
-----END PRIVATE KEY-----
"""
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=
-----END PUBLIC KEY-----
"""


def _gate() -> GateSpec:
    return GateSpec(id="tests_pass", stage=["candidate"], argv=[sys.executable, "-c", "pass"])


def _write_active_task(
    tmp_repo: Path,
    *,
    gate: GateSpec | None = None,
    merge_gate: dict[str, object] | None = None,
) -> tuple[Task, object]:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    gates = [gate or _gate()]
    config = AGOSConfig(
        executor={"name": "multica", "agent": "Lambda"},
        default_workflow="feature",
        workflows={"feature": WorkflowConfig(gates=gates)},
        merge_gate=merge_gate or {},
    )
    config.save(paths.agos_yaml)
    task = Task(
        id="agos-task-01",
        title="Merge gate task",
        workflow="feature",
        gates=[item.id for item in gates],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    first = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    ledger.append({"type": "gates_locked", "task_id": task.id, "gates": gates_locked_payload(gates)})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-1"),
            ledger_head_hash=first["hash"],
        ),
        paths,
    )
    return task, paths


def _check(result, name: str):
    return next(check for check in result.checks if check.name == name)


def _write_candidate(
    paths,
    *,
    candidate_id: str = "candidate-01",
    status: str = "accepted",
    clean_review: bool = False,
    patch_bytes: bytes = b"diff --git a/README.md b/README.md\n",
    with_decision: bool = True,
    write_created_record: bool = True,
    created_patch_ref: str | None = None,
    provenance_source: str | None = None,
    attestation_ref: str | None = None,
    base_commit: str = "a" * 40,
) -> CandidatePatch:
    store = ExecutionStore(paths)
    patch_ref, patch_sha = store.write_candidate_patch(candidate_id, patch_bytes)
    created = None
    if write_created_record:
        created_payload = {
                "type": "candidate_patch_created",
                "task_id": "agos-task-01",
                "subtask_id": (
                    "subtask-01" if candidate_id == "candidate-01" else f"subtask-{candidate_id}"
                ),
                "candidate_id": candidate_id,
                "patch_ref": created_patch_ref or patch_ref,
                "patch_sha256": patch_sha,
            }
        if provenance_source is not None:
            created_payload.update(
                {
                    "provenance_source": provenance_source,
                    "source_agent": "local",
                    "workspace_ref": (
                        "execution/workspaces/subtask-01.json"
                        if candidate_id == "candidate-01"
                        else f"execution/workspaces/subtask-{candidate_id}.json"
                    ),
                    "base_commit": base_commit,
                    "attestation_ref": attestation_ref,
                }
            )
        created = Ledger(paths.ledger).append(created_payload)
    test_refs = (
        ["execution/tests/test-patch.json", "execution/tests/test-gate.json"]
        if candidate_id == "candidate-01"
        else [
            f"execution/tests/{candidate_id}-patch.json",
            f"execution/tests/{candidate_id}-tests-pass.json",
        ]
    )
    review_refs: list[ReviewBinding] = []
    report_ref: str | None = None
    if clean_review:
        review_id = "review-01" if candidate_id == "candidate-01" else f"review-{candidate_id}"
        packet_ref = f"reviews/{review_id}/packet.json"
        report = ReviewReport(
            review_id=review_id,
            task_id="agos-task-01",
            packet_ref=packet_ref,
            findings=[],
        )
        report_ref = ReviewStore(paths).write_report(report)
        completed = Ledger(paths.ledger).append(
            {
                "type": "candidate_review_completed",
                "task_id": "agos-task-01",
                "candidate_id": candidate_id,
                "review_id": review_id,
                "report_ref": report_ref,
                "open_blocking_count": 0,
            }
        )
        review_refs.append(
            ReviewBinding(
                review_id=review_id,
                packet_ref=packet_ref,
                report_ref=report_ref,
                patch_sha256=patch_sha,
                base_commit=base_commit,
                test_refs=test_refs,
                state="completed",
                ledger_head_at_completion=completed["hash"],
                open_blocking_count=0,
            )
        )
    decision_ref = None
    if with_decision and status in {"accepted", "applied"}:
        evidence_refs = [patch_ref, *test_refs]
        if report_ref is not None:
            evidence_refs.append(report_ref)
        decision_ref = store.write_decision(
            ArbiterDecision(
                id=f"decision-{candidate_id}",
                candidate_id=candidate_id,
                decision="accepted",
                reason="Test fixture accepted candidate evidence.",
                evidence_refs=evidence_refs,
                decided_by="test_fixture",
            )
        )
    candidate = CandidatePatch(
        id=candidate_id,
        task_id="agos-task-01",
        subtask_id="subtask-01" if candidate_id == "candidate-01" else f"subtask-{candidate_id}",
        source_agent="local",
        workspace_ref=(
            "execution/workspaces/subtask-01.json"
            if candidate_id == "candidate-01"
            else f"execution/workspaces/subtask-{candidate_id}.json"
        ),
        patch_ref=patch_ref,
        patch_sha256=patch_sha,
        base_commit=base_commit,
        summary="Update README",
        status=status,  # type: ignore[arg-type]
        test_refs=test_refs,
        review_refs=review_refs,
        decision_ref=decision_ref,
        provenance=(
            CandidateProvenance(
                source=provenance_source,  # type: ignore[arg-type]
                ledger_head_hash=str(created["hash"]) if created is not None else None,
                attestation_ref=attestation_ref,
            )
            if provenance_source is not None
            else None
        ),
    )
    store.write_candidate(candidate)
    run_refs = (
        [("test-patch", "patch_applies", test_refs[0]), ("test-gate", "tests_pass", test_refs[1])]
        if candidate_id == "candidate-01"
        else [
            (f"{candidate_id}-patch", "patch_applies", test_refs[0]),
            (f"{candidate_id}-tests-pass", "tests_pass", test_refs[1]),
        ]
    )
    for run_id, gate_id, ref in run_refs:
        store.write_test_run(
            CandidateTestRun(
                id=run_id,
                candidate_id=candidate.id,
                gate_id=gate_id,
                state="passed",
                evidence_ref=ref,
                workspace_ref=candidate.workspace_ref,
            )
        )
    return candidate


def _append_candidate_applied(paths, candidate: CandidatePatch) -> None:
    Ledger(paths.ledger).append(
        {
            "type": "candidate_applied",
            "task_id": candidate.task_id,
            "candidate_id": candidate.id,
            "patch_ref": candidate.patch_ref,
            "decision_ref": candidate.decision_ref,
        }
    )


def _rewrite_candidate_decision(paths, candidate: CandidatePatch, **updates: object) -> None:
    assert candidate.decision_ref is not None
    decision_path = paths.current_task / candidate.decision_ref
    payload = json.loads(decision_path.read_text(encoding="utf-8"))
    payload.update(updates)
    decision_path.write_text(json.dumps(payload), encoding="utf-8")


def _rewrite_candidate_review(paths, candidate: CandidatePatch, **updates: object) -> CandidatePatch:
    binding = candidate.review_refs[-1].model_copy(update=updates)
    candidate = candidate.model_copy(update={"review_refs": [*candidate.review_refs[:-1], binding]})
    ExecutionStore(paths).write_candidate(candidate)
    return candidate


def _rewrite_review_report(paths, candidate: CandidatePatch, **updates: object) -> None:
    report_ref = candidate.review_refs[-1].report_ref
    assert report_ref is not None
    report_path = paths.current_task / report_ref
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload.update(updates)
    report_path.write_text(json.dumps(payload), encoding="utf-8")


def _publish_signed_test_anchor(paths) -> SignedFileTrustAnchorStore:
    private_key_path = paths.root.parent / "ci-private.pem"
    private_key_path.write_text(PRIVATE_KEY_PEM, encoding="ascii")
    public_key_path = paths.agos_dir / "keys" / "ci-public.pem"
    public_key_path.parent.mkdir(parents=True, exist_ok=True)
    public_key_path.write_text(PUBLIC_KEY_PEM, encoding="ascii")
    store = SignedFileTrustAnchorStore(paths.evidence / "signed-anchor.json")
    publish_current_signed_anchor(
        paths,
        store,
        issuer="protected-ci",
        key_id="ci-2026",
        private_key_path=private_key_path,
    )
    return store


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _git_diff_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "diff", "--binary", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout


def _commit(repo: Path, message: str) -> None:
    paths = [path.name for path in repo.iterdir() if path.name not in {".git", ".agos"}]
    subprocess.run(["git", "add", "--", *paths], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _write_review_report(paths, report: ReviewReport) -> str:
    return ReviewStore(paths).write_report(report)


def test_merge_gate_passes_clean_ledger_without_execution_store(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)

    result = verify_merge_gate(paths)

    assert result.passed is True
    assert _check(result, "ledger_chain").state == "pass"
    assert _check(result, "candidate_evidence").state == "pass"


def test_merge_gate_blocks_tampered_ledger(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    lines = paths.ledger.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["title"] = "forged"
    lines[0] = json.dumps(record)
    paths.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "ledger_chain").state == "block"


def test_merge_gate_blocks_gate_lock_drift(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    config = AGOSConfig.load(paths.agos_yaml)
    config.workflows["feature"].gates[0] = GateSpec(
        id="tests_pass",
        stage=["candidate"],
        argv=[sys.executable, "-c", "raise SystemExit(1)"],
    )
    config.save(paths.agos_yaml)

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "gates_locked").state == "block"


def test_merge_gate_blocks_when_not_initialized(tmp_repo: Path):
    paths = repo_paths(tmp_repo)

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "initialized").state == "block"


def test_merge_gate_blocks_when_status_is_missing(monkeypatch, tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    monkeypatch.setattr("agos.core.merge_gate.load_status", lambda _paths: None)

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "initialized").state == "block"
    assert "current task status is missing" in _check(result, "initialized").message


def test_merge_gate_blocks_when_gates_locked_missing(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    records = [record for record in records if record["type"] != "gates_locked"]
    prev = ""
    rewritten = []
    from agos.core.ledger import compute_hash

    for index, record in enumerate(records, start=1):
        record["seq"] = index
        record["prev_hash"] = prev
        record["hash"] = compute_hash(prev, {key: value for key, value in record.items() if key != "hash"})
        prev = record["hash"]
        rewritten.append(json.dumps(record))
    paths.ledger.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "gates_locked").state == "block"


def test_merge_gate_blocks_required_anchor_mismatch(monkeypatch, tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    monkeypatch.setattr("agos.core.trust_anchor.git_head", lambda _root: "a" * 40)
    store = FileTrustAnchorStore(paths.evidence / "anchors.json")
    publish_current_anchor(paths, store, issuer="CI")
    Ledger(paths.ledger).append({"type": "checkpoint", "repo_head": "a" * 40})

    result = verify_merge_gate(paths, require_anchor=True, anchor_store=store)

    assert result.passed is False
    assert _check(result, "trust_anchor").state == "block"


def test_merge_gate_blocks_required_anchor_without_store(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)

    result = verify_merge_gate(paths, require_anchor=True)

    assert result.passed is False
    assert _check(result, "trust_anchor").state == "block"


def test_merge_gate_blocks_candidate_patch_hash_mismatch(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="proposed")
    store = ExecutionStore(paths)
    store.patch_path(candidate.patch_ref).write_bytes(b"tampered")

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "candidate_patch_hashes").state == "block"


def test_merge_gate_blocks_non_terminal_candidate_status(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="proposed", clean_review=False)

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "candidate_status").state == "block"
    assert "proposed" in "; ".join(_check(result, "candidate_status").details)


def test_merge_gate_blocks_applied_candidate_without_decision(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(
        paths,
        status="applied",
        clean_review=True,
        with_decision=False,
    )
    _append_candidate_applied(paths, candidate)

    result = verify_merge_gate(paths)

    assert _check(result, "candidate_decisions").state == "block"
    assert "missing decision_ref" in "; ".join(_check(result, "candidate_decisions").details)


def test_merge_gate_allows_explicit_legacy_decisionless_candidate(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(
        paths,
        status="applied",
        clean_review=True,
        with_decision=False,
    )
    _append_candidate_applied(paths, candidate)

    result = verify_merge_gate(paths, allow_legacy_decisionless=True)

    assert _check(result, "candidate_decisions").state == "pass"
    assert "legacy decisionless" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_rejected_candidate_decision(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    _rewrite_candidate_decision(paths, candidate, decision="rejected")

    result = verify_merge_gate(paths)

    assert _check(result, "candidate_decisions").state == "block"
    assert "decision is not accepted" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_decision_for_different_candidate(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    _rewrite_candidate_decision(paths, candidate, candidate_id="candidate-other")

    result = verify_merge_gate(paths)

    assert _check(result, "candidate_decisions").state == "block"
    assert "candidate_id mismatch" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_decision_missing_required_evidence(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    _rewrite_candidate_decision(paths, candidate, evidence_refs=[candidate.patch_ref])

    result = verify_merge_gate(paths)

    assert _check(result, "candidate_decisions").state == "block"
    assert "missing evidence refs" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_missing_decision_file(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    assert candidate.decision_ref is not None
    (paths.current_task / candidate.decision_ref).unlink()

    result = verify_merge_gate(paths)

    assert _check(result, "candidate_decisions").state == "block"
    assert "decision evidence not found" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_invalid_decision_ref_even_in_legacy_mode(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    ExecutionStore(paths).write_candidate(
        candidate.model_copy(update={"decision_ref": "../outside-decision.json"})
    )

    result = verify_merge_gate(paths, allow_legacy_decisionless=True)

    assert _check(result, "candidate_decisions").state == "block"
    assert "invalid decision_ref" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_unreadable_decision_even_in_legacy_mode(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    assert candidate.decision_ref is not None
    (paths.current_task / candidate.decision_ref).write_text("{", encoding="utf-8")

    result = verify_merge_gate(paths, allow_legacy_decisionless=True)

    assert _check(result, "candidate_decisions").state == "block"
    assert "decision evidence is unreadable" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_decision_ref_that_does_not_match_decision_id(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    _rewrite_candidate_decision(paths, candidate, id="decision-renamed")

    result = verify_merge_gate(paths)

    assert _check(result, "candidate_decisions").state == "block"
    assert "decision_ref does not match decision id" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_applied_event_with_stale_decision_ref(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="applied", clean_review=True)
    Ledger(paths.ledger).append(
        {
            "type": "candidate_applied",
            "task_id": candidate.task_id,
            "candidate_id": candidate.id,
            "patch_ref": candidate.patch_ref,
            "decision_ref": "execution/decisions/stale.json",
        }
    )

    result = verify_merge_gate(paths)

    assert _check(result, "candidate_decisions").state == "block"
    assert "candidate_applied decision_ref does not match" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_multiple_applied_decision_records(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="applied", clean_review=True)
    _append_candidate_applied(paths, candidate)
    _append_candidate_applied(paths, candidate)

    result = verify_merge_gate(paths)

    assert _check(result, "candidate_decisions").state == "block"
    assert "multiple candidate_applied decision records" in "; ".join(
        _check(result, "candidate_decisions").details
    )


def test_merge_gate_blocks_duplicate_candidate_patch_created_records(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="proposed")
    Ledger(paths.ledger).append(
        {
            "type": "candidate_patch_created",
            "task_id": candidate.task_id,
            "subtask_id": candidate.subtask_id,
            "candidate_id": candidate.id,
            "patch_ref": candidate.patch_ref,
            "patch_sha256": candidate.patch_sha256,
        }
    )

    result = verify_merge_gate(paths)

    assert _check(result, "candidate_patch_hashes").state == "block"
    assert "multiple candidate_patch_created" in "; ".join(
        _check(result, "candidate_patch_hashes").details
    )


def test_merge_gate_blocks_missing_candidate_patch_created_record(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="proposed", write_created_record=False)

    result = verify_merge_gate(paths)

    assert "candidate patch creation ledger record is missing" in "; ".join(
        _check(result, "candidate_patch_hashes").details
    )


def test_merge_gate_blocks_candidate_patch_ref_that_disagrees_with_ledger(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(
        paths,
        status="proposed",
        created_patch_ref="execution/candidates/other.patch",
    )

    result = verify_merge_gate(paths)

    assert "candidate patch ref does not match" in "; ".join(
        _check(result, "candidate_patch_hashes").details
    )


def test_clean_review_without_persisted_paths_accepts_current_binding(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="reviewed", clean_review=True)

    assert _clean_review_issue(candidate) is None


def test_merge_gate_blocks_review_without_completion_ledger_hash(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    _rewrite_candidate_review(paths, candidate, ledger_head_at_completion=None)

    result = verify_merge_gate(paths)

    assert "missing completion ledger hash" in "; ".join(
        _check(result, "candidate_evidence").details
    )


def test_merge_gate_blocks_invalid_review_report_ref(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    report_ref = "../outside-report.json"
    completed = Ledger(paths.ledger).append(
        {
            "type": "candidate_review_completed",
            "task_id": candidate.task_id,
            "candidate_id": candidate.id,
            "review_id": candidate.review_refs[-1].review_id,
            "report_ref": report_ref,
            "open_blocking_count": 0,
        }
    )
    _rewrite_candidate_review(
        paths,
        candidate,
        report_ref=report_ref,
        ledger_head_at_completion=completed["hash"],
    )

    result = verify_merge_gate(paths)

    assert "review report ref is invalid" in "; ".join(
        _check(result, "candidate_evidence").details
    )


def test_merge_gate_blocks_unreadable_review_report(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    report_ref = candidate.review_refs[-1].report_ref
    assert report_ref is not None
    (paths.current_task / report_ref).write_text("{", encoding="utf-8")

    result = verify_merge_gate(paths)

    assert "review report is unreadable" in "; ".join(
        _check(result, "candidate_evidence").details
    )


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"review_id": "review-other"}, "review_id mismatch"),
        ({"task_id": "task-other"}, "task_id mismatch"),
        ({"packet_ref": "reviews/other/packet.json"}, "packet_ref mismatch"),
    ],
)
def test_merge_gate_blocks_review_report_binding_mismatch(
    tmp_repo: Path,
    updates: dict[str, str],
    expected: str,
):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    _rewrite_review_report(paths, candidate, **updates)

    result = verify_merge_gate(paths)

    assert expected in "; ".join(_check(result, "candidate_evidence").details)


def test_merge_gate_blocks_missing_candidate_patch_file(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="proposed")
    store = ExecutionStore(paths)
    store.patch_path(candidate.patch_ref).unlink()

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "candidate_patch_hashes").state == "block"


def test_merge_gate_blocks_when_candidate_store_read_fails(monkeypatch, tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)

    def fail_read_candidates(*_args, **_kwargs):
        raise RuntimeError("candidate store unavailable")

    monkeypatch.setattr("agos.core.merge_gate.ExecutionStore.read_candidates", fail_read_candidates)

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "candidate_patch_hashes").state == "block"
    assert _check(result, "candidate_evidence").state == "block"
    assert "candidate store unavailable" in _check(result, "candidate_patch_hashes").message


def test_merge_gate_blocks_checkpoint_head_not_ancestor_of_submitted_head(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    base = _git(tmp_repo, "rev-parse", "HEAD")
    _git(tmp_repo, "checkout", "-q", "-b", "side")
    (tmp_repo / "side.txt").write_text("side\n", encoding="utf-8")
    _commit(tmp_repo, "side")
    side_head = _git(tmp_repo, "rev-parse", "HEAD")
    _git(tmp_repo, "checkout", "-q", "main")
    (tmp_repo / "main.txt").write_text("main\n", encoding="utf-8")
    _commit(tmp_repo, "main")
    head = _git(tmp_repo, "rev-parse", "HEAD")
    Ledger(paths.ledger).append({"type": "checkpoint", "repo_head": side_head})

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is False
    assert _check(result, "submitted_diff").state == "block"
    assert any("not an ancestor" in detail for detail in _check(result, "submitted_diff").details)


def test_merge_gate_blocks_submitted_diff_with_extra_manual_change(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    patch_bytes = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    ).stdout
    _git(tmp_repo, "checkout", "--", "README.md")
    candidate = _write_candidate(paths, status="applied", clean_review=True, patch_bytes=patch_bytes)
    _append_candidate_applied(paths, candidate)
    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    _commit(tmp_repo, "apply candidate")
    (tmp_repo / "extra.txt").write_text("manual\n", encoding="utf-8")
    _commit(tmp_repo, "manual extra")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is False
    assert _check(result, "submitted_diff").state == "block"
    assert any(
        "does not match applied candidate evidence" in detail
        for detail in _check(result, "submitted_diff").details
    )


def test_merge_gate_blocks_submitted_diff_when_applied_candidate_lacks_ledger_event(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    patch_bytes = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=tmp_repo,
        check=True,
        capture_output=True,
    ).stdout
    _write_candidate(paths, status="applied", clean_review=True, patch_bytes=patch_bytes)
    _commit(tmp_repo, "apply candidate")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is False
    assert _check(result, "submitted_diff").state == "block"
    assert any(
        "no applied candidates were recorded" in detail
        for detail in _check(result, "submitted_diff").details
    )


def test_merge_gate_blocks_submitted_diff_when_ledger_applied_candidate_state_is_not_applied(
    tmp_repo: Path,
):
    _task, paths = _write_active_task(tmp_repo)
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    patch_bytes = _git_diff_bytes(tmp_repo, "HEAD")
    _git(tmp_repo, "checkout", "--", "README.md")
    applied = _write_candidate(paths, status="applied", clean_review=True, patch_bytes=patch_bytes)
    _append_candidate_applied(paths, applied)
    stale = _write_candidate(
        paths,
        candidate_id="candidate-02",
        status="accepted",
        clean_review=True,
        patch_bytes=b"diff --git a/OTHER.md b/OTHER.md\n",
    )
    _append_candidate_applied(paths, stale)
    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    _commit(tmp_repo, "apply candidate")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is False
    assert _check(result, "submitted_diff").state == "block"
    assert any(
        "candidate-02: applied candidate status is not applied" in detail
        for detail in _check(result, "submitted_diff").details
    )


def test_merge_gate_blocks_submitted_diff_when_apply_event_patch_ref_mismatches_candidate(
    tmp_repo: Path,
):
    _task, paths = _write_active_task(tmp_repo)
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    patch_bytes = _git_diff_bytes(tmp_repo, "HEAD")
    candidate = _write_candidate(paths, status="applied", clean_review=True, patch_bytes=patch_bytes)
    Ledger(paths.ledger).append(
        {
            "type": "candidate_applied",
            "task_id": candidate.task_id,
            "candidate_id": candidate.id,
            "patch_ref": "evidence/candidate_patches/other.patch",
            "decision_ref": candidate.decision_ref,
        }
    )
    _commit(tmp_repo, "apply candidate")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is False
    assert _check(result, "submitted_diff").state == "block"
    assert any(
        "candidate-01: applied candidate patch_ref does not match ledger" in detail
        for detail in _check(result, "submitted_diff").details
    )


def test_merge_gate_blocks_submitted_diff_when_apply_event_missing_patch_ref(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    patch_bytes = _git_diff_bytes(tmp_repo, "HEAD")
    candidate = _write_candidate(paths, status="applied", clean_review=True, patch_bytes=patch_bytes)
    Ledger(paths.ledger).append(
        {
            "type": "candidate_applied",
            "task_id": candidate.task_id,
            "candidate_id": candidate.id,
            "decision_ref": candidate.decision_ref,
        }
    )
    _commit(tmp_repo, "apply candidate")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is False
    assert _check(result, "submitted_diff").state == "block"
    assert any(
        "candidate-01: candidate_applied record is missing patch_ref" in detail
        for detail in _check(result, "submitted_diff").details
    )


def test_merge_gate_blocks_candidate_patch_metadata_drift_from_creation_ledger(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# original candidate\n", encoding="utf-8")
    original_patch = _git_diff_bytes(tmp_repo, "HEAD")
    _git(tmp_repo, "checkout", "--", "README.md")
    candidate = _write_candidate(paths, status="applied", clean_review=True, patch_bytes=original_patch)

    (tmp_repo / "README.md").write_text("# submitted candidate\n", encoding="utf-8")
    submitted_patch = _git_diff_bytes(tmp_repo, "HEAD")
    submitted_sha = hashlib.sha256(submitted_patch).hexdigest()
    store = ExecutionStore(paths)
    store.patch_path(candidate.patch_ref).write_bytes(submitted_patch)
    binding = candidate.review_refs[-1].model_copy(update={"patch_sha256": submitted_sha})
    store.write_candidate(
        candidate.model_copy(update={"patch_sha256": submitted_sha, "review_refs": [binding]})
    )
    _append_candidate_applied(paths, candidate)
    _commit(tmp_repo, "apply candidate")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is False
    assert _check(result, "candidate_patch_hashes").state == "block"
    assert any(
        "candidate-01: candidate patch hash does not match candidate_patch_created ledger" in detail
        for detail in _check(result, "candidate_patch_hashes").details
    )


def test_merge_gate_passes_submitted_diff_bound_to_applied_candidate(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    patch_bytes = _git_diff_bytes(tmp_repo, "HEAD")
    candidate = _write_candidate(paths, status="applied", clean_review=True, patch_bytes=patch_bytes)
    _append_candidate_applied(paths, candidate)
    _commit(tmp_repo, "apply candidate")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is True
    assert _check(result, "submitted_diff").state == "pass"


def test_merge_gate_passes_submitted_diff_bound_to_applied_candidate_bundle(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    (tmp_repo / "notes.txt").write_text("old\n", encoding="utf-8")
    _commit(tmp_repo, "seed notes")
    base = _git(tmp_repo, "rev-parse", "HEAD")

    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    readme_patch = _git_diff_bytes(tmp_repo, "HEAD", "--", "README.md")
    _git(tmp_repo, "checkout", "--", "README.md")
    (tmp_repo / "notes.txt").write_text("new\n", encoding="utf-8")
    notes_patch = _git_diff_bytes(tmp_repo, "HEAD", "--", "notes.txt")
    _git(tmp_repo, "checkout", "--", "notes.txt")

    first = _write_candidate(
        paths,
        candidate_id="candidate-01",
        status="applied",
        clean_review=True,
        patch_bytes=readme_patch,
    )
    second = _write_candidate(
        paths,
        candidate_id="candidate-02",
        status="applied",
        clean_review=True,
        patch_bytes=notes_patch,
    )
    _append_candidate_applied(paths, first)
    _append_candidate_applied(paths, second)
    Ledger(paths.ledger).append(
        {
            "type": "candidate_bundle_applied",
            "task_id": first.task_id,
            "bundle_decision_id": "bundle-01",
            "candidate_ids": [first.id, second.id],
        }
    )
    (tmp_repo / "README.md").write_text("# changed\n", encoding="utf-8")
    (tmp_repo / "notes.txt").write_text("new\n", encoding="utf-8")
    _commit(tmp_repo, "apply candidate bundle")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is True
    assert _check(result, "submitted_diff").state == "pass"


def test_merge_gate_blocks_accepted_candidate_missing_review(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="accepted", clean_review=False)

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "candidate_evidence").state == "block"


def test_merge_gate_allows_missing_review_when_explicitly_allowed(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="accepted", clean_review=False)

    result = verify_merge_gate(paths, allow_missing_review=True)

    assert result.passed is True


def test_merge_gate_allow_missing_review_still_blocks_stale_completed_review(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    stale_binding = candidate.review_refs[-1].model_copy(update={"patch_sha256": "b" * 64})
    ExecutionStore(paths).write_candidate(candidate.model_copy(update={"review_refs": [stale_binding]}))

    result = verify_merge_gate(paths, allow_missing_review=True)

    assert result.passed is False
    assert "review patch hash is stale" in "; ".join(_check(result, "candidate_evidence").details)


def test_merge_gate_blocks_accepted_candidate_missing_required_gate(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="accepted", clean_review=True)
    test_path = paths.current_task / "execution" / "tests" / "test-gate.json"
    test_path.unlink()

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert "missing passed candidate tests" in "; ".join(_check(result, "candidate_evidence").details)


def test_merge_gate_passes_accepted_candidate_with_tests_and_clean_review(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="accepted", clean_review=True)

    result = verify_merge_gate(paths)

    assert result.passed is True
    assert _check(result, "candidate_evidence").state == "pass"


def _stamp_dev_only_review(paths) -> None:
    """Attach a dev_only raw output ref to the candidate's completed review."""
    candidate = ExecutionStore(paths).read_candidate("candidate-01")
    binding = candidate.review_refs[-1]
    raw_ref = ReviewStore(paths).write_raw_output(
        binding.review_id, "fake", {"dev_only": True, "findings": []}
    )
    updated_binding = binding.model_copy(update={"raw_refs": [raw_ref]})
    ExecutionStore(paths).write_candidate(
        candidate.model_copy(update={"review_refs": [updated_binding]})
    )


def test_merge_gate_blocks_dev_only_review_by_default(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="accepted", clean_review=True)
    _stamp_dev_only_review(paths)

    result = verify_merge_gate(paths)

    assert result.passed is False
    details = "; ".join(_check(result, "candidate_evidence").details)
    assert "non-production reviewer" in details


def test_merge_gate_allows_dev_only_review_with_flag(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="accepted", clean_review=True)
    _stamp_dev_only_review(paths)
    config = AGOSConfig.load(paths.agos_yaml).model_copy(update={"allow_fake_reviewer": True})
    config.save(paths.agos_yaml)

    result = verify_merge_gate(paths)

    assert result.passed is True
    evidence = _check(result, "candidate_evidence")
    assert evidence.state == "pass"
    assert "dev-only reviewer" in "; ".join(evidence.details)


def test_merge_gate_blocks_missing_review_report_file(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    report_ref = candidate.review_refs[-1].report_ref
    assert report_ref is not None
    (paths.current_task / report_ref).unlink()

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert "report not found" in "; ".join(_check(result, "candidate_evidence").details)


def test_merge_gate_blocks_review_report_with_open_blocker(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    binding = candidate.review_refs[-1]
    blocker = Finding(
        id="finding-01",
        review_id=binding.review_id,
        source_agent="security",
        category="security",
        severity="high",
        blocking=True,
        title="Blocking finding",
        body="The report contents should be authoritative.",
    )
    _write_review_report(
        paths,
        ReviewReport(
            review_id=binding.review_id,
            task_id=candidate.task_id,
            packet_ref=binding.packet_ref,
            findings=[blocker],
        ),
    )

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert "open blocking findings" in "; ".join(_check(result, "candidate_evidence").details)


def test_merge_gate_blocks_review_completion_hash_not_in_ledger(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    stale_binding = candidate.review_refs[-1].model_copy(
        update={"ledger_head_at_completion": "0" * 64}
    )
    ExecutionStore(paths).write_candidate(candidate.model_copy(update={"review_refs": [stale_binding]}))

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert "completion ledger hash is not present" in "; ".join(
        _check(result, "candidate_evidence").details
    )


def test_merge_gate_blocks_review_completion_hash_for_wrong_ledger_record(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    wrong_hash = Ledger(paths.ledger).read_all()[0]["hash"]
    stale_binding = candidate.review_refs[-1].model_copy(
        update={"ledger_head_at_completion": wrong_hash}
    )
    ExecutionStore(paths).write_candidate(candidate.model_copy(update={"review_refs": [stale_binding]}))

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert "completion ledger record is stale" in "; ".join(
        _check(result, "candidate_evidence").details
    )


def test_merge_gate_blocks_manual_merge_required_decision(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    ExecutionStore(paths).write_bundle_decision(
        CandidateBundleDecision(
            id="bundle-01",
            strategy="manual_merge_required",
            reason="overlapping candidates require a human merge",
        )
    )

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "merge_arbitration").state == "block"
    assert "manual merge required" in "; ".join(_check(result, "merge_arbitration").details)


def test_merge_gate_blocks_failed_merge_preview(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    ExecutionStore(paths).write_merge_preview(
        CandidateMergePreview(
            id="merge-preview-01",
            decision_id="bundle-01",
            strategy="ordered_patch_stack",
            candidate_ids=["candidate-01"],
            state="failed",
            conflict_evidence_refs=["execution/merge-preview-01.log"],
        )
    )

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert _check(result, "merge_arbitration").state == "block"
    assert "failed merge preview" in "; ".join(_check(result, "merge_arbitration").details)


def test_required_provenance_passes_worker_export_with_allowed_signed_anchor(tmp_repo: Path):
    _task, paths = _write_active_task(
        tmp_repo,
        merge_gate={
            "provenance_policy": "required",
            "trusted_signers": [
                {
                    "issuer": "protected-ci",
                    "key_id": "ci-2026",
                    "public_key_path": "keys/ci-public.pem",
                }
            ],
        },
    )
    candidate = _write_candidate(
        paths,
        status="applied",
        clean_review=True,
        provenance_source="worker_export",
    )
    _append_candidate_applied(paths, candidate)
    signed_store = _publish_signed_test_anchor(paths)

    result = verify_merge_gate(
        paths,
        signed_anchor_store=signed_store,
        trusted_config_path=paths.agos_yaml,
    )

    assert result.passed is True
    assert result.provenance_state == "proven"
    assert _check(result, "provenance").state == "pass"


@pytest.mark.parametrize("policy", ["required", "optional"])
def test_proven_provenance_binds_submitted_diff_to_accepted_candidate(
    tmp_repo: Path,
    policy: str,
):
    _task, paths = _write_active_task(
        tmp_repo,
        merge_gate={
            "provenance_policy": policy,
            "trusted_signers": [
                {
                    "issuer": "protected-ci",
                    "key_id": "ci-2026",
                    "public_key_path": "keys/ci-public.pem",
                }
            ],
        },
    )
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# accepted candidate\n", encoding="utf-8")
    patch_bytes = _git_diff_bytes(tmp_repo, "HEAD")
    _write_candidate(
        paths,
        status="accepted",
        clean_review=True,
        patch_bytes=patch_bytes,
        provenance_source="worker_export",
        base_commit=base,
    )
    _commit(tmp_repo, "accepted candidate subject")
    head = _git(tmp_repo, "rev-parse", "HEAD")
    signed_store = _publish_signed_test_anchor(paths)

    result = verify_merge_gate(
        paths,
        signed_anchor_store=signed_store,
        trusted_config_path=paths.agos_yaml,
        base_ref=base,
        head_ref=head,
    )

    assert result.passed is True
    assert result.provenance_state == "proven"
    assert _check(result, "submitted_diff").state == "pass"


def test_required_provenance_blocks_reconstructed_candidate(tmp_repo: Path):
    _task, paths = _write_active_task(
        tmp_repo,
        merge_gate={"provenance_policy": "required"},
    )
    _write_candidate(
        paths,
        status="tested",
        clean_review=False,
        with_decision=False,
        provenance_source="ci_reconstructed",
    )

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert result.provenance_state == "unprovenanced"
    assert "ci_reconstructed" in "; ".join(_check(result, "provenance").details)


def test_required_provenance_blocks_legacy_candidate_and_missing_signed_anchor(tmp_repo: Path):
    _task, paths = _write_active_task(
        tmp_repo,
        merge_gate={"provenance_policy": "required"},
    )
    _write_candidate(paths, status="accepted", clean_review=True)

    result = verify_merge_gate(paths)

    assert result.passed is False
    details = "; ".join(_check(result, "provenance").details)
    assert "legacy_unattested" in details
    assert "signed anchor" in details


def test_optional_provenance_keeps_strict_unsigned_candidate_compatible(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(
        paths,
        status="accepted",
        clean_review=True,
        provenance_source="worker_export",
    )

    result = verify_merge_gate(paths)

    assert result.passed is True
    assert result.provenance_state == "unprovenanced"
    assert _check(result, "provenance").state == "pass"


def test_optional_provenance_accepts_exact_reconstructed_diff_without_governance_claim(
    tmp_repo: Path,
):
    _task, paths = _write_active_task(tmp_repo)
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# reconstructed\n", encoding="utf-8")
    patch_bytes = _git_diff_bytes(tmp_repo, "HEAD")
    _write_candidate(
        paths,
        status="tested",
        clean_review=False,
        patch_bytes=patch_bytes,
        with_decision=False,
        provenance_source="ci_reconstructed",
        base_commit=base,
    )
    _commit(tmp_repo, "reconstructed subject")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is True
    assert result.provenance_state == "unprovenanced"
    assert _check(result, "candidate_evidence").state == "pass"
    assert _check(result, "submitted_diff").state == "pass"


def test_disabled_provenance_validates_reconstructed_diff_without_claim(tmp_repo: Path):
    _task, paths = _write_active_task(
        tmp_repo,
        merge_gate={"provenance_policy": "disabled"},
    )
    base = _git(tmp_repo, "rev-parse", "HEAD")
    (tmp_repo / "README.md").write_text("# disabled provenance\n", encoding="utf-8")
    patch_bytes = _git_diff_bytes(tmp_repo, "HEAD")
    _write_candidate(
        paths,
        status="tested",
        clean_review=False,
        patch_bytes=patch_bytes,
        with_decision=False,
        provenance_source="ci_reconstructed",
        base_commit=base,
    )
    _commit(tmp_repo, "disabled provenance subject")
    head = _git(tmp_repo, "rev-parse", "HEAD")

    result = verify_merge_gate(paths, base_ref=base, head_ref=head)

    assert result.passed is True
    assert result.provenance_state == "disabled"


def test_provenance_blocks_stale_candidate_creation_hash(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(
        paths,
        status="accepted",
        clean_review=True,
        provenance_source="worker_export",
    )
    assert candidate.provenance is not None
    ExecutionStore(paths).write_candidate(
        candidate.model_copy(
            update={
                "provenance": candidate.provenance.model_copy(
                    update={"ledger_head_hash": "f" * 64}
                )
            }
        )
    )

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert "creation ledger hash" in "; ".join(_check(result, "provenance").details)


def test_provenance_blocks_candidate_source_rewritten_after_creation(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(
        paths,
        status="accepted",
        clean_review=True,
        provenance_source="worker_export",
    )
    assert candidate.provenance is not None
    ExecutionStore(paths).write_candidate(
        candidate.model_copy(
            update={
                "provenance": candidate.provenance.model_copy(
                    update={"source": "legacy_unattested"}
                )
            }
        )
    )

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert "source does not match" in "; ".join(_check(result, "provenance").details)


def test_provenance_blocks_candidate_metadata_removed_after_creation(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(
        paths,
        status="accepted",
        clean_review=True,
        provenance_source="worker_export",
    )
    ExecutionStore(paths).write_candidate(candidate.model_copy(update={"provenance": None}))

    result = verify_merge_gate(paths)

    assert result.passed is False
    assert "metadata is missing" in "; ".join(_check(result, "provenance").details)


def test_provenance_blocks_invalid_external_attestation_in_optional_mode(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    _write_candidate(
        paths,
        status="accepted",
        clean_review=True,
        provenance_source="external_attested",
        attestation_ref="evidence/missing-attestation.json",
    )

    result = verify_merge_gate(paths, trusted_config_path=paths.agos_yaml)

    assert result.passed is False
    assert "attestation" in "; ".join(_check(result, "provenance").details)


def test_external_attestation_and_signed_anchor_produce_proven_state(tmp_repo: Path):
    _task, paths = _write_active_task(
        tmp_repo,
        merge_gate={
            "trusted_signers": [
                {
                    "issuer": "protected-ci",
                    "key_id": "ci-2026",
                    "public_key_path": "keys/ci-public.pem",
                }
            ]
        },
    )
    attestation_ref = "evidence/external-attestation.json"
    candidate = _write_candidate(
        paths,
        status="accepted",
        clean_review=True,
        provenance_source="external_attested",
        attestation_ref=attestation_ref,
    )
    signed_store = _publish_signed_test_anchor(paths)
    envelope = sign_candidate_attestation(
        CandidateAttestationPayload(
            candidate_id=candidate.id,
            patch_sha256=candidate.patch_sha256,
            base_commit=candidate.base_commit,
            source_agent=candidate.source_agent,
            created_at="2026-07-14T00:00:00Z",
        ),
        issuer="protected-ci",
        key_id="ci-2026",
        private_key_path=paths.root.parent / "ci-private.pem",
    )
    attestation_path = paths.current_task / attestation_ref
    attestation_path.parent.mkdir(parents=True, exist_ok=True)
    attestation_path.write_text(envelope.canonical_json(), encoding="utf-8")

    result = verify_merge_gate(
        paths,
        signed_anchor_store=signed_store,
        trusted_config_path=paths.agos_yaml,
    )

    assert result.passed is True
    assert result.provenance_state == "proven"


def test_optional_provenance_blocks_invalid_supplied_signed_anchor(tmp_repo: Path):
    _task, paths = _write_active_task(
        tmp_repo,
        merge_gate={
            "trusted_signers": [
                {
                    "issuer": "protected-ci",
                    "key_id": "ci-2026",
                    "public_key_path": "keys/ci-public.pem",
                }
            ]
        },
    )
    _write_candidate(
        paths,
        status="accepted",
        clean_review=True,
        provenance_source="worker_export",
    )
    signed_store = _publish_signed_test_anchor(paths)
    envelope = signed_store.read("agos-task-01")
    signed_store.write(
        envelope.model_copy(
            update={"signature": ("A" if envelope.signature[0] != "A" else "B") + envelope.signature[1:]}
        )
    )

    result = verify_merge_gate(
        paths,
        signed_anchor_store=signed_store,
        trusted_config_path=paths.agos_yaml,
    )

    assert result.passed is False
    assert "signature" in "; ".join(_check(result, "provenance").details)


def test_trusted_config_does_not_fall_back_to_subject_config(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    trusted_config = tmp_repo.parent / "trusted" / ".agos" / "agos.yaml"
    trusted_config.parent.mkdir(parents=True)
    trusted_config.write_text(paths.agos_yaml.read_text(encoding="utf-8"), encoding="utf-8")
    paths.agos_yaml.write_text("not: [valid", encoding="utf-8")

    result = verify_merge_gate(paths, trusted_config_path=trusted_config)

    assert result.passed is True
    assert result.provenance_state == "unprovenanced"


def test_trusted_config_rejects_subject_selected_weaker_workflow(tmp_repo: Path):
    task, paths = _write_active_task(tmp_repo)
    trusted_config = tmp_repo.parent / "trusted" / ".agos" / "agos.yaml"
    trusted_config.parent.mkdir(parents=True)
    config = AGOSConfig.load(paths.agos_yaml)
    config.workflows["docs_only"] = WorkflowConfig(gates=[])
    config.save(trusted_config)
    save_task(
        task.model_copy(update={"workflow": "docs_only", "gates": []}),
        paths.task_yaml,
    )
    paths.ledger.unlink()
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    ledger.append({"type": "gates_locked", "task_id": task.id, "gates": []})

    result = verify_merge_gate(paths, trusted_config_path=trusted_config)

    assert result.passed is False
    assert _check(result, "gates_locked").state == "block"
    assert "trusted default workflow" in _check(result, "gates_locked").message


def test_missing_trusted_config_blocks_without_subject_fallback(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)

    result = verify_merge_gate(
        paths,
        trusted_config_path=tmp_repo.parent / "missing" / "agos.yaml",
    )

    assert result.passed is False
    assert _check(result, "initialized").state == "block"


@pytest.mark.parametrize(
    ("binding_update", "message"),
    [
        ({"report_ref": None}, "candidate-bound review is missing report_ref"),
        ({"patch_sha256": "b" * 64}, "candidate-bound review patch hash is stale"),
        ({"base_commit": "b" * 40}, "candidate-bound review base commit is stale"),
        ({"test_refs": ["execution/tests/other.json"]}, "candidate-bound review test_refs are stale"),
        ({"open_blocking_count": 1}, "candidate-bound review has open blocking findings"),
    ],
)
def test_clean_review_issue_rejects_stale_completed_binding(
    tmp_repo: Path,
    binding_update: dict,
    message: str,
):
    _task, paths = _write_active_task(tmp_repo)
    candidate = _write_candidate(paths, status="accepted", clean_review=True)
    stale_binding = candidate.review_refs[-1].model_copy(update=binding_update)
    candidate = candidate.model_copy(update={"review_refs": [stale_binding]})

    assert _clean_review_issue(candidate) == message
