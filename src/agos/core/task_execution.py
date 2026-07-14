"""Normalized task execution modes, requests, and results."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from agos.core.task import Task


ExecutionMode = Literal["legacy", "candidate"]
OutputContract = Literal["legacy", "source_code", "standalone"]
TaskExecutionState = Literal["running", "completed", "blocked", "failed", "stuck"]


class TaskExecutionConfig(BaseModel):
    """Default task entry mode and deliverable contract for new tasks."""

    mode: ExecutionMode = "legacy"
    output_contract: OutputContract = "legacy"


class ExecutorSelection(BaseModel):
    """Optional per-task executor/worker selection from a CLI or UI caller."""

    adapter: str
    agent: str
    selection_id: str | None = None
    command: str | None = None
    worker_adapter: str | None = None
    dangerously_bypass_permissions: bool = False


class TaskExecutionRequest(BaseModel):
    """One normalized request to create and execute a governed task."""

    title: str
    intent: str = ""
    workflow: str | None = None
    gate_overrides: list[str] = Field(default_factory=list)
    mode: ExecutionMode | None = None
    executor_selection: ExecutorSelection | None = None

    @field_validator("title")
    @classmethod
    def _nonempty_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must be non-empty")
        return value


class TaskExecutionResult(BaseModel):
    """Stable result shape shared by CLI and Dashboard task entrypoints."""

    task_id: str
    mode: ExecutionMode
    run_id: str
    state: TaskExecutionState
    issue_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    applied_candidate_ids: list[str] = Field(default_factory=list)
    blocked_stage: str | None = None
    blocked_reason: str | None = None
    compatibility_warnings: list[str] = Field(default_factory=list)


def effective_task_mode(task: Task) -> ExecutionMode:
    """Interpret missing v0.1 mode metadata without rewriting the task."""

    return task.execution_mode or "legacy"


def effective_output_contract(task: Task) -> OutputContract:
    """Interpret missing v0.1 output metadata as the legacy directory contract."""

    return task.output_contract or "legacy"


def task_requires_output_directory(task: Task) -> bool:
    """Return whether task success requires a standalone output directory."""

    return effective_output_contract(task) != "source_code"
