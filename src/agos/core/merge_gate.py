"""Authoritative merge-gate verifier for AGOS governed tasks."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from agos.core.command import run_command
from agos.core.config import AGOSConfig, GateSpec, ProvenancePolicy, load_config, resolve_gates
from agos.core.execution import (
    ArbiterDecision,
    CandidateBundleDecision,
    CandidateMergePreview,
    CandidatePatch,
    ReviewBinding,
)
from agos.core.execution_store import ExecutionStore
from agos.core.execution_workspace import candidate_patch_paths
from agos.core.gate import gates_match
from agos.core.ledger import Ledger
from agos.core.merge_gate_provenance import (
    ProvenanceState,
    evaluate_candidate_provenance,
)
from agos.core.repo import AgosPaths
from agos.core.review import ReviewReport
from agos.core.status import (
    read_status_cache as load_status,
    repair_status_from_verified_records,
)
from agos.core.task import Task, load_task
from agos.core.trust_anchor import (
    TrustAnchorPayload,
    SignedTrustAnchorStore,
    TrustAnchorStore,
    verify_current_anchor,
    verify_current_signed_anchor,
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
    provenance_state: ProvenanceState = "unprovenanced"


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
    allow_legacy_decisionless: bool = False,
    trusted_config_path: Path | None = None,
    provenance_policy: ProvenancePolicy | None = None,
    signed_anchor_store: SignedTrustAnchorStore | None = None,
    base_ref: str | None = None,
    head_ref: str | None = None,
) -> MergeGateResult:
    checks: list[MergeGateCheck] = []
    task: Task | None = None
    resolved_gates: list[GateSpec] = []
    records: list[dict] = []
    anchor: TrustAnchorPayload | None = None
    provenance_state: ProvenanceState = (
        "disabled" if provenance_policy == "disabled" else "unprovenanced"
    )

    try:
        config_path = trusted_config_path or paths.agos_yaml
        config = (
            AGOSConfig.load(trusted_config_path)
            if trusted_config_path is not None
            else load_config(paths.root)
        )
        effective_policy = provenance_policy or config.merge_gate.provenance_policy
        provenance_state = "disabled" if effective_policy == "disabled" else "unprovenanced"
        task = load_task(paths.task_yaml)
    except Exception as exc:
        checks.append(MergeGateCheck(name="initialized", state="block", message=str(exc)))
        return _result(checks, task_id=None, provenance_state=provenance_state)

    cached_status = None
    cache_error: Exception | None = None
    try:
        cached_status = load_status(paths)
    except Exception as exc:
        cache_error = exc

    ledger = Ledger(paths.ledger)
    ledger_error: Exception | None = None
    try:
        records = ledger.read_verified()
    except Exception as exc:
        ledger_error = exc

    if ledger_error is None:
        try:
            repair_status_from_verified_records(paths, task, records, cached=cached_status)
        except Exception as exc:
            checks.append(MergeGateCheck(name="initialized", state="block", message=str(exc)))
            checks.append(
                MergeGateCheck(name="ledger_chain", state="pass", message="ledger chain verified")
            )
            return _result(checks, task_id=task.id, provenance_state=provenance_state)
        checks.append(MergeGateCheck(name="initialized", state="pass", message="AGOS task is active"))
        checks.append(MergeGateCheck(name="ledger_chain", state="pass", message="ledger chain verified"))
    else:
        initialization_message = (
            str(cache_error)
            if cache_error is not None
            else "AGOS task is active"
            if cached_status is not None
            else "current task status is missing"
        )
        checks.append(
            MergeGateCheck(
                name="initialized",
                state="pass" if cached_status is not None else "block",
                message=initialization_message,
            )
        )
        checks.append(MergeGateCheck(name="ledger_chain", state="block", message=str(ledger_error)))

    try:
        if trusted_config_path is not None:
            if task.workflow != config.default_workflow:
                raise ValueError(
                    "task workflow does not match trusted default workflow: "
                    f"{task.workflow!r} != {config.default_workflow!r}"
                )
            resolved_gates = resolve_gates(config, config.default_workflow)
            trusted_gate_ids = [gate.id for gate in resolved_gates]
            if task.gates != trusted_gate_ids:
                raise ValueError(
                    "task gate selection does not match complete trusted workflow: "
                    f"{task.gates!r} != {trusted_gate_ids!r}"
                )
        else:
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

    signed_verification = None
    if signed_anchor_store is not None:
        signed_verification = verify_current_signed_anchor(
            paths,
            signed_anchor_store,
            trusted_signers=config.merge_gate.trusted_signers,
            trusted_config_path=config_path,
        )
        anchor = signed_verification.anchor

    if require_anchor:
        if anchor_store is None:
            if signed_verification is None:
                checks.append(
                    MergeGateCheck(
                        name="trust_anchor",
                        state="block",
                        message="trust anchor is required but no store was provided",
                    )
                )
            else:
                checks.append(
                    MergeGateCheck(
                        name="trust_anchor",
                        state="pass" if signed_verification.passed else "block",
                        message="signed trust anchor verified"
                        if signed_verification.passed
                        else "signed trust anchor verification failed",
                        details=list(signed_verification.issues),
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
        provenance = evaluate_candidate_provenance(
            paths,
            candidates,
            records,
            policy=effective_policy,
            trusted_signers=config.merge_gate.trusted_signers,
            trusted_config_path=config_path,
            signed_anchor_verification=signed_verification,
        )
        provenance_state = provenance.state
        checks.append(
            MergeGateCheck(
                name="provenance",
                state="block" if provenance.issues else "pass",
                message="candidate provenance verification failed"
                if provenance.issues
                else f"candidate provenance is {provenance.state}",
                details=[*provenance.issues, *provenance.warnings],
            )
        )
        reconstructed_ids = set(provenance.reconstructed_candidate_ids)
        governed_candidates = [
            candidate
            for candidate in candidates
            if candidate.id not in reconstructed_ids and effective_policy != "disabled"
        ]
        validation_candidates = (
            candidates
            if effective_policy == "disabled"
            else [candidate for candidate in candidates if candidate.id in reconstructed_ids]
        )
        status_issues = _candidate_status_issues(governed_candidates)
        status_issues.extend(
            _validation_candidate_status_issues(
                validation_candidates,
                reconstructed_only=effective_policy != "disabled",
            )
        )
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
            governed_candidates,
            required_gate_ids=[gate.id for gate in resolved_gates],
            allow_missing_review=allow_missing_review,
            paths=paths,
            ledger_records_by_hash={
                str(record["hash"]): record for record in records if record.get("hash")
            },
            allow_fake_reviewer=config.allow_fake_reviewer,
        )
        evidence_issues.extend(
            _validation_candidate_evidence_issues(
                store,
                validation_candidates,
                records=records,
                required_gate_ids=[gate.id for gate in resolved_gates],
                reconstructed_only=effective_policy != "disabled",
            )
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
        decision_issues, decision_warnings = _candidate_decision_issues(
            governed_candidates,
            records=records,
            paths=paths,
            allow_legacy_decisionless=allow_legacy_decisionless,
        )
        checks.append(
            MergeGateCheck(
                name="candidate_decisions",
                state="block" if decision_issues else "pass",
                message="candidate decision verification failed"
                if decision_issues
                else "candidate decisions verified",
                details=decision_issues + decision_warnings,
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
        submitted_diff_issues = (
            _validation_submitted_diff_issues(
                paths,
                store,
                validation_candidates,
                base_ref=base_ref,
                head_ref=head_ref,
            )
            if validation_candidates
            else _submitted_diff_issues(
                paths,
                store,
                records,
                candidates,
                base_ref=base_ref,
                head_ref=head_ref,
                allow_accepted=provenance_state == "proven",
            )
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
        checks.append(MergeGateCheck(name="candidate_decisions", state="block", message=str(exc)))
        if not any(check.name == "provenance" for check in checks):
            checks.append(MergeGateCheck(name="provenance", state="block", message=str(exc)))

    return _result(
        checks,
        task_id=task.id,
        anchor=anchor,
        provenance_state=provenance_state,
    )


def _candidate_decision_issues(
    candidates: list[CandidatePatch],
    *,
    records: list[dict],
    paths: AgosPaths,
    allow_legacy_decisionless: bool,
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    applied_records: dict[str, list[dict]] = {}
    for record in records:
        if record.get("type") == "candidate_applied" and record.get("candidate_id"):
            applied_records.setdefault(str(record["candidate_id"]), []).append(record)

    for candidate in candidates:
        if candidate.status not in {"accepted", "applied"}:
            continue
        if candidate.decision_ref is None:
            detail = f"{candidate.id}: missing decision_ref"
            if allow_legacy_decisionless:
                warnings.append(f"{detail}; allowed as legacy decisionless evidence")
            else:
                issues.append(detail)
            continue

        try:
            decision_path = _task_ref_path(paths, candidate.decision_ref)
        except ValueError:
            issues.append(f"{candidate.id}: invalid decision_ref: {candidate.decision_ref}")
            continue
        if not decision_path.is_file():
            issues.append(
                f"{candidate.id}: decision evidence not found: {candidate.decision_ref}"
            )
            continue
        try:
            decision = ArbiterDecision.model_validate_json(decision_path.read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"{candidate.id}: decision evidence is unreadable: {exc}")
            continue

        expected_ref = f"execution/decisions/{decision.id}.json"
        if candidate.decision_ref != expected_ref:
            issues.append(
                f"{candidate.id}: decision_ref does not match decision id: {expected_ref}"
            )
        if decision.candidate_id != candidate.id:
            issues.append(
                f"{candidate.id}: decision candidate_id mismatch: {decision.candidate_id}"
            )
        if decision.decision != "accepted":
            issues.append(f"{candidate.id}: decision is not accepted: {decision.decision}")

        required_refs = {candidate.patch_ref, *candidate.test_refs}
        completed_reviews = [binding for binding in candidate.review_refs if binding.state == "completed"]
        if completed_reviews and completed_reviews[-1].report_ref is not None:
            required_refs.add(completed_reviews[-1].report_ref)
        missing_refs = sorted(required_refs - set(decision.evidence_refs))
        if missing_refs:
            issues.append(
                f"{candidate.id}: decision missing evidence refs: {', '.join(missing_refs)}"
            )

        if candidate.status == "applied":
            candidate_apply_records = applied_records.get(candidate.id, [])
            if not candidate_apply_records:
                issues.append(f"{candidate.id}: applied candidate is missing candidate_applied decision evidence")
            elif len(candidate_apply_records) > 1:
                issues.append(f"{candidate.id}: multiple candidate_applied decision records found")
            elif candidate_apply_records[0].get("decision_ref") != candidate.decision_ref:
                issues.append(
                    f"{candidate.id}: candidate_applied decision_ref does not match candidate"
                )
    return issues, warnings


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


def _validation_candidate_status_issues(
    candidates: list[CandidatePatch],
    *,
    reconstructed_only: bool,
) -> list[str]:
    issues: list[str] = []
    for candidate in candidates:
        if reconstructed_only and candidate.status != "tested":
            issues.append(
                f"{candidate.id}: ci_reconstructed candidate status must be tested: "
                f"{candidate.status}"
            )
        elif not reconstructed_only and candidate.status not in {"tested", "accepted", "applied"}:
            issues.append(
                f"{candidate.id}: validation-only candidate status is not eligible: "
                f"{candidate.status}"
            )
    return issues


def _validation_candidate_evidence_issues(
    store: ExecutionStore,
    candidates: list[CandidatePatch],
    *,
    records: list[dict],
    required_gate_ids: list[str],
    reconstructed_only: bool,
) -> list[str]:
    issues: list[str] = []
    required = {"patch_applies", *required_gate_ids}
    for candidate in candidates:
        runs = store.read_test_runs(candidate.id)
        passed = {run.gate_id for run in runs if run.state == "passed"}
        missing = sorted(required - passed)
        if missing:
            issues.append(
                f"{candidate.id}: missing passed deterministic tests: {', '.join(missing)}"
            )
        run_refs = {f"execution/tests/{run.id}.json" for run in runs if run.gate_id in required}
        missing_bindings = sorted(run_refs - set(candidate.test_refs))
        if missing_bindings:
            issues.append(
                f"{candidate.id}: candidate is missing deterministic test refs: "
                f"{', '.join(missing_bindings)}"
            )
        if not reconstructed_only:
            continue
        if candidate.review_refs:
            issues.append(f"{candidate.id}: ci_reconstructed candidate must not contain review refs")
        if candidate.decision_ref is not None:
            issues.append(f"{candidate.id}: ci_reconstructed candidate must not contain decision_ref")
        forbidden = {
            "candidate_review_completed",
            "candidate_decision_recorded",
            "candidate_applied",
        }
        found = sorted(
            {
                str(record.get("type"))
                for record in records
                if record.get("candidate_id") == candidate.id
                and record.get("type") in forbidden
            }
        )
        if found:
            issues.append(
                f"{candidate.id}: ci_reconstructed candidate has forbidden ledger events: "
                f"{', '.join(found)}"
            )
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
    allow_accepted: bool = False,
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
        if allow_accepted:
            accepted = [candidate for candidate in candidates if candidate.status == "accepted"]
            return [
                *issues,
                *_validation_submitted_diff_issues(
                    paths,
                    store,
                    accepted,
                    base_ref=base_ref,
                    head_ref=head_ref,
                ),
            ]
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


def _validation_submitted_diff_issues(
    paths: AgosPaths,
    store: ExecutionStore,
    candidates: list[CandidatePatch],
    *,
    base_ref: str | None,
    head_ref: str | None,
) -> list[str]:
    if not base_ref or not head_ref:
        return ["validation-only candidate evidence requires both base_ref and head_ref"]
    if len(candidates) != 1:
        return ["validation-only submitted diff requires exactly one candidate"]
    candidate = candidates[0]
    resolved_base = _git_rev_parse(paths, base_ref)
    if resolved_base is None:
        return [f"submitted diff base ref could not be resolved: {base_ref}"]
    if candidate.base_commit != resolved_base:
        return [
            f"{candidate.id}: validation candidate base_commit does not match submitted base"
        ]
    diff = _git_diff(paths, base_ref, head_ref)
    if diff is None:
        return [f"submitted diff could not be read for {base_ref}..{head_ref}"]
    try:
        expected = store.patch_path(candidate.patch_ref).read_bytes()
    except Exception as exc:
        return [f"validation candidate patch evidence could not be read: {exc}"]
    if (
        candidate_patch_paths(diff) != candidate_patch_paths(expected)
        or hashlib.sha256(diff).hexdigest() != hashlib.sha256(expected).hexdigest()
    ):
        return ["submitted diff does not match validation-only candidate evidence"]
    return []


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


def _git_rev_parse(paths: AgosPaths, ref: str) -> str | None:
    proc = run_command(
        ["git", "rev-parse", "--verify", ref],
        cwd=paths.root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return str(proc.stdout).strip()


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
    provenance_state: ProvenanceState = "unprovenanced",
) -> MergeGateResult:
    return MergeGateResult(
        passed=all(check.state == "pass" for check in checks),
        checks=checks,
        task_id=task_id,
        anchor=anchor,
        provenance_state=provenance_state,
    )
