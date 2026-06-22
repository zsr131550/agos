"""Execution orchestration service for isolated candidate patches."""
from __future__ import annotations

import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import yaml

from agos.core.arbiters import (
    CandidateDecisionArbiter,
    CandidateDecisionSnapshot,
    CandidateMergeArbiter,
    ReviewDecisionArbiter,
    ReviewDecisionSnapshot,
)
from agos.core.command import run_command
from agos.core.config import load_config, resolve_gates
from agos.core.execution import (
    ArbiterDecision,
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
from agos.core.execution_store import ExecutionStore
from agos.core.execution_worker import (
    ExecutionWorkerAdapter,
    WorkerAssignment,
    WorkerWorkspaceHandle,
)
from agos.core.execution_workspace import (
    ExecutionWorkspaceManager,
    candidate_patch_paths,
    patch_bytes_sha256,
)
from agos.core.gate import GateContext, build_gate, gates_match
from agos.core.ledger import Ledger, LedgerTamperError
from agos.core.repo import AgosPaths, git_status_porcelain
from agos.core.review import Finding, ReviewPacket, ReviewReport
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

    def register_worker_adapter(self, adapter: ExecutionWorkerAdapter) -> None:
        self._worker_adapters[adapter.name] = adapter

    def worker_adapter_names(self) -> list[str]:
        return sorted(self._worker_adapters)

    def execute_plan(self, plan_path: Path) -> ExecutionPlan:
        status, task = self._active_task()
        del status
        payload = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
        plan = ExecutionPlan.model_validate(payload)
        if plan.task_id != task.id:
            raise ValueError(f"execution plan task_id {plan.task_id!r} does not match active task {task.id!r}")

        plan_ref = self.store.write_plan(plan)
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
            failed_event = self._append_event(
                {
                    "type": "candidate_review_failed",
                    "task_id": task.id,
                    "candidate_id": candidate.id,
                    "review_id": review_id,
                    "error": str(exc),
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
            self.store.write_candidate(candidate.model_copy(update={"review_refs": bindings}))
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

        subtask = self.store.read_subtask(candidate.subtask_id)
        patch_bytes = self._patch_bytes(candidate)
        self.workspace_manager.validate_patch_scope(patch_bytes, subtask.write_scope)
        self._assert_candidate_tests_passed(candidate, task)
        review, report = self._latest_completed_review_and_report(candidate)
        self._assert_review_binding_current(candidate, review, report)
        merge_decision = self.merge_arbiter.decide(
            candidate,
            accepted_candidate_paths={
                other.id: candidate_patch_paths(self._patch_bytes(other))
                for other in self.store.read_candidates()
                if other.id != candidate.id and other.status in {"accepted", "applied"}
            },
            dirty_paths=_dirty_paths(self.paths.root),
            patch_paths=candidate_patch_paths(patch_bytes),
        )
        if not merge_decision.allowed:
            if merge_decision.dirty_paths:
                raise ValueError(
                    f"governed repo has dirty files in candidate scope: {', '.join(merge_decision.dirty_paths)}"
                )
            if merge_decision.conflict_candidate_ids:
                raise ValueError(
                    "candidate conflicts with accepted/applied candidate: "
                    f"{merge_decision.conflict_candidate_ids[0]}"
                )

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
                    "task_id": task.id,
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
                "task_id": task.id,
                "candidate_id": candidate.id,
                "patch_ref": candidate.patch_ref,
                "decision_ref": candidate.decision_ref,
            }
        )
        return applied

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
                    command=gate_spec.command or " ".join(gate_spec.argv or []),
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


def _workspace_binding(prepared: object) -> WorkspaceBinding:
    if isinstance(prepared, WorkspaceBinding):
        return prepared
    binding = getattr(prepared, "binding", None)
    if isinstance(binding, WorkspaceBinding):
        return binding
    raise TypeError("worker prepare() must return a WorkspaceBinding or an object with binding")


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
