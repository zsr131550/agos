"""Automatic execution pipeline built on the existing execution service."""
from __future__ import annotations

from pydantic import BaseModel, Field

from agos.core.config import load_config
from agos.core.execution_runtime import ExecutionRuntime, ExecutionRuntimeSnapshot
from agos.core.execution_service import ExecutionService
from agos.core.execution_planner import create_execution_plan
from agos.core.review_adapter import ReviewerAdapter
from agos.core.review_orchestrator import ReviewerSpec
from agos.core.status import load_status
from agos.core.task import load_task


class AutoExecutionResult(BaseModel):
    plan_id: str
    task_id: str
    run_id: str
    run_state: str
    completed_subtasks: list[str] = Field(default_factory=list)
    failed_subtasks: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    accepted_candidate_ids: list[str] = Field(default_factory=list)
    applied_candidate_ids: list[str] = Field(default_factory=list)
    dry_run: bool = True
    notes: list[str] = Field(default_factory=list)


def run_auto_execution(
    service: ExecutionService,
    *,
    apply: bool = False,
    allow_missing_review: bool = False,
    planner_json: str | None = None,
    reviewer_adapters: dict[str, ReviewerAdapter] | None = None,
    reviewer_specs: list[ReviewerSpec] | None = None,
) -> AutoExecutionResult:
    """Generate, run, test, review, decide, and optionally apply a plan."""

    status = load_status(service.paths)
    if status is None:
        raise ValueError("No active AGOS task found")
    task = load_task(service.paths.task_yaml)
    config = load_config(service.paths.root)
    plan = create_execution_plan(
        task,
        config,
        config.workers,
        planner_json=planner_json,
    )
    prepared = service.execute_plan_model(plan)
    snapshot = _run_prepared_plan(service, prepared)

    candidate_ids: list[str] = []
    accepted_candidate_ids: list[str] = []
    applied_candidate_ids: list[str] = []
    notes: list[str] = []
    if snapshot.state == "stuck":
        notes.append("execution runtime stopped after repeated state observations; manual inspection required")
    reviewers = dict(reviewer_adapters or {})
    specs = list(reviewer_specs or [])

    for subtask_id in snapshot.completed_subtasks:
        try:
            candidate = service.submit_candidate(subtask_id, summary=f"Automatic candidate for {subtask_id}.")
        except Exception as exc:
            notes.append(f"candidate skipped for {subtask_id}: {exc}")
            continue
        candidate_ids.append(candidate.id)

        runs = service.test_candidate(candidate.id)
        if any(run.state != "passed" for run in runs):
            notes.append(f"candidate {candidate.id} tests failed")
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
                notes.append(f"candidate {candidate.id} review failed: {exc}")
                continue
            if report.open_blocking_findings():
                notes.append(f"candidate {candidate.id} has blocking review findings")
                continue
            reviewed = True
        elif not allow_missing_review:
            notes.append(f"candidate {candidate.id} requires review before acceptance")
            continue
        else:
            _packet_ref, packet = service.review_candidate(candidate.id)
            service.ingest_candidate_review(candidate.id, packet.review_id, findings=[])
            reviewed = True

        try:
            service.decide_candidate(
                candidate.id,
                decision="accepted",
                reason=_acceptance_reason(reviewed=reviewed),
                decided_by="agos_auto_pipeline",
            )
        except Exception as exc:
            notes.append(f"candidate {candidate.id} was not accepted: {exc}")
            continue
        accepted_candidate_ids.append(candidate.id)

        if apply:
            try:
                service.apply_candidate(candidate.id)
            except Exception as exc:
                notes.append(f"candidate {candidate.id} apply failed: {exc}")
                continue
            applied_candidate_ids.append(candidate.id)

    return AutoExecutionResult(
        plan_id=prepared.id,
        task_id=task.id,
        run_id=snapshot.run_id,
        run_state=snapshot.state,
        completed_subtasks=list(snapshot.completed_subtasks),
        failed_subtasks=list(snapshot.failed_subtasks),
        candidate_ids=candidate_ids,
        accepted_candidate_ids=accepted_candidate_ids,
        applied_candidate_ids=applied_candidate_ids,
        dry_run=not apply,
        notes=notes,
    )


def _run_prepared_plan(service: ExecutionService, plan) -> ExecutionRuntimeSnapshot:
    runtime_config = load_config(service.paths.root).orchestration
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
        worker_timeout_seconds=runtime_config.worker_timeout_seconds,
    )
    run_id = "auto-run-" + plan.id.removeprefix("auto-plan-")
    snapshot = runtime.tick(plan, run_id=run_id)
    previous: tuple[str, str, tuple[str, ...], tuple[str, ...]] | None = None
    for _ in range(20):
        state_key = (
            snapshot.state,
            ",".join(snapshot.running_subtasks),
            snapshot.completed_subtasks,
            snapshot.failed_subtasks,
        )
        if snapshot.state not in {"queued", "running"}:
            break
        if previous == state_key:
            snapshot = ExecutionRuntimeSnapshot(
                run_id=snapshot.run_id,
                running_subtasks=snapshot.running_subtasks,
                completed_subtasks=snapshot.completed_subtasks,
                failed_subtasks=snapshot.failed_subtasks,
                cancelled_subtasks=snapshot.cancelled_subtasks,
                backend=snapshot.backend,
                state="stuck",
                waiting_nodes=snapshot.waiting_nodes,
                completed_nodes=snapshot.completed_nodes,
                failed_nodes=snapshot.failed_nodes,
                output_refs=dict(snapshot.output_refs),
            )
            break
        previous = state_key
        snapshot = runtime.tick(plan, run_id=run_id)
    return snapshot


def _acceptance_reason(*, reviewed: bool) -> str:
    if reviewed:
        return "Automatic pipeline accepted candidate after passing tests and clean review."
    return "Automatic pipeline accepted candidate after passing tests with missing review explicitly allowed."
