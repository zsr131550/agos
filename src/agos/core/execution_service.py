"""Execution orchestration service for isolated candidate patches."""
from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import yaml

from agos.core.arbiters import (
    CandidateDecisionArbiter,
    CandidateDecisionSnapshot,
    CandidateMergeArbiter,
    MergeCandidateSnapshot,
    ReviewDecisionArbiter,
    ReviewDecisionSnapshot,
)
from agos.core.command import run_command
from agos.core.config import load_config, resolve_gates
from agos.core.execution import (
    ArbiterDecision,
    CandidateBundleDecision,
    CandidateMergePreview,
    CandidatePatch,
    CandidateTestRun,
    DecisionValue,
    ExecutionPlan,
    ExecutionSubtask,
    ReviewBinding,
    WorkspaceBinding,
    utc_now_iso,
)
from agos.core.execution_orchestration import ExecutionOrchestrator
from agos.core.execution_runtime import ExecutionRuntime, ExecutionRuntimeSnapshot
from agos.core.execution_store import ExecutionStore
from agos.core.execution_worker import (
    ExecutionWorkerAdapter,
    WorkerAssignment,
    WorkerWorkspaceHandle,
    ensure_worker_ready,
)
from agos.core.execution_workspace import (
    ExecutionWorkspaceManager,
    candidate_patch_paths,
    patch_bytes_sha256,
)
from agos.core.gate import GateContext, build_gate, gate_command_text, gates_match
from agos.core.ledger import Ledger, LedgerTamperError
from agos.core.orchestration.models import (
    OrchestrationRunSpec,
    OrchestratorRunHandle,
    OrchestratorRunStatus,
)
from agos.core.orchestration.protocols import OrchestrationBackend
from agos.core.repo import AgosPaths, git_status_porcelain
from agos.core.review import Finding, ReviewPacket, ReviewReport
from agos.core.review_adapter import ReviewerAdapter
from agos.core.review_orchestrator import (
    ParallelReviewOrchestrator,
    ReviewerSpec,
    ReviewRunResult,
)
from agos.core.review_service import ReviewService
from agos.core.status import TaskStatus, load_status, save_status
from agos.core.task import Task, load_task


