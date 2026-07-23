"""Unified application service for legacy and candidate task entrypoints."""
from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agos.core.adapter import ExecutorAdapter, ExecutorRun, RunStatus
from agos.core.config import AGOSConfig, GateSpec, load_config, resolve_gates
from agos.core.evidence import EvidenceStore
from agos.core.execution_pipeline import AutoExecutionResult
from agos.core.gate import gates_locked_payload
from agos.core.repo import (
    AgosPaths,
    current_task_is_active,
    staging_task_dir,
    task_paths,
)
from agos.core.task import ExecutorBinding, Task, load_task, new_task_id
from agos.core.task_state import (
    TaskEvent,
    TaskRevision,
    TaskState,
    TaskStateCommitIndeterminate,
    TaskStateConflict,
)
from agos.core.task_execution import (
    ExecutionMode,
    ExecutorSelection,
    TaskExecutionRequest,
    TaskExecutionResult,
    effective_task_mode,
    executor_selection_id,
)


class TaskExecutionError(RuntimeError):
    """Raised when unified task creation or dispatch cannot proceed."""


class TaskExecutionDispatchUnreconciled(TaskExecutionError):
    """Raised after an external legacy run cannot be safely reconciled."""

    def __init__(
        self,
        *,
        task_id: str,
        run_id: str,
        stage: str,
        cause: Exception,
    ) -> None:
        super().__init__(
            f"executor run {run_id} for task {task_id} requires reconciliation "
            f"after {stage}: {cause}"
        )
        self.task_id = task_id
        self.run_id = run_id
        self.stage = stage


@dataclass(frozen=True)
class ResolvedLegacyExecutor:
    """A configured legacy adapter plus its initial polling behavior."""

    adapter: ExecutorAdapter
    synchronous: bool = False


class LegacyExecutorFactory(Protocol):
    def __call__(
        self,
        paths: AgosPaths,
        selection: ExecutorSelection | None,
    ) -> ResolvedLegacyExecutor: ...


CandidateRunner = Callable[[AgosPaths, str | None], AutoExecutionResult]
CandidateReadiness = Callable[[AGOSConfig], list[str]]


