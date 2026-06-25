"""Automatic execution-plan generation and validation."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

from pydantic import ValidationError

from agos.core.config import AGOSConfig, WorkerConfig
from agos.core.execution import ExecutionPlan
from agos.core.json_text import load_json_object_from_text
from agos.core.task import Task


class PlannerAdapter(Protocol):
    """Planner backend that returns ExecutionPlan JSON for the active task."""

    def plan_json(self, task: Task, available_workers: list[str]) -> str: ...


def create_execution_plan(
    task: Task,
    config: AGOSConfig,
    available_workers: Mapping[str, WorkerConfig] | Mapping[str, object],
    *,
    planner_json: str | None = None,
    planner: PlannerAdapter | None = None,
) -> ExecutionPlan:
    """Create a conservative execution plan for an active task.

    Priority: an explicit ``planner_json`` argument wins, then an LLM planner
    adapter (when enabled), then the deterministic fallback plan. Invalid JSON
    from an external planner falls back to the deterministic MVP plan. Valid JSON
    is normalized to the active task id and then fully validated; structurally
    impossible plans are rejected instead of silently rewritten.
    """

    worker_names = list(available_workers)
    if planner_json is None and planner is not None and config.orchestration.planner.enabled:
        planner_json = _safe_llm_plan(planner, task, worker_names)
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


def _safe_llm_plan(planner: PlannerAdapter, task: Task, worker_names: list[str]) -> str | None:
    """Ask the LLM planner for a plan, returning JSON text or None on failure.

    CLI absence, timeout, non-zero exit, or output without a JSON object all
    return None so the caller falls back to the deterministic plan. A JSON
    object that is not a valid ExecutionPlan is intentionally NOT masked here: it
    surfaces as a ValueError in the normal planner_json validation path, since a
    structured-but-invalid plan indicates an LLM error rather than an unavailable
    CLI and must not silently downgrade.
    """

    try:
        stdout = planner.plan_json(task, worker_names)
    except Exception:
        return None
    payload = load_json_object_from_text(stdout)
    if payload is None:
        return None
    return json.dumps(payload)


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
                    "write_scope": _fallback_write_scope(config),
                    "worker": {"adapter": worker, "role": "worker_agent"},
                }
            ],
        }
    )


def _fallback_write_scope(config: AGOSConfig) -> list[str]:
    # ExecutionSubtask rejects ".". Use explicit top-level project boundaries so
    # fallback automation can work on code, tests, and docs without opening the
    # whole repository. The set is configurable so deployments can widen it.
    return list(config.orchestration.fallback_write_scope)


def _validate_plan_workers(plan: ExecutionPlan, worker_names: list[str]) -> None:
    allowed = set(worker_names)
    if not allowed:
        allowed.add("local_worktree")
    unknown = sorted(
        {subtask.worker.adapter for subtask in plan.subtasks if subtask.worker.adapter not in allowed}
    )
    if unknown:
        raise ValueError(f"execution plan references unknown worker: {', '.join(unknown)}")
