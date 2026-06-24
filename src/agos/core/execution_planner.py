"""Automatic execution-plan generation and validation."""
from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import ValidationError

from agos.core.config import AGOSConfig, WorkerConfig
from agos.core.execution import ExecutionPlan
from agos.core.task import Task


def create_execution_plan(
    task: Task,
    config: AGOSConfig,
    available_workers: Mapping[str, WorkerConfig] | Mapping[str, object],
    *,
    planner_json: str | None = None,
) -> ExecutionPlan:
    """Create a conservative execution plan for an active task.

    Invalid JSON from an external planner falls back to the deterministic MVP
    plan. Valid JSON is normalized to the active task id and then fully
    validated; structurally impossible plans are rejected instead of silently
    rewritten.
    """

    worker_names = list(available_workers)
    if planner_json is not None:
        try:
            payload = json.loads(planner_json)
        except json.JSONDecodeError:
            return _fallback_plan(task, config, worker_names)
        if not isinstance(payload, dict):
            raise ValueError("planner output must be a JSON object")
        payload = dict(payload)
        payload["task_id"] = task.id
        try:
            plan = ExecutionPlan.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc
        _validate_plan_workers(plan, worker_names)
        return plan

    return _fallback_plan(task, config, worker_names)


def _fallback_plan(task: Task, config: AGOSConfig, worker_names: list[str]) -> ExecutionPlan:
    worker = worker_names[0] if worker_names else "local_worktree"
    return ExecutionPlan.model_validate(
        {
            "id": f"auto-plan-{task.id}",
            "task_id": task.id,
            "max_parallel": config.orchestration.max_parallel,
            "requires_candidate_review": True,
            "subtasks": [
                {
                    "id": f"auto-subtask-{task.id}",
                    "title": task.title,
                    "intent": task.intent,
                    "depends_on": [],
                    "write_scope": _fallback_write_scope(),
                    "worker": {"adapter": worker, "role": "worker_agent"},
                }
            ],
        }
    )


def _fallback_write_scope() -> list[str]:
    # ExecutionSubtask rejects "." and the existing patch-scope guard matches
    # concrete paths, so README.md is the broadest safe default that also works
    # for the repository's MVP smoke path.
    return ["README.md"]


def _validate_plan_workers(plan: ExecutionPlan, worker_names: list[str]) -> None:
    allowed = set(worker_names)
    if not allowed:
        allowed.add("local_worktree")
    unknown = sorted(
        {subtask.worker.adapter for subtask in plan.subtasks if subtask.worker.adapter not in allowed}
    )
    if unknown:
        raise ValueError(f"execution plan references unknown worker: {', '.join(unknown)}")