class TaskExecutionService:
    """Validate, publish, dispatch, and normalize one governed task start."""

    def __init__(
        self,
        paths: AgosPaths,
        *,
        legacy_executor_factory: LegacyExecutorFactory,
        candidate_runner: CandidateRunner,
        candidate_readiness: CandidateReadiness | None = None,
        task_id_factory: Callable[[], str] = new_task_id,
    ) -> None:
        self.paths = paths
        self.legacy_executor_factory = legacy_executor_factory
        self.candidate_runner = candidate_runner
        self.candidate_readiness = candidate_readiness or (lambda _config: [])
        self.task_id_factory = task_id_factory

    def start(self, request: TaskExecutionRequest) -> TaskExecutionResult:
        prepared = self._validate_request(request)
        task, staging_paths = self._stage_task(request, prepared)
        if prepared.mode == "legacy":
            return self._start_legacy(task, staging_paths, request, prepared.warnings)

        self._publish(staging_paths.current_task)
        try:
            auto_result = self.candidate_runner(self.paths, None)
            return self._finalize_candidate(auto_result, warnings=prepared.warnings)
        except Exception as exc:
            self._persist_candidate_exception(task, exc, warnings=prepared.warnings)
            if isinstance(exc, TaskExecutionError):
                raise
            raise TaskExecutionError(str(exc)) from exc

    def resume_candidate(self) -> TaskExecutionResult:
        task = load_task(self.paths.task_yaml)
        if effective_task_mode(task) != "candidate":
            raise TaskExecutionError("active task is not a candidate-mode execution")
        previous = self.load_result()
        if previous.state == "completed":
            return previous
        try:
            auto_result = self.candidate_runner(self.paths, previous.run_id)
            return self._finalize_candidate(
                auto_result,
                warnings=previous.compatibility_warnings,
                resumed=True,
            )
        except Exception as exc:
            self._persist_candidate_exception(
                task,
                exc,
                warnings=previous.compatibility_warnings,
                run_id=previous.run_id,
                resumed=True,
            )
            if isinstance(exc, TaskExecutionError):
                raise
            raise TaskExecutionError(str(exc)) from exc

    def load_result(self) -> TaskExecutionResult:
        path = _result_path(self.paths)
        if not path.is_file():
            raise TaskExecutionError("active task execution result is missing")
        return TaskExecutionResult.model_validate_json(path.read_text(encoding="utf-8"))

    def _validate_request(self, request: TaskExecutionRequest) -> _PreparedStart:
        if current_task_is_active(self.paths.current_task):
            raise TaskExecutionError("Active task already exists in .agos/tasks/current")
        try:
            config = load_config(self.paths.root)
        except Exception as exc:
            raise TaskExecutionError(str(exc)) from exc
        mode = request.mode or config.task_execution.mode
        workflow = request.workflow or config.default_workflow
        try:
            gates = resolve_gates(
                config,
                workflow,
                override=request.gate_overrides or None,
            )
        except Exception as exc:
            raise TaskExecutionError(str(exc)) from exc

        warnings: list[str] = []
        if "task_execution" not in config.model_fields_set:
            warnings.append(
                "configuration omits task_execution; using compatible legacy defaults"
            )
        if mode == "candidate":
            issues = [
                *_candidate_configuration_issues(config),
                *self.candidate_readiness(config),
            ]
            if issues:
                raise TaskExecutionError("candidate mode is not ready: " + "; ".join(issues))
        return _PreparedStart(
            config=config,
            mode=mode,
            workflow=workflow,
            gates=gates,
            warnings=warnings,
        )

    def _stage_task(
        self,
        request: TaskExecutionRequest,
        prepared: _PreparedStart,
    ) -> tuple[Task, AgosPaths]:
        task_id = f"agos-{self.task_id_factory()}"
        selection = request.executor_selection
        task = Task(
            id=task_id,
            title=request.title.strip(),
            intent=request.intent,
            workflow=prepared.workflow,
            gates=[gate.id for gate in prepared.gates],
            executor=ExecutorBinding(
                adapter=selection.adapter if selection else prepared.config.executor.name,
                agent=selection.agent if selection else prepared.config.executor.agent,
                selection_id=(
                    selection.selection_id
                    if selection
                    else executor_selection_id(
                        prepared.config.executor.name,
                        prepared.config.executor.agent,
                    )
                ),
            ),
            execution_mode=prepared.mode,
            output_contract=prepared.config.task_execution.output_contract,
        )
        staging_dir = staging_task_dir(self.paths.root, task.id)
        shutil.rmtree(staging_dir, ignore_errors=True)
        staging_paths = task_paths(self.paths.root, staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)
        task.save(staging_paths.task_yaml)
        TaskState(staging_paths).record(
            TaskEvent(
                "task_started",
                {
                    "task_id": task.id,
                    "title": task.title,
                    "workflow": task.workflow,
                },
            ),
            TaskEvent(
                "gates_locked",
                {
                    "task_id": task.id,
                    "gates": gates_locked_payload(prepared.gates),
                },
            ),
            TaskEvent(
                "task_execution_started",
                {
                    "task_id": task.id,
                    "mode": prepared.mode,
                    "output_contract": task.output_contract,
                },
            ),
            expected=TaskRevision.empty(),
        )
        return task, staging_paths

    def _start_legacy(
        self,
        task: Task,
        staging_paths: AgosPaths,
        request: TaskExecutionRequest,
        warnings: list[str],
    ) -> TaskExecutionResult:
        try:
            resolved = self.legacy_executor_factory(
                staging_paths,
                request.executor_selection,
            )
            run = resolved.adapter.start(task)
            evidence_ref = f"runs/{run.run_id}.json"
            try:
                EvidenceStore(staging_paths.evidence).write_run(
                    run.run_id,
                    {
                        "task_id": task.id,
                        "adapter": run.adapter,
                        "run_id": run.run_id,
                        "issue_id": run.issue_id,
                    },
                )
            except Exception as exc:
                self._preserve_unreconciled_legacy_dispatch(
                    staging_paths,
                    task=task,
                    run=run,
                    stage="evidence_write_failed",
                    evidence_ref=evidence_ref,
                    cause=exc,
                )
                raise TaskExecutionDispatchUnreconciled(
                    task_id=task.id,
                    run_id=run.run_id,
                    stage="evidence_write_failed",
                    cause=exc,
                ) from exc
            task_state = TaskState(staging_paths)
            snapshot = task_state.current()
            if snapshot is None:
                raise TaskExecutionError("staged task state is missing")
            try:
                commit = task_state.record(
                    TaskEvent(
                        "executor_dispatched",
                        {
                            "task_id": task.id,
                            "adapter": run.adapter,
                            "run_id": run.run_id,
                            "issue_id": run.issue_id,
                        },
                    ),
                    expected=snapshot.revision,
                )
            except Exception as exc:
                stage = _unreconciled_dispatch_stage(exc)
                self._preserve_unreconciled_legacy_dispatch(
                    staging_paths,
                    task=task,
                    run=run,
                    stage=stage,
                    evidence_ref=evidence_ref,
                    cause=exc,
                )
                raise TaskExecutionDispatchUnreconciled(
                    task_id=task.id,
                    run_id=run.run_id,
                    stage=stage,
                    cause=exc,
                ) from exc
            self._publish(staging_paths.current_task)
            task_state = TaskState(self.paths)
            run_status = _initial_status(resolved, run)
            state = "running"
            blocked_stage = None
            blocked_reason = None
            if run_status is not None and run_status.state != "running":
                state, event_type, _phase = _legacy_terminal_state(run_status)
                commit = task_state.record(
                    TaskEvent(
                        event_type,
                        {
                            "task_id": task.id,
                            "run_id": run.run_id,
                            "issue_id": run.issue_id,
                            "state": run_status.state,
                            "detail": run_status.detail,
                        },
                    ),
                    expected=commit.snapshot.revision,
                )
                if state != "completed":
                    blocked_stage = "executor"
                    blocked_reason = run_status.detail or run_status.state

            result = TaskExecutionResult(
                task_id=task.id,
                mode="legacy",
                run_id=run.run_id,
                issue_id=run.issue_id,
                state=state,
                blocked_stage=blocked_stage,
                blocked_reason=blocked_reason,
                compatibility_warnings=warnings,
            )
            if state != "running":
                task_state.record(
                    TaskEvent(
                        "task_execution_completed"
                        if state == "completed"
                        else "task_execution_blocked",
                        {
                            "task_id": task.id,
                            "mode": "legacy",
                            "run_id": run.run_id,
                            "state": state,
                            "blocked_stage": blocked_stage,
                            "blocked_reason": blocked_reason,
                        },
                    ),
                    expected=commit.snapshot.revision,
                )
            _write_result(self.paths, result)
            return result
        except TaskExecutionDispatchUnreconciled:
            raise
        except Exception as exc:
            shutil.rmtree(staging_paths.current_task, ignore_errors=True)
            if isinstance(exc, TaskExecutionError):
                raise
            raise TaskExecutionError(str(exc)) from exc

    def _preserve_unreconciled_legacy_dispatch(
        self,
        staging_paths: AgosPaths,
        *,
        task: Task,
        run: ExecutorRun,
        stage: str,
        evidence_ref: str,
        cause: Exception,
    ) -> None:
        try:
            TaskState(staging_paths).record(
                TaskEvent(
                    "executor_dispatch_unreconciled",
                    {
                        "task_id": task.id,
                        "adapter": run.adapter,
                        "run_id": run.run_id,
                        "issue_id": run.issue_id,
                        "triggered_by": "task_execution_start",
                        "stage": stage,
                        "evidence_ref": evidence_ref,
                        "error": str(cause),
                    },
                )
            )
        except Exception as recovery_error:
            raise TaskExecutionDispatchUnreconciled(
                task_id=task.id,
                run_id=run.run_id,
                stage=f"{stage}_recovery_failed",
                cause=recovery_error,
            ) from recovery_error
        try:
            self._publish(staging_paths.current_task)
        except Exception as publish_error:
            raise TaskExecutionDispatchUnreconciled(
                task_id=task.id,
                run_id=run.run_id,
                stage="publish_failed",
                cause=publish_error,
            ) from publish_error

    def _finalize_candidate(
        self,
        auto_result: AutoExecutionResult,
        *,
        warnings: list[str],
        resumed: bool = False,
    ) -> TaskExecutionResult:
        task_state = TaskState(self.paths)
        snapshot = task_state.current()
        if snapshot is None:
            raise TaskExecutionError("candidate task status is missing")
        task = snapshot.task
        if auto_result.task_id != task.id:
            raise TaskExecutionError(
                f"candidate pipeline task mismatch: {auto_result.task_id!r} != {task.id!r}"
            )
        state = _candidate_state(auto_result)
        result = TaskExecutionResult(
            task_id=task.id,
            mode="candidate",
            run_id=auto_result.run_id,
            state=state,
            candidate_ids=list(auto_result.candidate_ids),
            applied_candidate_ids=list(auto_result.applied_candidate_ids),
            blocked_stage=auto_result.blocked_stage,
            blocked_reason=auto_result.blocked_reason,
            compatibility_warnings=warnings,
        )
        task_state.record(
            TaskEvent(
                "task_execution_completed"
                if state == "completed"
                else "task_execution_blocked",
                {
                    "task_id": task.id,
                    "mode": "candidate",
                    "run_id": auto_result.run_id,
                    "state": state,
                    "candidate_ids": result.candidate_ids,
                    "applied_candidate_ids": result.applied_candidate_ids,
                    "blocked_stage": result.blocked_stage,
                    "blocked_reason": result.blocked_reason,
                    "resumed": resumed,
                },
            ),
            expected=snapshot.revision,
        )
        _write_result(self.paths, result)
        return result

    def _persist_candidate_exception(
        self,
        task: Task,
        exc: Exception,
        *,
        warnings: list[str],
        run_id: str | None = None,
        resumed: bool = False,
    ) -> TaskExecutionResult:
        result = TaskExecutionResult(
            task_id=task.id,
            mode="candidate",
            run_id=run_id or f"candidate-start-{task.id}",
            state="failed",
            blocked_stage="candidate_pipeline",
            blocked_reason=str(exc),
            compatibility_warnings=warnings,
        )
        task_state = TaskState(self.paths)
        snapshot = task_state.current()
        if snapshot is None:
            raise TaskExecutionError("candidate task status is missing") from exc
        task_state.record(
            TaskEvent(
                "task_execution_blocked",
                {
                    "task_id": task.id,
                    "mode": "candidate",
                    "run_id": result.run_id,
                    "state": "failed",
                    "blocked_stage": result.blocked_stage,
                    "blocked_reason": result.blocked_reason,
                    "resumed": resumed,
                },
            ),
            expected=snapshot.revision,
        )
        _write_result(self.paths, result)
        return result

    def _publish(self, staging_dir: Path) -> None:
        shutil.rmtree(self.paths.current_task, ignore_errors=True)
        self.paths.current_task.parent.mkdir(parents=True, exist_ok=True)
        staging_dir.rename(self.paths.current_task)


