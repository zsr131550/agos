"""Execution orchestration service for isolated candidate patches."""
from __future__ import annotations

import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import yaml

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
    utc_now_iso,
)
from agos.core.execution_store import ExecutionStore
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

    def __init__(self, paths: AgosPaths, *, worktree_root: Path | None = None) -> None:
        self.paths = paths
        self.store = ExecutionStore(paths)
        self.workspace_manager = ExecutionWorkspaceManager(
            paths,
            task_id=self._task_id_or_placeholder(),
            worktree_root=worktree_root,
        )

    def execute_plan(self, plan_path: Path) -> ExecutionPlan:
        status, task = self._active_task()
        del status
        payload = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
        plan = ExecutionPlan.model_validate(payload)
        if plan.task_id != task.id:
            raise ValueError(f"execution plan task_id {plan.task_id!r} does not match active task {task.id!r}")

        plan_ref = self.store.write_plan(plan)
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
            workspace = self.workspace_manager.create_workspace(subtask)
            workspace_ref = self.store.write_workspace(workspace)
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
                    "base_commit": workspace.base_commit,
                }
            )

        normalized = plan.model_copy(update={"subtasks": updated_subtasks})
        self.store.write_plan(normalized)
        return normalized

    def submit_candidate(self, subtask_id: str, *, summary: str) -> CandidatePatch:
        _status, task = self._active_task()
        subtask = self.store.read_subtask(subtask_id)
        workspace = self.store.read_workspace(subtask_id)
        patch_bytes = self.workspace_manager.capture_patch(Path(workspace.path))
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
        patch_run = self._record_patch_applies(candidate, patch_bytes)
        runs.append(patch_run)
        if patch_run.state == "passed":
            runs.extend(self._run_candidate_gates(candidate, patch_bytes, gates))

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

        report_ref, report = ReviewService(self.paths).ingest_findings(review_id, findings)
        completion_status = _load_active_status(self.paths)
        completed = binding.model_copy(
            update={
                "report_ref": report_ref,
                "state": "completed",
                "completed_at": utc_now_iso(),
                "ledger_head_at_completion": completion_status.ledger_head_hash,
                "open_blocking_count": len(report.open_blocking_findings()),
            }
        )
        bindings = list(candidate.review_refs)
        bindings[binding_index] = completed
        updated = candidate.model_copy(update={"status": "reviewed", "review_refs": bindings})
        self.store.write_candidate(updated)
        self._append_event(
            {
                "type": "candidate_review_completed",
                "task_id": task.id,
                "candidate_id": candidate.id,
                "review_id": review_id,
                "report_ref": report_ref,
                "open_blocking_count": len(report.open_blocking_findings()),
            }
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
        if decision == "accepted":
            self._assert_candidate_tests_passed(candidate, task)
            review = self._latest_completed_review_binding(candidate)
            if review is None:
                raise ValueError("accepted candidates require a completed candidate-bound review")
            self._assert_review_binding_current(candidate, review)
            if review.open_blocking_count not in {0, None}:
                raise ValueError("candidate review has open blocking findings")
            evidence_refs = [
                candidate.patch_ref,
                *candidate.test_refs,
                cast(str, review.report_ref),
            ]
        else:
            evidence_refs = [candidate.patch_ref]

        decision_model = ArbiterDecision(
            id=_new_id("decision"),
            candidate_id=candidate.id,
            decision=cast(DecisionValue, decision),
            reason=reason,
            evidence_refs=evidence_refs,
            decided_by=decided_by,
        )
        decision_ref = self.store.write_decision(decision_model)
        status_update = _candidate_status_for_decision(decision)
        updated = candidate.model_copy(update={"status": status_update, "decision_ref": decision_ref})
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
            self._append_event({"type": "candidate_rejected", "candidate_id": candidate.id})
        if decision == "superseded":
            self._append_event({"type": "candidate_superseded", "candidate_id": candidate.id})
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
        review = self._latest_completed_review_binding(candidate)
        if review is None:
            raise ValueError("candidate apply requires a completed candidate-bound review")
        self._assert_review_binding_current(candidate, review)
        self._assert_latest_decision_accepted(candidate)
        self._assert_no_accepted_overlap(candidate)
        self._assert_no_dirty_overlap(candidate_patch_paths(patch_bytes))

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

    def _record_patch_applies(self, candidate: CandidatePatch, patch_bytes: bytes) -> CandidateTestRun:
        self._append_event(
            {
                "type": "candidate_test_started",
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
                "candidate_id": candidate.id,
                "gate_id": "patch_applies",
                "state": run.state,
                "evidence_ref": run.evidence_ref,
            }
        )
        return run

    def _run_candidate_gates(
        self,
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
        return completed[-1] if completed else None

    def _assert_review_binding_current(
        self,
        candidate: CandidatePatch,
        binding: ReviewBinding,
    ) -> None:
        subtask = self.store.read_subtask(candidate.subtask_id)
        if binding.report_ref is None:
            raise ValueError("completed candidate-bound review is missing report_ref")
        if binding.patch_sha256 != candidate.patch_sha256:
            raise ValueError("candidate-bound review patch hash is stale")
        if binding.base_commit != candidate.base_commit:
            raise ValueError("candidate-bound review base commit is stale")
        if binding.write_scope != subtask.write_scope:
            raise ValueError("candidate-bound review write_scope is stale")
        if binding.test_refs != candidate.test_refs:
            raise ValueError("candidate-bound review test_refs are stale")
        if binding.open_blocking_count not in {0, None}:
            raise ValueError("candidate-bound review has open blocking findings")

    def _assert_latest_decision_accepted(self, candidate: CandidatePatch) -> None:
        decisions = self.store.read_decisions(candidate.id)
        if not decisions or decisions[-1].decision != "accepted":
            raise ValueError("latest arbiter decision is not accepted")

    def _assert_no_accepted_overlap(self, candidate: CandidatePatch) -> None:
        patch_paths = candidate_patch_paths(self._patch_bytes(candidate))
        for other in self.store.read_candidates():
            if other.id == candidate.id or other.status not in {"accepted", "applied"}:
                continue
            if patch_paths & candidate_patch_paths(self._patch_bytes(other)):
                raise ValueError(f"candidate conflicts with accepted/applied candidate: {other.id}")

    def _assert_no_dirty_overlap(self, patch_paths: set[str]) -> None:
        dirty_paths = _dirty_paths(self.paths.root)
        overlap = sorted(patch_paths & dirty_paths)
        if overlap:
            raise ValueError(f"governed repo has dirty files in candidate scope: {', '.join(overlap)}")

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


def _load_active_status(paths: AgosPaths) -> TaskStatus:
    status = load_status(paths)
    if status is None:
        raise ValueError("No active AGOS task found")
    return status


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _candidate_status_for_decision(decision: str) -> str:
    if decision == "accepted":
        return "accepted"
    if decision == "rejected":
        return "rejected"
    if decision == "superseded":
        return "superseded"
    return "reviewed"


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
