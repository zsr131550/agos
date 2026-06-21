"""Executor seam types seen by the governance core."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from agos.core.task import Task


@dataclass(frozen=True)
class ExecutorRun:
    """Handle returned by an executor when a task is dispatched."""

    adapter: str
    run_id: str
    issue_id: str | None = None


@dataclass(frozen=True)
class Event:
    """One unit of executor activity captured as evidence."""

    seq: int
    ts: str
    kind: Literal["tool_call", "file_edit", "text", "error", "run_complete"]
    content: str
    raw: dict


@dataclass(frozen=True)
class RunStatus:
    """High-level executor run status."""

    state: Literal["running", "completed", "failed", "blocked"]
    detail: str | None = None


@runtime_checkable
class ExecutorAdapter(Protocol):
    """Executor interface consumed by the governance core."""

    name: str

    def start(self, task: Task) -> ExecutorRun: ...

    def stream_events(
        self,
        run_id: str,
        since: int | None = None,
    ) -> Iterator[Event]: ...

    def status(self, run_id: str) -> RunStatus: ...
