"""Authoritative merge-gate verifier for AGOS governed tasks."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from agos.core.command import run_command
from agos.core.config import GateSpec, load_config, resolve_gates
from agos.core.execution import CandidateBundleDecision, CandidateMergePreview, CandidatePatch, ReviewBinding
from agos.core.execution_store import ExecutionStore
from agos.core.execution_workspace import candidate_patch_paths
from agos.core.gate import gates_match
from agos.core.ledger import Ledger
from agos.core.repo import AgosPaths
from agos.core.review import ReviewReport
from agos.core.status import load_status
from agos.core.task import Task, load_task
from agos.core.trust_anchor import (
    TrustAnchorPayload,
    TrustAnchorStore,
    verify_current_anchor,
)


CheckState = Literal["pass", "block"]


class MergeGateCheck(BaseModel):
    name: str
    state: CheckState
    message: str
    details: list[str] = Field(default_factory=list)


class MergeGateResult(BaseModel):
    passed: bool
    checks: list[MergeGateCheck]
    task_id: str | None = None
    anchor: TrustAnchorPayload | None = None


@dataclass(frozen=True)
class AppliedCandidateEvidence:
    candidate: CandidatePatch
    patch_ref: str


def verify_merge_gate(
    paths: AgosPaths,
    *,
    require_anchor: bool = False,
    anchor_store: TrustAnchorStore | None = None,
    allow_missing_review: bool = False,
    base_ref: str | None = None,
    head_ref: str | None = None,
) -> MergeGateResult:
    checks: list[MergeGateCheck] = []
    task: Task | None = None
    resolved_gates: list[GateSpec] = []
    records: list[dict] = []
    anchor: TrustAnchorPayload | None = None

    try:
        config = load_config(paths.root)
        task = load_task(paths.task_yaml)
        status = load_status(paths)
        if status is None:
            raise ValueError("current task status is missing")
        checks.append(MergeGateCheck(name="initialized", state="pass", message="AGOS task is active"))
    except Exception as exc:
        checks.append(MergeGateCheck(name="initialized", state="block", message=str(exc)))
        return _result(checks, task_id=None)

    ledger = Ledger(paths.ledger)
    try:
        ledger.verify_chain()
        records = ledger.read_all()
        checks.append(MergeGateCheck(name="ledger_chain", state="pass", message="ledger chain verified"))
    except Exception as exc:
        checks.append(MergeGateCheck(name="ledger_chain", state="block", message=str(exc)))

    try:
        resolved_gates = resolve_gates(config, task.workflow, override=task.gates)
        locked_records = [record for record in records if record.get("type") == "gates_locked"]
        locked = locked_records[-1].get("gates", []) if locked_records else []
        if not locked_records:
            raise ValueError("missing gates_locked record")
        if not gates_match(locked, resolved_gates):
            raise ValueError("current gate config does not match gates_locked")
        checks.append(MergeGateCheck(name="gates_locked", state="pass", message="gate lock matches config"))
    except Exception as exc:
        checks.append(MergeGateCheck(name="gates_locked", state="block", message=str(exc)))

    if require_anchor:
        if anchor_store is None:
            checks.append(
                MergeGateCheck(
                    name="trust_anchor",
                    state="block",
                    message="trust anchor is required but no store was provided",
                )
            )
        else:
            verification = verify_current_anchor(paths, anchor_store)
            anchor = verification.anchor
            checks.append(
                MergeGateCheck(
                    name="trust_anchor",
                    state="pass" if verification.passed else "block",
                    message="trust anchor verified" if verification.passed else "trust anchor verification failed",
                    details=list(verification.issues),
                )
            )
    else:
        checks.append(
            MergeGateCheck(name="trust_anchor", state="pass", message="trust anchor not required")
        )

    store = ExecutionStore(paths)
    try:
        candidates = store.read_candidates()
        patch_issues = _candidate_patch_issues(store, candidates, records)
        checks.append(
            MergeGateCheck(
                name="candidate_patch_hashes",
                state="block" if patch_issues else "pass",
                message="candidate patch hash verification failed"
                if patch_issues
                else "candidate patch hashes verified",
                details=patch_issues,
            )
        )
        status_issues = _candidate_status_issues(candidates)
        checks.append(
            MergeGateCheck(
                name="candidate_status",
                state="block" if status_issues else "pass",
                message="candidate status verification failed"
                if status_issues
                else "candidate statuses verified",
                details=status_issues,
            )
        )
        evidence_issues, evidence_warnings = _candidate_evidence_issues(
            store,
            candidates,
            required_gate_ids=[gate.id for gate in resolved_gates],
            allow_missing_review=allow_missing_review,
            paths=paths,
            ledger_records_by_hash={
                str(record["hash"]): record for record in records if record.get("hash")
            },
            allow_fake_reviewer=config.allow_fake_reviewer,
        )
        checks.append(
            MergeGateCheck(
                name="candidate_evidence",
                state="block" if evidence_issues else "pass",
                message="candidate evidence verification failed"
                if evidence_issues
                else "candidate evidence verified",
                details=evidence_issues + evidence_warnings,
            )
        )
        arbitration_issues = _merge_arbitration_issues(store, records)
        checks.append(
            MergeGateCheck(
                name="merge_arbitration",
                state="block" if arbitration_issues else "pass",
                message="merge arbitration verification failed"
                if arbitration_issues
                else "merge arbitration verified",
                details=arbitration_issues,
            )
        )
        submitted_diff_issues = _submitted_diff_issues(
            paths,
            store,
            records,
            candidates,
            base_ref=base_ref,
            head_ref=head_ref,
        )
        checks.append(
            MergeGateCheck(
                name="submitted_diff",
                state="block" if submitted_diff_issues else "pass",
                message="submitted diff verification failed"
                if submitted_diff_issues
                else (
                    "submitted diff verified"
                    if base_ref and head_ref
                    else "submitted diff not checked"
                ),
                details=submitted_diff_issues,
            )
        )
    except Exception as exc:
        checks.append(MergeGateCheck(name="candidate_patch_hashes", state="block", message=str(exc)))
        checks.append(MergeGateCheck(name="candidate_evidence", state="block", message=str(exc)))

    return _result(checks, task_id=task.id, anchor=anchor)


def _candidate_patch_issues(
    store: ExecutionStore,
    candidates: list[CandidatePatch],
    records: list[dict],
) -> list[str]:
    issues: list[str] = []
    created_records_by_candidate: dict[str, list[dict]] = {}
    for record in records:
        if record.get("type") != "candidate_patch_created" or not record.get("candidate_id"):
            continue
        created_records_by_candidate.setdefault(str(record["candidate_id"]), []).append(record)

    for candidate in candidates:
        try:
            created_records = created_records_by_candidate.get(candidate.id, [])
            if not created_records:
                issues.append(f"{candidate.id}: candidate patch creation ledger record is missing")
                continue
            if len(created_records) > 1:
                issues.append(f"{candidate.id}: multiple candidate_patch_created ledger records found")
                continue
            created = created_records[0]
            if created.get("patch_ref") != candidate.patch_ref:
                issues.append(
                    f"{candidate.id}: candidate patch ref does not match candidate_patch_created ledger"
                )
                continue
            if created.get("patch_sha256") != candidate.patch_sha256:
                issues.append(
                    f"{candidate.id}: candidate patch hash does not match candidate_patch_created ledger"
                )
                continue
            path = store.patch_path(candidate.patch_ref)
            if not path.is_file():
                issues.append(f"{candidate.id}: candidate patch file not found: {candidate.patch_ref}")
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != candidate.patch_sha256:
                issues.append(f"{candidate.id}: candidate patch hash mismatch")
            if actual != created.get("patch_sha256"):
                issues.append(
                    f"{candidate.id}: candidate patch file hash does not match candidate_patch_created ledger"
                )
        except Exception as exc:
            issues.append(f"{candidate.id}: {exc}")
    return issues


def _candidate_evidence_issues(
    store: ExecutionStore,
    candidates: list[CandidatePatch],
    *,
    required_gate_ids: list[str],
    allow_missing_review: bool,
    paths: AgosPaths,
    ledger_records_by_hash: dict[str, dict],
    allow_fake_reviewer: bool = False,
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    required = {"patch_applies", *required_gate_ids}
    for candidate in candidates:
        if candidate.status not in {"accepted", "applied"}:
            continue
        runs = store.read_test_runs(candidate.id)
        passed = {run.gate_id for run in runs if run.state == "passed"}
        missing = sorted(required - passed)
        if missing:
            issues.append(f"{candidate.id}: missing passed candidate tests: {', '.join(missing)}")
        completed_reviews = [binding for binding in candidate.review_refs if binding.state == "completed"]
        if allow_missing_review and not completed_reviews:
            continue
        review_issue = _clean_review_issue(
            candidate,
            paths=paths,
            ledger_records_by_hash=ledger_records_by_hash,
        )
        if review_issue is not None:
            issues.append(f"{candidate.id}: {review_issue}")
            continue
        block, warning = _dev_only_review_provenance(
            candidate,
            paths=paths,
            allow_fake_reviewer=allow_fake_reviewer,
        )
        if block is not None:
            issues.append(f"{candidate.id}: {block}")
        if warning is not None:
            warnings.append(f"{candidate.id}: {warning}")
    return issues, warnings


def _candidate_status_issues(candidates: list[CandidatePatch]) -> list[str]:
    issues: list[str] = []
    non_terminal = {"proposed", "testing", "reviewing", "tested", "reviewed"}
    for candidate in candidates:
        if candidate.status in non_terminal:
            issues.append(f"{candidate.id}: candidate status is not terminal: {candidate.status}")
    return issues


def _clean_review_issue(
    candidate: CandidatePatch,
    *,
    paths: AgosPaths | None = None,
    ledger_records_by_hash: dict[str, dict] | None = None,
) -> str | None:
    completed = [binding for binding in candidate.review_refs if binding.state == "completed"]
    if not completed:
        return "candidate requires a completed clean candidate-bound review"
    binding = completed[-1]
    if binding.report_ref is None:
        return "candidate-bound review is missing report_ref"
    if binding.patch_sha256 != candidate.patch_sha256:
        return "candidate-bound review patch hash is stale"
    if binding.base_commit != candidate.base_commit:
        return "candidate-bound review base commit is stale"
    if binding.test_refs != candidate.test_refs:
        return "candidate-bound review test_refs are stale"
    if binding.open_blocking_count != 0:
        return "candidate-bound review has open blocking findings"
    if paths is None:
        return None
    if binding.ledger_head_at_completion is None:
        return "candidate-bound review is missing completion ledger hash"
    if ledger_records_by_hash is not None:
        completion_record = ledger_records_by_hash.get(binding.ledger_head_at_completion)
        if completion_record is None:
            return "candidate-bound review completion ledger hash is not present in ledger"
        if (
            completion_record.get("type") != "candidate_review_completed"
            or completion_record.get("candidate_id") != candidate.id
            or completion_record.get("review_id") != binding.review_id
            or completion_record.get("report_ref") != binding.report_ref
        ):
            return "candidate-bound review completion ledger record is stale"
    return _review_report_issue(candidate, binding_report_ref=binding.report_ref, paths=paths)


def _review_report_issue(
    candidate: CandidatePatch,
    *,
    binding_report_ref: str,
    paths: AgosPaths,
) -> str | None:
    binding = [item for item in candidate.review_refs if item.state == "completed"][-1]
    try:
        report_path = _task_ref_path(paths, binding_report_ref)
    except ValueError as exc:
        return f"candidate-bound review report ref is invalid: {exc}"
    if not report_path.is_file():
        return f"candidate-bound review report not found: {binding_report_ref}"
    try:
        report = ReviewReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"candidate-bound review report is unreadable: {exc}"
    if report.review_id != binding.review_id:
        return "candidate-bound review report review_id mismatch"
    if report.task_id != candidate.task_id:
        return "candidate-bound review report task_id mismatch"
    if report.packet_ref != binding.packet_ref:
        return "candidate-bound review report packet_ref mismatch"
    open_blocking_count = len(report.open_blocking_findings())
    if open_blocking_count != 0:
        return "candidate-bound review report has open blocking findings"
    if open_blocking_count != binding.open_blocking_count:
        return "candidate-bound review open blocking count is stale"
    return None


def _dev_only_review_provenance(
    candidate: CandidatePatch,
    *,
    paths: AgosPaths,
    allow_fake_reviewer: bool,
) -> tuple[str | None, str | None]:
    """Inspect completed review raw outputs for a dev_only provenance marker.

    fake.py stamps ``dev_only: true`` into its raw output; production reviewers
    do not. Returns ``(block_issue, warning)``: block when a dev-only review is
    not explicitly allowed, otherwise a non-blocking warning. Raw outputs that
    are missing or unreadable are treated as non-dev-only; the reviewer registry
    is the primary defense against fake reviewers reaching production.
    """
    completed = [binding for binding in candidate.review_refs if binding.state == "completed"]
    if not completed:
        return None, None
    binding = completed[-1]
    if not _binding_is_dev_only(binding, paths=paths):
        return None, None
    if allow_fake_reviewer:
        return None, "candidate reviewed by dev-only reviewer (allow_fake_reviewer=true)"
    return "candidate reviewed by non-production reviewer", None


def _binding_is_dev_only(binding: ReviewBinding, *, paths: AgosPaths) -> bool:
    for ref in binding.raw_refs:
        try:
            path = _task_ref_path(paths, ref)
        except ValueError:
            continue
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("dev_only") is True:
            return True
    return False


def _merge_arbitration_issues(store: ExecutionStore, records: list[dict]) -> list[str]:
    issues: list[str] = []
    for decision in _read_bundle_decisions(store):
        if decision.strategy == "manual_merge_required":
            issues.append(f"{decision.id}: manual merge required")
        if decision.conflict_candidate_ids:
            issues.append(
                f"{decision.id}: merge decision has conflicts: "
                f"{', '.join(decision.conflict_candidate_ids)}"
            )
    for preview in _read_merge_previews(store):
        if preview.state == "failed":
            issues.append(f"{preview.id}: failed merge preview")
    for record in records:
        if record.get("type") == "candidate_apply_blocked":
            issues.append(f"{record.get('candidate_id')}: candidate apply was blocked")
    return issues


def _submitted_diff_issues(
    paths: AgosPaths,
    store: ExecutionStore,
    records: list[dict],
    candidates: list[CandidatePatch],
    *,
    base_ref: str | None,
    head_ref: str | None,
) -> list[str]:
    if base_ref is None and head_ref is None:
        return []
    if not base_ref or not head_ref:
        return ["both base_ref and head_ref are required for submitted diff verification"]

    issues = _checkpoint_ancestry_issues(paths, records, head_ref)
    diff = _git_diff(paths, base_ref, head_ref)
    if diff is None:
        return [*issues, f"submitted diff could not be read for {base_ref}..{head_ref}"]
    if not diff.strip():
        return issues

    applied, applied_issues = _applied_candidates_in_ledger_order(candidates, records)
    if applied_issues:
        return [*issues, *applied_issues]
    if not applied:
        return [*issues, "submitted diff is non-empty but no applied candidates were recorded"]

    try:
        expected_chunks = [store.patch_path(item.patch_ref).read_bytes() for item in applied]
    except Exception as exc:
        return [*issues, f"applied candidate patch evidence could not be read: {exc}"]

    expected = b"".join(expected_chunks)
    expected_paths = set().union(*(candidate_patch_paths(chunk) for chunk in expected_chunks))
    submitted_paths = candidate_patch_paths(diff)
    if submitted_paths != expected_paths or hashlib.sha256(diff).hexdigest() != hashlib.sha256(expected).hexdigest():
        issues.append("submitted diff does not match applied candidate evidence")
    return issues


def _checkpoint_ancestry_issues(paths: AgosPaths, records: list[dict], head_ref: str) -> list[str]:
    issues: list[str] = []
    for record in records:
        if record.get("type") != "checkpoint" or not record.get("repo_head"):
            continue
        checkpoint_head = str(record["repo_head"])
        proc = run_command(
            ["git", "merge-base", "--is-ancestor", checkpoint_head, head_ref],
            cwd=paths.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            issues.append(f"checkpoint repo head {checkpoint_head} is not an ancestor of {head_ref}")
    return issues


def _git_diff(paths: AgosPaths, base_ref: str, head_ref: str) -> bytes | None:
    proc = run_command(
        ["git", "diff", "--binary", f"{base_ref}..{head_ref}"],
        cwd=paths.root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout if isinstance(proc.stdout, bytes) else proc.stdout.encode()


def _applied_candidates_in_ledger_order(
    candidates: list[CandidatePatch],
    records: list[dict],
) -> tuple[list[AppliedCandidateEvidence], list[str]]:
    by_id = {candidate.id: candidate for candidate in candidates}
    ordered: list[AppliedCandidateEvidence] = []
    issues: list[str] = []
    apply_refs: dict[str, str] = {}
    duplicate_apply_ids: set[str] = set()
    bundled_ids = {
        str(candidate_id)
        for record in records
        if record.get("type") == "candidate_bundle_applied"
        and isinstance(record.get("candidate_ids"), list)
        for candidate_id in record["candidate_ids"]
    }

    for record in records:
        if record.get("type") != "candidate_applied" or not record.get("candidate_id"):
            continue
        candidate_id = str(record["candidate_id"])
        patch_ref = record.get("patch_ref")
        if patch_ref is None:
            issues.append(f"{candidate_id}: candidate_applied record is missing patch_ref")
            continue
        if candidate_id in apply_refs:
            duplicate_apply_ids.add(candidate_id)
            continue
        apply_refs[candidate_id] = str(patch_ref)

    for candidate_id in sorted(duplicate_apply_ids):
        issues.append(f"{candidate_id}: multiple candidate_applied ledger records found")

    seen: set[str] = set()
    for record in records:
        candidate_ids: list[str] = []
        if record.get("type") == "candidate_applied" and record.get("candidate_id"):
            candidate_id = str(record["candidate_id"])
            if candidate_id not in bundled_ids:
                candidate_ids = [candidate_id]
        elif record.get("type") == "candidate_bundle_applied" and isinstance(record.get("candidate_ids"), list):
            candidate_ids = [str(candidate_id) for candidate_id in record["candidate_ids"]]
        for candidate_id in candidate_ids:
            if candidate_id in seen:
                continue
            candidate = by_id.get(candidate_id)
            if candidate is None:
                issues.append(f"{candidate_id}: applied candidate is missing from candidate store")
                continue
            if candidate.status != "applied":
                issues.append(f"{candidate_id}: applied candidate status is not applied")
                continue
            ledger_patch_ref = apply_refs.get(candidate_id)
            if ledger_patch_ref is None:
                issues.append(f"{candidate_id}: applied candidate is missing candidate_applied evidence")
                continue
            if ledger_patch_ref != candidate.patch_ref:
                issues.append(f"{candidate_id}: applied candidate patch_ref does not match ledger")
                continue
            ordered.append(AppliedCandidateEvidence(candidate=candidate, patch_ref=ledger_patch_ref))
            seen.add(candidate_id)
    return ordered, issues


def _read_bundle_decisions(store: ExecutionStore) -> list[CandidateBundleDecision]:
    directory = store.execution_dir / "bundle_decisions"
    if not directory.exists():
        return []
    return [
        CandidateBundleDecision.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def _read_merge_previews(store: ExecutionStore) -> list[CandidateMergePreview]:
    directory = store.execution_dir / "merge_previews"
    if not directory.exists():
        return []
    return [
        CandidateMergePreview.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
    ]


def _task_ref_path(paths: AgosPaths, ref: str):
    relative = PurePosixPath(ref)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(ref)
    return paths.current_task.joinpath(*relative.parts)


def _result(
    checks: list[MergeGateCheck],
    *,
    task_id: str | None,
    anchor: TrustAnchorPayload | None = None,
) -> MergeGateResult:
    return MergeGateResult(
        passed=all(check.state == "pass" for check in checks),
        checks=checks,
        task_id=task_id,
        anchor=anchor,
    )
