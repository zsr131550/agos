"""Executor adapter contracts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agos.core.task import Task


@dataclass(slots=True)
class ExecutorRun:
    """Opaque handle for a dispatched executor task."""

    adapter: str
    run_id: str
    issue_id: str | None = None


class ExecutorAdapter(Protocol):
    """Minimal dispatch contract for v0.1 commands."""

    name: str

    def start(self, task: Task) -> ExecutorRun:
        """Dispatch a task and return its executor handle."""

