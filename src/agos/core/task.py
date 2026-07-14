"""Task definition models (`task.yaml`)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from ulid import ULID

from agos.core.task_execution import ExecutionMode, OutputContract


class ExecutorBinding(BaseModel):
    """Which executor adapter and agent the task is bound to."""

    adapter: str
    agent: str
    selection_id: str | None = None


class Task(BaseModel):
    """A single governed task serialized to `task.yaml`."""

    id: str
    title: str
    intent: str = ""
    acceptance: list[str] = Field(default_factory=list)
    workflow: str = "feature"
    gates: list[str] = Field(default_factory=list)
    executor: ExecutorBinding
    execution_mode: ExecutionMode | None = None
    output_contract: OutputContract | None = None

    @field_validator("title")
    @classmethod
    def _nonempty_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must be non-empty")
        return value

    def save(self, path: Path) -> None:
        save_task(self, path)


TaskConfig = Task


def new_task_id() -> str:
    """Return a fresh ULID string for a task."""

    return str(ULID())


def task_output_ref(task: Task) -> str:
    """Return the default workspace-relative output directory for a task."""

    return f"outputs/{task.id}"


def save_task(task: Task, path: Path) -> None:
    """Write `task.yaml` in a stable, human-readable form."""

    path.parent.mkdir(parents=True, exist_ok=True)
    data = task.model_dump(exclude_none=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def load_task(path: Path) -> Task:
    """Read and validate `task.yaml`."""

    return Task.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
