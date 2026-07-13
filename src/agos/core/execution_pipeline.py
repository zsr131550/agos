"""Automatic execution pipeline built on the existing execution service."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import time

from pydantic import BaseModel, Field

from agos.core.config import load_config
from agos.core.execution_runtime import ExecutionRuntime, ExecutionRuntimeSnapshot
from agos.core.execution_service import ExecutionService
from agos.core.execution_planner import PlanSource, PlannerAdapter, create_execution_plan_with_provenance
from agos.core.review_adapter import ReviewerAdapter
from agos.core.review_orchestrator import ReviewerSpec
from agos.core.status import load_status
from agos.core.task import load_task


class AutoExecutionResult(BaseModel):
    plan_id: str
    task_id: str
    run_id: str
    run_state: str
    planner_source: PlanSource = "fallback"
    subtask_worker_assignments: dict[str, str] = Field(default_factory=dict)
    completed_subtasks: list[str] = Field(default_factory=list)
    failed_subtasks: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    accepted_candidate_ids: list[str] = Field(default_factory=list)
    applied_candidate_ids: list[str] = Field(default_factory=list)
    reviewer_ids: list[str] = Field(default_factory=list)
    candidate_review_ids: dict[str, str] = Field(default_factory=dict)
    candidate_review_raw_refs: dict[str, list[str]] = Field(default_factory=dict)
    blocked_stage: str | None = None
    blocked_reason: str | None = None
    dry_run: bool = True
    notes: list[str] = Field(default_factory=list)


def run_auto_execution(
    service: ExecutionService,
    *,
    apply: bool = False,
    allow_missing_review: bool = False,
    planner_json: str | None = None,
    planner: PlannerAdapter | None = None,
    reviewer_adapters: dict[str, ReviewerAdapter] | None = None,
    reviewer_specs: list[ReviewerSpec] | None = None,
) -> AutoExecutionResult:
    """Generate, run, test, review, decide, and optionally apply a plan."""

    status = load_status(service.paths)
    if status is None:
        raise ValueError("No active AGOS task found")
    task = load_task(service.paths.task_yaml)
    config = load_config(service.paths.root)
    plan_result = create_execution_plan_with_provenance(
        task,
        config,
        config.workers,
        planner_json=planner_json,
        planner=planner,
    )
    plan = plan_result.plan
    prepared = service.execute_plan_model(plan)
    snapshot = _run_prepared_plan(service, prepared)

    candidate_ids: list[str] = []
    accepted_candidate_ids: list[str] = []
    applied_candidate_ids: list[str] = []
    reviewer_ids: list[str] = []
    candidate_review_ids: dict[str, str] = {}
    candidate_review_raw_refs: dict[str, list[str]] = {}
    blocked_stage: str | None = None
    blocked_reason: str | None = None
    notes: list[str] = []
    if snapshot.state == "stuck":
        note = "execution runtime exhausted its polling budget; resume or inspect persisted worker state"
        notes.append(note)
        blocked_stage, blocked_reason = _first_block(blocked_stage, blocked_reason, "execution", note)
    if snapshot.failed_subtasks:
        note = "execution completed with failed subtasks"
        notes.append(note)
        blocked_stage, blocked_reason = _first_block(blocked_stage, blocked_reason, "execution", note)
    reviewers = dict(reviewer_adapters or {})
    specs = list(reviewer_specs or [])

    for subtask_id in snapshot.completed_subtasks:
        try:
            candidate = service.submit_candidate(subtask_id, summary=f"Automatic candidate for {subtask_id}.")
        except Exception as exc:
            note = f"candidate skipped for {subtask_id}: {exc}"
            notes.append(note)
            blocked_stage, blocked_reason = _first_block(blocked_stage, blocked_reason, "candidate", note)
            continue
        candidate_ids.append(candidate.id)

        runs = service.test_candidate(candidate.id)
        if any(run.state != "passed" for run in runs):
            note = f"candidate {candidate.id} tests failed"
            notes.append(note)
            blocked_stage, blocked_reason = _first_block(blocked_stage, blocked_reason, "tests", note)
            continue

        reviewed = False
        if specs:
            try:
                _report_ref, report, _result = service.run_candidate_review(
                    candidate.id,
                    reviewer_adapters=reviewers,
                    reviewer_specs=specs,
                    max_parallel=config.orchestration.max_parallel,
                )
            except Exception as exc:
                reviewer_ids.extend(_new_ids(reviewer_ids, [spec.id for spec in specs]))
                _record_latest_review_binding(
                    service,
                    candidate.id,
                    candidate_review_ids,
                    candidate_review_raw_refs,
                )
                note = f"candidate {candidate.id} review failed: {exc}"
                notes.append(note)
                blocked_stage, blocked_reason = _first_block(blocked_stage, blocked_reason, "review", note)
                continue
            reviewer_ids.extend(_new_ids(reviewer_ids, [spec.id for spec in specs]))
            candidate_review_ids[candidate.id] = report.review_id
            candidate_review_raw_refs[candidate.id] = list(_result.raw_refs)
            if report.open_blocking_findings():
                note = f"candidate {candidate.id} has blocking review findings"
                notes.append(note)
                blocked_stage, blocked_reason = _first_block(blocked_stage, blocked_reason, "review", note)
                continue
            reviewed = True
        elif not allow_missing_review:
            note = f"candidate {candidate.id} requires review before acceptance"
            notes.append(note)
            blocked_stage, blocked_reason = _first_block(blocked_stage, blocked_reason, "review", note)
            continue
        else:
            _packet_ref, packet = service.review_candidate(candidate.id)
            service.ingest_candidate_review(candidate.id, packet.review_id, findings=[])
            candidate_review_ids[candidate.id] = packet.review_id
            candidate_review_raw_refs[candidate.id] = []
            notes.append(f"candidate {candidate.id} missing review explicitly allowed")

        try:
            service.decide_candidate(
                candidate.id,
                decision="accepted",
                reason=_acceptance_reason(reviewed=reviewed),
                decided_by="agos_auto_pipeline",
            )
        except Exception as exc:
            note = f"candidate {candidate.id} was not accepted: {exc}"
            notes.append(note)
            blocked_stage, blocked_reason = _first_block(blocked_stage, blocked_reason, "decision", note)
            continue
        accepted_candidate_ids.append(candidate.id)

        if apply:
            try:
                service.apply_candidate(candidate.id)
            except Exception as exc:
                note = f"candidate {candidate.id} apply failed: {exc}"
                notes.append(note)
                blocked_stage, blocked_reason = _first_block(blocked_stage, blocked_reason, "apply", note)
                continue
            applied_candidate_ids.append(candidate.id)

    return AutoExecutionResult(
        plan_id=prepared.id,
        task_id=task.id,
        run_id=snapshot.run_id,
        run_state=snapshot.state,
        planner_source=plan_result.source,
        subtask_worker_assignments={
            subtask.id: subtask.worker.adapter for subtask in prepared.subtasks
        },
        completed_subtasks=list(snapshot.completed_subtasks),
        failed_subtasks=list(snapshot.failed_subtasks),
        candidate_ids=candidate_ids,
        accepted_candidate_ids=accepted_candidate_ids,
        applied_candidate_ids=applied_candidate_ids,
        reviewer_ids=reviewer_ids,
        candidate_review_ids=candidate_review_ids,
        candidate_review_raw_refs=candidate_review_raw_refs,
        blocked_stage=blocked_stage,
        blocked_reason=blocked_reason,
        dry_run=not apply,
        notes=notes,
    )


def _run_prepared_plan(
    service: ExecutionService,
    plan,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> ExecutionRuntimeSnapshot:
    config = load_config(service.paths.root)
    runtime_config = config.orchestration
    worker_timeout_seconds = runtime_config.worker_timeout_seconds
    if worker_timeout_seconds is None and _any_claude_async_poll(config):
        # Async `--bg` polling without an overall timeout would let a stuck
        # background session poll indefinitely; apply a bounded fallback.
        worker_timeout_seconds = CLAUDE_ASYNC_FALLBACK_TIMEOUT_SECONDS
    runtime = ExecutionRuntime(
        state_dir=service.paths.current_task / "execution" / "runs",
        worker_adapters=service.worker_adapters(),
        workspace_paths={
            subtask.id: service.store.read_workspace(subtask.id).path
            for subtask in plan.subtasks
            if subtask.workspace_ref is not None
        },
        max_retries=runtime_config.max_retries,
        retry_backoff_seconds=runtime_config.retry_backoff_seconds,
        worker_timeout_seconds=worker_timeout_seconds,
    )
    run_id = "auto-run-" + plan.id.removeprefix("auto-plan-")
    snapshot = runtime.tick(plan, run_id=run_id)
    for _ in range(runtime_config.max_tick_iterations):
        if snapshot.state not in {"queued", "running"}:
            break
        interval = _running_poll_interval(config, plan, snapshot.running_subtasks)
        if interval > 0:
            sleeper(interval)
        snapshot = runtime.tick(plan, run_id=run_id)
    if snapshot.state in {"queued", "running"}:
        snapshot = replace(snapshot, state="stuck")
    return snapshot


def _running_poll_interval(config, plan, running_subtasks: tuple[str, ...]) -> int:
    subtasks_by_id = {subtask.id: subtask for subtask in plan.subtasks}
    intervals = []
    for subtask_id in running_subtasks:
        subtask = subtasks_by_id.get(subtask_id)
        if subtask is None:
            continue
        worker = config.workers.get(subtask.worker.adapter)
        if worker is not None:
            intervals.append(worker.poll_interval_seconds)
    return max(intervals, default=0)


def _acceptance_reason(*, reviewed: bool) -> str:
    if reviewed:
        return "Automatic pipeline accepted candidate after passing tests and clean review."
    return "Automatic pipeline accepted candidate after passing tests with missing review explicitly allowed."


def _first_block(
    current_stage: str | None,
    current_reason: str | None,
    stage: str,
    reason: str,
) -> tuple[str | None, str | None]:
    if current_stage is not None:
        return current_stage, current_reason
    return stage, reason


def _new_ids(existing: list[str], incoming: list[str]) -> list[str]:
    seen = set(existing)
    return [value for value in incoming if value not in seen]


def _record_latest_review_binding(
    service: ExecutionService,
    candidate_id: str,
    candidate_review_ids: dict[str, str],
    candidate_review_raw_refs: dict[str, list[str]],
) -> None:
    try:
        candidate = service.store.read_candidate(candidate_id)
    except Exception:
        return
    if not candidate.review_refs:
        return
    binding = candidate.review_refs[-1]
    candidate_review_ids[candidate_id] = binding.review_id
    candidate_review_raw_refs[candidate_id] = list(binding.raw_refs)


# Overall timeout applied when a claude_code worker opts into async `--bg`
# polling but no explicit `worker_timeout_seconds` is configured. Keeps a stuck
# background session from polling forever (P1-2 correction #3).
CLAUDE_ASYNC_FALLBACK_TIMEOUT_SECONDS = 600


def _any_claude_async_poll(config) -> bool:
    return any(
        worker.type == "claude_code" and worker.claude_async_poll
        for worker in config.workers.values()
    )