class ExecutionService:
    """Coordinate execution artifacts, review bindings, and guarded apply."""

    def __init__(
        self,
        paths: AgosPaths,
        *,
        worktree_root: Path | None = None,
        worker_adapters: dict[str, ExecutionWorkerAdapter] | None = None,
        orchestration_backends: dict[str, OrchestrationBackend] | None = None,
    ) -> None:
        self.paths = paths
        self.store = ExecutionStore(paths)
        self.workspace_manager = ExecutionWorkspaceManager(
            paths,
            task_id=self._task_id_or_placeholder(),
            worktree_root=worktree_root,
        )
        self.execution_orchestrator = ExecutionOrchestrator(paths)
        self.review_arbiter = ReviewDecisionArbiter()
        self.candidate_arbiter = CandidateDecisionArbiter()
        self.merge_arbiter = CandidateMergeArbiter()
        self._worker_adapters: dict[str, ExecutionWorkerAdapter] = dict(worker_adapters or {})
        self._orchestration_backends: dict[str, OrchestrationBackend] = dict(
            orchestration_backends or {}
        )

    def register_worker_adapter(self, adapter: ExecutionWorkerAdapter) -> None:
        self._worker_adapters[adapter.name] = adapter

    def register_orchestration_backend(self, backend: OrchestrationBackend) -> None:
        self._orchestration_backends[backend.name] = backend

    def worker_adapter_names(self) -> list[str]:
        return sorted(self._worker_adapters)

    def worker_adapters(self) -> dict[str, ExecutionWorkerAdapter]:
        return dict(self._worker_adapters)

    def orchestration_backend_names(self) -> list[str]:
        return sorted(self._orchestration_backends)

    def start_execution_run(
        self,
        plan_path: Path,
        *,
        run_id: str | None = None,
    ) -> ExecutionRuntimeSnapshot:
        backend_name = load_config(self.paths.root).orchestration.backend
        plan = self.execute_plan(plan_path, build_orchestration_spec=False)
        execution_run_id = run_id or _new_id("execution-run")
        if backend_name != "native_async":
            spec = self._execution_spec_for_backend(plan_path, backend_name, run_id=execution_run_id)
            backend = self._orchestration_backend(backend_name)
            handle = backend.run(spec)
            initial = ExecutionRuntimeSnapshot(
                run_id=handle.run_id,
                backend=handle.backend,
                state="queued",
            )
            self._write_run_snapshot(initial)
            snapshot = _snapshot_from_orchestrator_status(backend.poll(handle))
            self._write_run_snapshot(snapshot)
            return snapshot
        self._ensure_plan_worker_readiness(plan)
        return self._execution_runtime(plan).tick(plan, run_id=execution_run_id)

    def resume_execution_run(self, run_id: str) -> ExecutionRuntimeSnapshot:
        persisted = self._read_run_snapshot(run_id)
        if persisted is not None and persisted.backend != "native_async":
            return self._poll_orchestration_snapshot(persisted)
        plan = self.store.read_plan()
        return self._execution_runtime(plan).tick(plan, run_id=run_id)

    def status_execution_run(self, run_id: str) -> ExecutionRuntimeSnapshot:
        persisted = self._read_run_snapshot(run_id)
        if persisted is not None and persisted.backend != "native_async":
            return self._poll_orchestration_snapshot(persisted)
        plan = self.store.read_plan()
        return self._execution_runtime(plan).status(plan, run_id=run_id)

    def cancel_execution_run(self, run_id: str) -> ExecutionRuntimeSnapshot:
        persisted = self._read_run_snapshot(run_id)
        if persisted is not None and persisted.backend != "native_async":
            return self._cancel_orchestration_snapshot(persisted)
        plan = self.store.read_plan()
        return self._execution_runtime(plan).cancel(plan, run_id=run_id)

    def execute_plan(
        self,
        plan_path: Path,
        *,
        build_orchestration_spec: bool = True,
    ) -> ExecutionPlan:
        payload = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
        plan = ExecutionPlan.model_validate(payload)
        return self.execute_plan_model(
            plan,
            build_orchestration_spec=build_orchestration_spec,
            plan_path=plan_path,
        )

    def execute_plan_model(
        self,
        plan: ExecutionPlan,
        *,
        build_orchestration_spec: bool = False,
        plan_path: Path | None = None,
    ) -> ExecutionPlan:
        status, task = self._active_task()
        del status
        if plan.task_id != task.id:
            raise ValueError(f"execution plan task_id {plan.task_id!r} does not match active task {task.id!r}")

        plan_ref = self.store.write_plan(plan)
        if build_orchestration_spec:
            if plan_path is None:
                raise ValueError("plan_path is required when building an orchestration spec")
            self.execution_orchestrator.build_spec(plan_path)
        self._append_event(
            {
                "type": "execution_plan_created",
                "task_id": task.id,
                "plan_id": plan.id,
                "plan_ref": plan_ref,
                "subtask_ids": [subtask.id for subtask in plan.subtasks],
            }
        )

        updated_subtasks: list[ExecutionSubtask] = []
        for subtask in plan.subtasks:
            prepared = self._worker_adapter(subtask.worker.adapter).prepare(WorkerAssignment(subtask=subtask))
            workspace_binding = _workspace_binding(prepared)
            workspace_binding = workspace_binding.model_copy(
                update={"worker_handle_metadata": _worker_handle_metadata(prepared)}
            )
            workspace_ref = self.store.write_workspace(workspace_binding)
            updated = subtask.model_copy(
                update={"status": "workspace_ready", "workspace_ref": workspace_ref}
            )
            self.store.write_subtask(updated)
            updated_subtasks.append(updated)
            self._append_event(
                {
                    "type": "subtask_workspace_created",
                    "task_id": task.id,
                    "subtask_id": subtask.id,
                    "workspace_ref": workspace_ref,
                    "base_commit": workspace_binding.base_commit,
                }
            )

        normalized = plan.model_copy(update={"subtasks": updated_subtasks})
        self.store.write_plan(normalized)
        return normalized

    def submit_candidate(self, subtask_id: str, *, summary: str) -> CandidatePatch:
        _status, task = self._active_task()
        subtask = self.store.read_subtask(subtask_id)
        workspace = self.store.read_workspace(subtask_id)
        worker = self._worker_adapter(subtask.worker.adapter)
        export = worker.export_candidate(
            WorkerWorkspaceHandle(
                subtask_id=subtask.id,
                metadata=_canonical_worker_handle_metadata(workspace),
            )
        )
        patch_bytes = export["patch_bytes"]
        self.workspace_manager.validate_patch_scope(patch_bytes, subtask.write_scope)
        candidate_id = _new_id("candidate")
        patch_ref, patch_sha = self.store.write_candidate_patch(candidate_id, patch_bytes)
        candidate = CandidatePatch(
            id=candidate_id,
            task_id=task.id,
            subtask_id=subtask.id,
            source_agent=subtask.worker.adapter,
            workspace_ref=workspace.ref,
            patch_ref=patch_ref,
            patch_sha256=patch_sha,
            base_commit=workspace.base_commit,
            summary=summary,
        )
        self.store.write_candidate(candidate)
        self._append_event(
            {
                "type": "candidate_patch_created",
                "task_id": task.id,
                "subtask_id": subtask.id,
                "candidate_id": candidate.id,
                "patch_ref": patch_ref,
                "patch_sha256": patch_sha,
            }
        )
        return candidate

    def test_candidate(self, candidate_id: str, *, gate_id: str | None = None) -> list[CandidateTestRun]:
        _status, task = self._active_task()
        candidate = self._candidate_with_valid_patch(candidate_id)
        patch_bytes = self._patch_bytes(candidate)
        gates = self._active_gates(task)
        if gate_id is not None:
            by_id = {gate.id: gate for gate in gates}
            if gate_id not in by_id:
                raise ValueError(f"gate is not locked for active task: {gate_id}")
            gates = [by_id[gate_id]]

        candidate = candidate.model_copy(update={"status": "testing"})
        self.store.write_candidate(candidate)
        runs: list[CandidateTestRun] = []
        patch_run = self._record_patch_applies(task.id, candidate, patch_bytes)
        runs.append(patch_run)
        if patch_run.state == "passed":
            runs.extend(self._run_candidate_gates(task.id, candidate, patch_bytes, gates))

        test_refs = [*candidate.test_refs, *[self.store.write_test_run(run) for run in runs]]
        final_status = "tested" if all(run.state == "passed" for run in runs) else "proposed"
        candidate = candidate.model_copy(update={"status": final_status, "test_refs": test_refs})
        self.store.write_candidate(candidate)
        return runs

    def review_candidate(self, candidate_id: str) -> tuple[str, ReviewPacket]:
        status, task = self._active_task()
        candidate = self._candidate_with_valid_patch(candidate_id)
        subtask = self.store.read_subtask(candidate.subtask_id)
        self._assert_candidate_tests_passed(candidate, task)
        review_service = ReviewService(self.paths)
        context_refs = [
            f"execution/candidates/{candidate.id}.json",
            f"execution/subtasks/{subtask.id}.json",
            candidate.workspace_ref,
            *candidate.test_refs,
        ]
        packet_ref, packet = review_service.create_packet(
            diff_kind="candidate_patch",
            diff_evidence_ref=candidate.patch_ref,
            subject={
                "type": "candidate",
                "candidate_id": candidate.id,
                "subtask_id": subtask.id,
                "task_id": task.id,
            },
            context_refs=context_refs,
        )
        binding = ReviewBinding(
            review_id=packet.review_id,
            packet_ref=packet_ref,
            patch_sha256=candidate.patch_sha256,
            base_commit=candidate.base_commit,
            write_scope=list(subtask.write_scope),
            test_refs=list(candidate.test_refs),
            ledger_head_at_start=status.ledger_head_hash,
            state="started",
        )
        updated = candidate.model_copy(
            update={"status": "reviewing", "review_refs": [*candidate.review_refs, binding]}
        )
        self.store.write_candidate(updated)
        self._append_event(
            {
                "type": "candidate_review_started",
                "task_id": task.id,
                "candidate_id": candidate.id,
                "review_id": packet.review_id,
                "packet_ref": packet_ref,
                "patch_ref": candidate.patch_ref,
            }
        )
        return packet_ref, packet

    def ingest_candidate_review(
        self,
        candidate_id: str,
        review_id: str,
        *,
        findings: Iterable[Finding],
    ) -> tuple[str, ReviewReport]:
        _status, task = self._active_task()
        candidate = self.store.read_candidate(candidate_id)
        binding_index, binding = self._review_binding(candidate, review_id)
        if binding.state != "started":
            raise ValueError(f"candidate review binding is not started: {review_id}")

        try:
            ordered_findings = self.review_arbiter.decide(
                ReviewDecisionSnapshot(review_id=review_id, findings=tuple(findings))
            )
            report_ref, report = ReviewService(self.paths).ingest_findings(review_id, ordered_findings)
        except Exception as exc:
            self._mark_candidate_review_failed(candidate.id, review_id, error=str(exc))
            raise

        completed_event = self._append_event(
            {
                "type": "candidate_review_completed",
                "task_id": task.id,
                "candidate_id": candidate.id,
                "review_id": review_id,
                "report_ref": report_ref,
                "open_blocking_count": len(report.open_blocking_findings()),
            }
        )
        completed = binding.model_copy(
            update={
                "report_ref": report_ref,
                "state": "completed",
                "completed_at": utc_now_iso(),
                "ledger_head_at_completion": completed_event["hash"],
                "open_blocking_count": len(report.open_blocking_findings()),
            }
        )
        bindings = list(candidate.review_refs)
        bindings[binding_index] = completed
        self.store.write_candidate(
            candidate.model_copy(update={"status": "reviewed", "review_refs": bindings})
        )
        return report_ref, report

    def run_candidate_review(
        self,
        candidate_id: str,
        *,
        reviewer_adapters: dict[str, ReviewerAdapter],
        reviewer_specs: list[ReviewerSpec],
        max_parallel: int = 4,
    ) -> tuple[str, ReviewReport, ReviewRunResult]:
        if not reviewer_specs:
            raise ValueError("at least one configured reviewer is required")

        packet_ref, packet = self.review_candidate(candidate_id)
        run_id = _new_id("review-run")
        try:
            result = ParallelReviewOrchestrator(reviewer_adapters).run(
                run_id=run_id,
                packet=packet,
                reviewers=reviewer_specs,
                max_parallel=max_parallel,
            )
        except Exception as exc:
            self._mark_candidate_review_failed(candidate_id, packet.review_id, error=str(exc))
            raise
        if result.state != "completed":
            failed = ", ".join(result.failed_reviewers)
            self._mark_candidate_review_failed(
                candidate_id,
                packet.review_id,
                error=f"required reviewers failed: {failed}",
            )
            raise ValueError(f"required reviewers failed: {failed}")

        report_ref, report = self.ingest_candidate_review(
            candidate_id,
            packet.review_id,
            findings=result.findings,
        )
        return report_ref, report, result

    def decide_candidate(
        self,
        candidate_id: str,
        *,
        decision: str,
        reason: str,
        decided_by: str = "local_user",
    ) -> ArbiterDecision:
        _status, task = self._active_task()
        candidate = self._candidate_with_valid_patch(candidate_id)
        review_binding = self._latest_completed_review_binding(candidate)
        review_report_ref = review_binding.report_ref if review_binding is not None else None
        review_open_blocking_count = 0
        review_binding_current = False
        if review_binding is not None:
            review_binding, review_report = self._latest_completed_review_and_report(candidate)
            if cast(DecisionValue, decision) == "accepted":
                self._assert_review_binding_current(candidate, review_binding, review_report)
            review_binding_current = True
            review_open_blocking_count = len(review_report.open_blocking_findings())
        tests_passed = self._candidate_tests_passed(candidate, task)
        evidence_refs = [candidate.patch_ref]
        if tests_passed:
            evidence_refs.extend(candidate.test_refs)
        if review_binding_current and review_report_ref is not None:
            evidence_refs.append(cast(str, review_report_ref))

        decision_result = self.candidate_arbiter.decide(
            CandidateDecisionSnapshot(
                candidate_id=candidate.id,
                decision=cast(DecisionValue, decision),
                reason=reason,
                decided_by=decided_by,
                evidence_refs=tuple(evidence_refs),
                tests_passed=tests_passed,
                review_binding_current=review_binding_current,
                review_open_blocking_count=review_open_blocking_count,
                patch_ref=candidate.patch_ref,
                test_refs=tuple(candidate.test_refs),
                review_report_ref=review_report_ref,
            )
        )
        decision_model = decision_result.decision
        decision_ref = self.store.write_decision(decision_model)
        updated = candidate.model_copy(
            update={"status": decision_result.candidate_status, "decision_ref": decision_ref}
        )
        self.store.write_candidate(updated)
        self._append_event(
            {
                "type": "candidate_decision_recorded",
                "task_id": task.id,
                "candidate_id": candidate.id,
                "decision": decision,
                "decision_ref": decision_ref,
                "evidence_refs": evidence_refs,
            }
        )
        if decision == "rejected":
            self._append_event(
                {"type": "candidate_rejected", "task_id": task.id, "candidate_id": candidate.id}
            )
        if decision == "superseded":
            self._append_event(
                {"type": "candidate_superseded", "task_id": task.id, "candidate_id": candidate.id}
            )
        return decision_model

    def apply_candidate(self, candidate_id: str) -> CandidatePatch:
        _status, task = self._active_task()
        candidate = self._candidate_with_valid_patch(candidate_id)
        if candidate.status == "applied":
            raise ValueError(f"candidate is already applied: {candidate.id}")
        if candidate.status != "accepted":
            raise ValueError("candidate must be accepted before apply")

        patch_bytes = self._patch_bytes(candidate)
        self._preflight_candidate_for_bundle_apply(
            candidate,
            task,
            [candidate],
            check_patch_applies=False,
        )
        return self._apply_candidate_patch_to_repo(task.id, candidate, patch_bytes)

    def decide_candidate_bundle(
        self,
        candidate_ids: list[str] | None = None,
        *,
        dependency_order: list[str] | None = None,
    ) -> CandidateBundleDecision:
        _status, task = self._active_task()
        candidates = self._bundle_candidates(candidate_ids)
        snapshots = [self._merge_candidate_snapshot(candidate, task) for candidate in candidates]
        decision = self.merge_arbiter.decide_bundle(
            snapshots,
            dirty_paths=_dirty_paths(self.paths.root),
            dependency_order=dependency_order or (),
        )
        stored = CandidateBundleDecision(
            id=_new_id("bundle"),
            strategy=decision.strategy,
            candidate_ids=list(decision.candidate_ids),
            reason=decision.reason,
            evidence_refs=list(decision.evidence_refs),
            conflict_candidate_ids=list(decision.conflict_candidate_ids),
        )
        decision_ref = self.store.write_bundle_decision(stored)
        self._append_event(
            {
                "type": "candidate_bundle_decided",
                "task_id": task.id,
                "strategy": stored.strategy,
                "candidate_ids": stored.candidate_ids,
                "decision_ref": decision_ref,
                "evidence_refs": stored.evidence_refs,
                "conflict_candidate_ids": stored.conflict_candidate_ids,
            }
        )
        return stored

    def apply_candidate_bundle(self, decision_id: str) -> list[CandidatePatch]:
        _status, task = self._active_task()
        decision = self.store.read_bundle_decision(decision_id)
        if decision.strategy == "manual_merge_required":
            raise ValueError("manual merge required; bundle cannot be applied automatically")
        candidates = [self._candidate_with_valid_patch(candidate_id) for candidate_id in decision.candidate_ids]
        if decision.strategy == "single_candidate":
            applied = [self.apply_candidate(candidates[0].id)]
        else:
            patches = [(candidate, self._patch_bytes(candidate)) for candidate in candidates]
            for candidate in candidates:
                self._preflight_candidate_for_bundle_apply(candidate, task, candidates)
            if decision.strategy == "ordered_patch_stack":
                self._dry_run_candidate_stack(decision, task, patches)
            applied = [
                self._apply_candidate_patch_to_repo(task.id, candidate, patch_bytes)
                for candidate, patch_bytes in patches
            ]
        self._append_event(
            {
                "type": "candidate_bundle_applied",
                "task_id": task.id,
                "bundle_decision_id": decision.id,
                "candidate_ids": [candidate.id for candidate in applied],
                "patch_refs": {candidate.id: candidate.patch_ref for candidate in applied},
            }
        )
        return applied

    def preview_candidate_bundle(self, decision_id: str) -> tuple[str, CandidateMergePreview]:
        _status, task = self._active_task()
        decision = self.store.read_bundle_decision(decision_id)
        if decision.strategy == "manual_merge_required":
            raise ValueError("manual merge required; bundle cannot be previewed automatically")

        candidates = [self._candidate_with_valid_patch(candidate_id) for candidate_id in decision.candidate_ids]
        patches = [(candidate, self._patch_bytes(candidate)) for candidate in candidates]
        for candidate in candidates:
            self._preflight_candidate_for_bundle_apply(candidate, task, candidates)
        if decision.strategy == "ordered_patch_stack":
            return self._dry_run_candidate_stack(
                decision,
                task,
                patches,
                raise_on_failure=False,
            )
        return self._write_static_merge_preview(decision, task, candidates)

    def _bundle_candidates(self, candidate_ids: list[str] | None) -> list[CandidatePatch]:
        if candidate_ids is None or not candidate_ids:
            candidates = [
                candidate
                for candidate in self.store.read_candidates()
                if candidate.status == "accepted"
            ]
        else:
            candidates = [self.store.read_candidate(candidate_id) for candidate_id in candidate_ids]
        if not candidates:
            raise ValueError("candidate bundle requires at least one candidate")
        return candidates

    def _merge_candidate_snapshot(
        self,
        candidate: CandidatePatch,
        task: Task,
    ) -> MergeCandidateSnapshot:
        patch_bytes = self._patch_bytes(candidate)
        open_blocking_count = 1
        try:
            review, report = self._latest_completed_review_and_report(candidate)
            self._assert_review_binding_current(candidate, review, report)
            open_blocking_count = len(report.open_blocking_findings())
        except ValueError:
            open_blocking_count = 1
        return MergeCandidateSnapshot(
            candidate_id=candidate.id,
            patch_ref=candidate.patch_ref,
            patch_sha256=candidate.patch_sha256,
            touched_paths=tuple(sorted(candidate_patch_paths(patch_bytes))),
            tests_passed=self._candidate_tests_passed(candidate, task),
            review_open_blocking_count=open_blocking_count,
            accepted=candidate.status == "accepted",
            score=len(candidate.test_refs),
        )

    def _preflight_candidate_for_bundle_apply(
        self,
        candidate: CandidatePatch,
        task: Task,
        bundle_candidates: list[CandidatePatch],
        *,
        check_patch_applies: bool = True,
    ) -> None:
        if candidate.status != "accepted":
            raise ValueError(f"candidate must be accepted before bundle apply: {candidate.id}")
        subtask = self.store.read_subtask(candidate.subtask_id)
        patch_bytes = self._patch_bytes(candidate)
        self.workspace_manager.validate_patch_scope(patch_bytes, subtask.write_scope)
        self._assert_candidate_tests_passed(candidate, task)
        review, report = self._latest_completed_review_and_report(candidate)
        self._assert_review_binding_current(candidate, review, report)
        bundle_ids = {item.id for item in bundle_candidates}
        merge_decision = self.merge_arbiter.decide(
            candidate,
            accepted_candidate_paths={
                other.id: candidate_patch_paths(self._patch_bytes(other))
                for other in self.store.read_candidates()
                if other.id != candidate.id
                and other.id not in bundle_ids
                and other.status in {"accepted", "applied"}
            },
            dirty_paths=_dirty_paths(self.paths.root),
            patch_paths=candidate_patch_paths(patch_bytes),
        )
        if not merge_decision.allowed:
            if merge_decision.dirty_paths:
                raise ValueError(
                    "governed repo has dirty files in candidate scope: "
                    f"{', '.join(merge_decision.dirty_paths)}"
                )
            if merge_decision.conflict_candidate_ids:
                raise ValueError(
                    "candidate conflicts with accepted/applied candidate: "
                    f"{merge_decision.conflict_candidate_ids[0]}"
                )
        if not check_patch_applies:
            return
        check = run_command(
            ["git", "apply", "--check", "--binary", "-"],
            cwd=self.paths.root,
            input=patch_bytes,
            capture_output=True,
        )
        if check.returncode != 0:
            raise ValueError(f"candidate patch does not apply during bundle preflight: {candidate.id}")

    def _apply_candidate_patch_to_repo(
        self,
        task_id: str,
        candidate: CandidatePatch,
        patch_bytes: bytes,
    ) -> CandidatePatch:
        check = run_command(
            ["git", "apply", "--check", "--binary", "-"],
            cwd=self.paths.root,
            input=patch_bytes,
            capture_output=True,
        )
        if check.returncode != 0:
            evidence_ref = self._write_apply_blocked_evidence(candidate, check)
            self._append_event(
                {
                    "type": "candidate_apply_blocked",
                    "task_id": task_id,
                    "candidate_id": candidate.id,
                    "evidence_ref": evidence_ref,
                }
            )
            raise ValueError(f"candidate patch does not apply: {evidence_ref}")

        run_command(
            ["git", "apply", "--binary", "-"],
            cwd=self.paths.root,
            input=patch_bytes,
            check=True,
            capture_output=True,
        )
        applied = candidate.model_copy(update={"status": "applied"})
        self.store.write_candidate(applied)
        self._append_event(
            {
                "type": "candidate_applied",
                "task_id": task_id,
                "candidate_id": candidate.id,
                "patch_ref": candidate.patch_ref,
                "decision_ref": candidate.decision_ref,
            }
        )
        return applied

    def _write_static_merge_preview(
        self,
        decision: CandidateBundleDecision,
        task: Task,
        candidates: list[CandidatePatch],
    ) -> tuple[str, CandidateMergePreview]:
        preview_id = _new_id("merge-preview")
        log_dir = self.paths.evidence / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_name = f"{preview_id}.log"
        log_path = log_dir / log_name
        log_ref = f"execution/{log_name}"
        preview = CandidateMergePreview(
            id=preview_id,
            decision_id=decision.id,
            strategy=decision.strategy,
            candidate_ids=[candidate.id for candidate in candidates],
            state="passed",
            evidence_refs=[log_ref, *decision.evidence_refs],
            conflict_evidence_refs=[],
        )
        preview_ref = self.store.write_merge_preview(preview)
        log_path.write_text(
            "\n".join(
                [
                    "command: candidate bundle preview",
                    f"decision_id: {decision.id}",
                    f"task_id: {task.id}",
                    f"strategy: {decision.strategy}",
                    "candidate_ids: " + ", ".join(candidate.id for candidate in candidates),
                    "state: passed",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self._append_event(
            {
                "type": "candidate_merge_preview_completed",
                "task_id": task.id,
                "bundle_decision_id": decision.id,
                "preview_ref": preview_ref,
                "state": "passed",
                "evidence_refs": preview.evidence_refs,
                "conflict_evidence_refs": preview.conflict_evidence_refs,
            }
        )
        return preview_ref, preview

    def _dry_run_candidate_stack(
        self,
        decision: CandidateBundleDecision,
        task: Task,
        patches: list[tuple[CandidatePatch, bytes]],
        *,
        raise_on_failure: bool = True,
    ) -> tuple[str, CandidateMergePreview]:
        preview_id = _new_id("merge-preview")
        log_dir = self.paths.evidence / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_name = f"{preview_id}.log"
        log_path = log_dir / log_name
        log_ref = f"execution/{log_name}"
        evidence_refs = [log_ref]
        conflict_evidence_refs: list[str] = []
        lines = [
            "command: ordered_patch_stack dry-run",
            f"decision_id: {decision.id}",
            "candidate_ids: " + ", ".join(candidate.id for candidate, _patch in patches),
        ]
        workspace: Path | None = None
        state: Literal["passed", "failed"] = "passed"
        failure: str | None = None
        try:
            workspace = self.workspace_manager._create_verification_workspace(preview_id)
            lines.append(f"workspace: {workspace}")
            for candidate, patch_bytes in patches:
                check = run_command(
                    ["git", "apply", "--check", "--binary", "-"],
                    cwd=workspace,
                    input=patch_bytes,
                    capture_output=True,
                )
                lines.extend(_process_log_lines(f"check {candidate.id}", check))
                if check.returncode != 0:
                    state = "failed"
                    conflict_evidence_refs.append(log_ref)
                    failure = f"ordered patch stack dry-run failed for {candidate.id}: {log_ref}"
                    break

                applied = run_command(
                    ["git", "apply", "--binary", "-"],
                    cwd=workspace,
                    input=patch_bytes,
                    capture_output=True,
                )
                lines.extend(_process_log_lines(f"apply {candidate.id}", applied))
                if applied.returncode != 0:
                    state = "failed"
                    conflict_evidence_refs.append(log_ref)
                    failure = f"ordered patch stack apply failed for {candidate.id}: {log_ref}"
                    break

            if failure is None:
                diff_proc = run_command(
                    ["git", "diff", "--binary", "HEAD"],
                    cwd=workspace,
                    check=True,
                    capture_output=True,
                )
                stack_diff = _decode(diff_proc.stdout)
                for gate_spec in self._active_gates(task):
                    result = build_gate(gate_spec).evaluate(
                        GateContext(
                            repo_root=workspace,
                            stage="candidate",
                            diff=stack_diff,
                            evidence_dir=self.paths.evidence,
                        )
                    )
                    gate_ref = _evidence_ref(self.paths.evidence, result.evidence_path)
                    if gate_ref:
                        evidence_refs.append(gate_ref)
                    lines.append(f"gate {gate_spec.id}: {result.state} {gate_ref}")
                    if result.state != "pass":
                        state = "failed"
                        conflict_evidence_refs.append(gate_ref or log_ref)
                        failure = f"ordered patch stack gate failed for {gate_spec.id}: {gate_ref or log_ref}"
                        break
        except Exception as exc:
            if failure is None:
                state = "failed"
                conflict_evidence_refs.append(log_ref)
                failure = f"ordered patch stack dry-run errored: {exc}"
            raise
        finally:
            if workspace is not None:
                self.workspace_manager._remove_worktree(workspace)
            lines.append(f"state: {state}")
            if failure is not None:
                lines.append(f"failure: {failure}")
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            preview = CandidateMergePreview(
                id=preview_id,
                decision_id=decision.id,
                strategy=decision.strategy,
                candidate_ids=[candidate.id for candidate, _patch in patches],
                state=state,
                evidence_refs=evidence_refs,
                conflict_evidence_refs=conflict_evidence_refs,
            )
            preview_ref = self.store.write_merge_preview(preview)
            self._append_event(
                {
                    "type": "candidate_merge_preview_completed",
                    "task_id": task.id,
                    "bundle_decision_id": decision.id,
                    "preview_ref": preview_ref,
                    "state": state,
                    "evidence_refs": evidence_refs,
                    "conflict_evidence_refs": conflict_evidence_refs,
                }
            )
        if failure is not None and raise_on_failure:
            raise ValueError(failure)
        return preview_ref, preview

    def _record_patch_applies(
        self,
        task_id: str,
        candidate: CandidatePatch,
        patch_bytes: bytes,
    ) -> CandidateTestRun:
        self._append_event(
            {
                "type": "candidate_test_started",
                "task_id": task_id,
                "candidate_id": candidate.id,
                "gate_id": "patch_applies",
            }
        )
        evidence = self.workspace_manager.check_patch_applies(
            candidate_id=candidate.id,
            patch_bytes=patch_bytes,
            evidence_dir=self.paths.evidence,
        )
        run = CandidateTestRun(
            id=_new_id("candidate-test"),
            candidate_id=candidate.id,
            gate_id="patch_applies",
            command=evidence.command,
            state="passed" if evidence.state == "passed" else "failed",
            evidence_ref=evidence.evidence_ref,
            workspace_ref=candidate.workspace_ref,
            completed_at=utc_now_iso(),
        )
        self._append_event(
            {
                "type": "candidate_test_completed",
                "task_id": task_id,
                "candidate_id": candidate.id,
                "gate_id": "patch_applies",
                "state": run.state,
                "evidence_ref": run.evidence_ref,
            }
        )
        return run

    def _run_candidate_gates(
        self,
        task_id: str,
        candidate: CandidatePatch,
        patch_bytes: bytes,
        gates: list[Any],
    ) -> list[CandidateTestRun]:
        if not gates:
            return []

        workspace = self.workspace_manager._create_verification_workspace(candidate.id)
        try:
            run_command(
                ["git", "apply", "--binary", "-"],
                cwd=workspace,
                input=patch_bytes,
                check=True,
                capture_output=True,
            )
            runs: list[CandidateTestRun] = []
            for gate_spec in gates:
                self._append_event(
                    {
                        "type": "candidate_test_started",
                        "task_id": task_id,
                        "candidate_id": candidate.id,
                        "gate_id": gate_spec.id,
                    }
                )
                result = build_gate(gate_spec).evaluate(
                    GateContext(
                        repo_root=workspace,
                        stage="candidate",
                        diff=patch_bytes.decode("utf-8", errors="replace"),
                        evidence_dir=self.paths.evidence,
                    )
                )
                run = CandidateTestRun(
                    id=_new_id("candidate-test"),
                    candidate_id=candidate.id,
                    gate_id=gate_spec.id,
                    command=gate_command_text(gate_spec),
                    state="passed" if result.state == "pass" else "failed",
                    evidence_ref=_evidence_ref(self.paths.evidence, result.evidence_path),
                    workspace_ref=candidate.workspace_ref,
                    completed_at=utc_now_iso(),
                )
                runs.append(run)
                self._append_event(
                    {
                        "type": "candidate_test_completed",
                        "task_id": task_id,
                        "candidate_id": candidate.id,
                        "gate_id": gate_spec.id,
                        "state": run.state,
                        "evidence_ref": run.evidence_ref,
                    }
                )
            return runs
        finally:
            self.workspace_manager._remove_worktree(workspace)

    def _active_task(self) -> tuple[TaskStatus, Task]:
        status = _load_active_status(self.paths)
        if status.phase == "done":
            raise ValueError("active task is already done")
        try:
            Ledger(self.paths.ledger).verify_chain()
        except LedgerTamperError as exc:
            raise ValueError(f"Ledger verification failed: {exc}") from exc
        return status, load_task(self.paths.task_yaml)

    def _candidate_with_valid_patch(self, candidate_id: str) -> CandidatePatch:
        candidate = self.store.read_candidate(candidate_id)
        actual = patch_bytes_sha256(self._patch_bytes(candidate))
        if actual != candidate.patch_sha256:
            raise ValueError(f"candidate patch hash mismatch: {candidate.id}")
        return candidate

    def _candidate_tests_passed(self, candidate: CandidatePatch, task: Task) -> bool:
        runs = self.store.read_test_runs(candidate.id)
        passed = {run.gate_id for run in runs if run.state == "passed"}
        required = {"patch_applies", *[gate.id for gate in self._active_gates(task)]}
        return not (required - passed)

    def _patch_bytes(self, candidate: CandidatePatch) -> bytes:
        path = self.store.patch_path(candidate.patch_ref)
        if not path.is_file():
            raise ValueError(f"candidate patch file not found: {candidate.patch_ref}")
        return path.read_bytes()

    def _active_gates(self, task: Task):
        config = load_config(self.paths.root)
        gates = resolve_gates(config, task.workflow, override=task.gates)
        records = Ledger(self.paths.ledger).read_all()
        locked_records = [record for record in records if record.get("type") == "gates_locked"]
        if locked_records and not gates_match(locked_records[-1].get("gates", []), gates):
            raise ValueError("Current gate set does not match gates_locked")
        return gates

    def _assert_candidate_tests_passed(self, candidate: CandidatePatch, task: Task) -> None:
        runs = self.store.read_test_runs(candidate.id)
        passed = {run.gate_id for run in runs if run.state == "passed"}
        required = {"patch_applies", *[gate.id for gate in self._active_gates(task)]}
        missing = sorted(required - passed)
        if missing:
            raise ValueError(f"candidate is missing passed tests: {', '.join(missing)}")

    def _review_binding(self, candidate: CandidatePatch, review_id: str) -> tuple[int, ReviewBinding]:
        for index, binding in enumerate(candidate.review_refs):
            if binding.review_id == review_id:
                return index, binding
        raise ValueError(f"candidate-bound review not found: {review_id}")

    def _mark_candidate_review_failed(
        self,
        candidate_id: str,
        review_id: str,
        *,
        error: str,
    ) -> None:
        _status, task = self._active_task()
        candidate = self.store.read_candidate(candidate_id)
        binding_index, binding = self._review_binding(candidate, review_id)
        failed_event = self._append_event(
            {
                "type": "candidate_review_failed",
                "task_id": task.id,
                "candidate_id": candidate.id,
                "review_id": review_id,
                "error": error,
            }
        )
        failed = binding.model_copy(
            update={
                "state": "failed",
                "completed_at": utc_now_iso(),
                "ledger_head_at_completion": failed_event["hash"],
            }
        )
        bindings = list(candidate.review_refs)
        bindings[binding_index] = failed
        next_status = _candidate_status_after_failed_review(candidate, review_id)
        self.store.write_candidate(
            candidate.model_copy(update={"status": next_status, "review_refs": bindings})
        )

    def _latest_completed_review_binding(self, candidate: CandidatePatch) -> ReviewBinding | None:
        completed = [binding for binding in candidate.review_refs if binding.state == "completed"]
        if not completed:
            return None
        return max(completed, key=self._review_completion_seq)

    def _latest_completed_review_and_report(
        self,
        candidate: CandidatePatch,
    ) -> tuple[ReviewBinding, ReviewReport]:
        binding = self._latest_completed_review_binding(candidate)
        if binding is None:
            raise ValueError("candidate requires a completed candidate-bound review")
        if binding.report_ref is None:
            raise ValueError("completed candidate-bound review is missing report_ref")
        try:
            report = ReviewService(self.paths).store.read_report(binding.review_id)
        except FileNotFoundError as exc:
            raise ValueError(f"candidate-bound review report not found: {binding.review_id}") from exc
        return binding, report

    def _assert_review_binding_current(
        self,
        candidate: CandidatePatch,
        binding: ReviewBinding,
        report: ReviewReport,
    ) -> None:
        subtask = self.store.read_subtask(candidate.subtask_id)
        if binding.report_ref is None:
            raise ValueError("completed candidate-bound review is missing report_ref")
        if report.review_id != binding.review_id:
            raise ValueError("selected candidate-bound review report does not match review_id")
        if binding.patch_sha256 != candidate.patch_sha256:
            raise ValueError("candidate-bound review patch hash is stale")
        if binding.base_commit != candidate.base_commit:
            raise ValueError("candidate-bound review base commit is stale")
        if binding.write_scope != subtask.write_scope:
            raise ValueError("candidate-bound review write_scope is stale")
        if binding.test_refs != candidate.test_refs:
            raise ValueError("candidate-bound review test_refs are stale")
        open_blocking_count = len(report.open_blocking_findings())
        if binding.open_blocking_count is not None and binding.open_blocking_count != open_blocking_count:
            raise ValueError("candidate-bound review open blocking count is stale")
        if open_blocking_count:
            raise ValueError("candidate-bound review has open blocking findings")

    def _review_completion_seq(self, binding: ReviewBinding) -> int:
        if binding.ledger_head_at_completion is None:
            raise ValueError("candidate-bound review is missing completion ledger head")
        for record in Ledger(self.paths.ledger).read_all():
            if record.get("hash") == binding.ledger_head_at_completion:
                return int(record["seq"])
        raise ValueError(
            f"candidate-bound review completion ledger hash not found: {binding.review_id}"
        )

    def _write_apply_blocked_evidence(
        self,
        candidate: CandidatePatch,
        proc: subprocess.CompletedProcess,
    ) -> str:
        log_dir = self.paths.evidence / "execution"
        log_dir.mkdir(parents=True, exist_ok=True)
        name = f"{candidate.id}-apply-{_fsafe_ts()}-{uuid4().hex[:8]}.log"
        path = log_dir / name
        path.write_text(
            (
                "command: git apply --check --binary -\n"
                f"candidate_id: {candidate.id}\n"
                f"exit_code: {proc.returncode}\n"
                f"--- stdout ---\n{_decode(proc.stdout)}\n"
                f"--- stderr ---\n{_decode(proc.stderr)}\n"
            ),
            encoding="utf-8",
        )
        return f"execution/{name}"

    def _append_event(self, record: dict[str, Any]) -> dict[str, Any]:
        status = _load_active_status(self.paths)
        appended = Ledger(self.paths.ledger).append(record)
        status.ledger_head_hash = appended["hash"]
        save_status(status, self.paths)
        return appended

    def _task_id_or_placeholder(self) -> str:
        status = load_status(self.paths)
        return status.task_id if status is not None else "unknown-task"

    def _worker_adapter(self, adapter_name: str) -> ExecutionWorkerAdapter:
        if adapter_name not in self._worker_adapters:
            raise ValueError(f"unsupported worker adapter: {adapter_name}")
        return self._worker_adapters[adapter_name]

    def _orchestration_backend(self, backend_name: str) -> OrchestrationBackend:
        if backend_name not in self._orchestration_backends:
            raise ValueError(f"unsupported orchestration backend: {backend_name}")
        return self._orchestration_backends[backend_name]

    def _ensure_plan_worker_readiness(self, plan: ExecutionPlan) -> None:
        checked: set[str] = set()
        for subtask in plan.subtasks:
            adapter_name = subtask.worker.adapter
            if adapter_name in checked:
                continue
            ensure_worker_ready(self._worker_adapter(adapter_name))
            checked.add(adapter_name)

    def _execution_spec_for_backend(
        self,
        plan_path: Path,
        backend_name: str,
        *,
        run_id: str,
    ) -> OrchestrationRunSpec:
        return self.execution_orchestrator.build_spec(
            plan_path,
            run_id=run_id,
            backend=backend_name,
        )

    def _poll_orchestration_snapshot(
        self,
        snapshot: ExecutionRuntimeSnapshot,
    ) -> ExecutionRuntimeSnapshot:
        handle = OrchestratorRunHandle(backend=snapshot.backend, run_id=snapshot.run_id)
        updated = _snapshot_from_orchestrator_status(
            self._orchestration_backend(snapshot.backend).poll(handle)
        )
        self._write_run_snapshot(updated)
        return updated

    def _cancel_orchestration_snapshot(
        self,
        snapshot: ExecutionRuntimeSnapshot,
    ) -> ExecutionRuntimeSnapshot:
        handle = OrchestratorRunHandle(backend=snapshot.backend, run_id=snapshot.run_id)
        updated = _snapshot_from_orchestrator_status(
            self._orchestration_backend(snapshot.backend).cancel(handle)
        )
        self._write_run_snapshot(updated)
        return updated

    def _write_run_snapshot(self, snapshot: ExecutionRuntimeSnapshot) -> None:
        path = self._run_status_path(snapshot.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_snapshot_payload(snapshot), indent=2, sort_keys=True), encoding="utf-8")

    def _read_run_snapshot(self, run_id: str) -> ExecutionRuntimeSnapshot | None:
        path = self._run_status_path(run_id)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _snapshot_from_payload(payload)

    def _run_status_path(self, run_id: str) -> Path:
        return self.paths.current_task / "execution" / "runs" / run_id / "status.json"

    def _execution_runtime(self, plan: ExecutionPlan) -> ExecutionRuntime:
        runtime_config = load_config(self.paths.root).orchestration
        return ExecutionRuntime(
            state_dir=self.paths.current_task / "execution" / "runs",
            worker_adapters=self._worker_adapters,
            workspace_paths={
                subtask.id: self.store.read_workspace(subtask.id).path
                for subtask in plan.subtasks
                if subtask.workspace_ref is not None
            },
            max_retries=runtime_config.max_retries,
            retry_backoff_seconds=runtime_config.retry_backoff_seconds,
            worker_timeout_seconds=runtime_config.worker_timeout_seconds,
        )


def _workspace_binding(prepared: object) -> WorkspaceBinding:
    if isinstance(prepared, WorkspaceBinding):
        return prepared
    binding = getattr(prepared, "binding", None)
    if isinstance(binding, WorkspaceBinding):
        return binding
    raise TypeError("worker prepare() must return a WorkspaceBinding or an object with binding")


def _snapshot_from_orchestrator_status(
    status: OrchestratorRunStatus,
) -> ExecutionRuntimeSnapshot:
    return ExecutionRuntimeSnapshot(
        run_id=status.run_id,
        backend=status.backend,
        state=status.state,
        waiting_nodes=tuple(status.waiting_nodes),
        completed_nodes=tuple(status.completed_nodes),
        failed_nodes=tuple(status.failed_nodes),
        output_refs=dict(status.output_refs or {}),
    )


def _snapshot_payload(snapshot: ExecutionRuntimeSnapshot) -> dict[str, object]:
    return {
        "run_id": snapshot.run_id,
        "backend": snapshot.backend,
        "state": snapshot.state,
        "running_subtasks": list(snapshot.running_subtasks),
        "completed_subtasks": list(snapshot.completed_subtasks),
        "failed_subtasks": list(snapshot.failed_subtasks),
        "cancelled_subtasks": list(snapshot.cancelled_subtasks),
        "waiting_nodes": list(snapshot.waiting_nodes),
        "completed_nodes": list(snapshot.completed_nodes),
        "failed_nodes": list(snapshot.failed_nodes),
        "output_refs": snapshot.output_refs,
    }


def _snapshot_from_payload(payload: dict[str, object]) -> ExecutionRuntimeSnapshot:
    return ExecutionRuntimeSnapshot(
        run_id=str(payload["run_id"]),
        backend=str(payload.get("backend") or "native_async"),
        state=str(payload.get("state") or "queued"),
        running_subtasks=_string_tuple(payload.get("running_subtasks", [])),
        completed_subtasks=_string_tuple(payload.get("completed_subtasks", [])),
        failed_subtasks=_string_tuple(payload.get("failed_subtasks", [])),
        cancelled_subtasks=_string_tuple(payload.get("cancelled_subtasks", [])),
        waiting_nodes=_string_tuple(payload.get("waiting_nodes", [])),
        completed_nodes=_string_tuple(payload.get("completed_nodes", [])),
        failed_nodes=_string_tuple(payload.get("failed_nodes", [])),
        output_refs=_string_dict(payload.get("output_refs", {})),
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value)


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _worker_handle_metadata(prepared: object) -> dict[str, str]:
    handle = getattr(prepared, "handle", None)
    metadata = getattr(handle, "metadata", None)
    if isinstance(metadata, dict):
        return {str(key): str(value) for key, value in metadata.items()}
    return {}


def _canonical_worker_handle_metadata(workspace: WorkspaceBinding) -> dict[str, str]:
    metadata = dict(workspace.worker_handle_metadata)
    metadata["workspace_path"] = workspace.path
    metadata["workspace_ref"] = workspace.ref
    return metadata


def _candidate_status_after_failed_review(
    candidate: CandidatePatch,
    failed_review_id: str,
) -> str:
    if any(
        binding.state == "completed" and binding.review_id != failed_review_id
        for binding in candidate.review_refs
    ):
        return "reviewed"
    if candidate.test_refs:
        return "tested"
    return "proposed"


def _load_active_status(paths: AgosPaths) -> TaskStatus:
    status = load_status(paths)
    if status is None:
        raise ValueError("No active AGOS task found")
    return status


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _evidence_ref(evidence_dir: Path, evidence_path: str | None) -> str:
    if evidence_path is None:
        return ""
    path = Path(evidence_path)
    try:
        return path.resolve().relative_to(evidence_dir.resolve()).as_posix()
    except ValueError:
        return evidence_path


def _dirty_paths(repo_root: Path) -> set[str]:
    output = git_status_porcelain(repo_root)
    paths: set[str] = set()
    for line in output.splitlines():
        if not line:
            continue
        path = line[3:]
        if path.startswith(".agos/"):
            continue
        if " -> " in path:
            _old, path = path.split(" -> ", 1)
        paths.add(path.replace("\\", "/"))
    return paths


def _fsafe_ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _process_log_lines(label: str, proc: subprocess.CompletedProcess) -> list[str]:
    return [
        f"--- {label} ---",
        f"exit_code: {proc.returncode}",
        f"--- stdout ---\n{_decode(proc.stdout)}",
        f"--- stderr ---\n{_decode(proc.stderr)}",
    ]