@dataclass(frozen=True)
class _PreparedStart:
    config: AGOSConfig
    mode: ExecutionMode
    workflow: str
    gates: list[GateSpec]
    warnings: list[str]


def _candidate_configuration_issues(config: AGOSConfig) -> list[str]:
    issues: list[str] = []
    automatic_worker_types = {"command", "codex_cli", "claude_code", "multica", "openhands"}
    if not any(worker.type in automatic_worker_types for worker in config.workers.values()):
        issues.append("candidate mode requires an automatic worker")
    automatic_reviewers = [
        reviewer
        for reviewer in config.reviewers.values()
        if reviewer.type in {"codex_cli", "claude_code"}
        or (reviewer.type == "fake" and config.allow_fake_reviewer)
    ]
    if not automatic_reviewers:
        issues.append("candidate mode requires an automatic reviewer")
    return issues


def _initial_status(
    resolved: ResolvedLegacyExecutor,
    run: ExecutorRun,
) -> RunStatus | None:
    if not resolved.synchronous:
        return None
    try:
        return resolved.adapter.status(run.run_id, issue_id=run.issue_id)
    except Exception:
        return None


def _unreconciled_dispatch_stage(exc: Exception) -> str:
    if isinstance(exc, TaskStateConflict):
        return "ledger_conflict"
    if isinstance(exc, TaskStateCommitIndeterminate):
        return "ledger_commit_indeterminate"
    return "ledger_write_failed"


def _legacy_terminal_state(status: RunStatus) -> tuple[str, str, str]:
    if status.state == "completed":
        return "completed", "executor_completed", "done"
    if status.state == "blocked":
        return "blocked", "executor_blocked", "blocked"
    return "failed", "executor_blocked", "blocked"


def _candidate_state(result: AutoExecutionResult) -> str:
    if result.run_state == "stuck":
        return "stuck"
    if result.blocked_stage is not None:
        return "blocked"
    if result.run_state in {"failed", "cancelled"}:
        return "failed"
    if result.run_state in {"queued", "running"}:
        return "running"
    return "completed"


def _result_path(paths: AgosPaths) -> Path:
    return paths.current_task / "execution" / "task-execution.json"


def _write_result(paths: AgosPaths, result: TaskExecutionResult) -> None:
    path = _result_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
