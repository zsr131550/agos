"""Worker adapter seam for execution orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agos.core.execution import ExecutionSubtask, WorkspaceBinding


@dataclass(frozen=True)
class WorkerAssignment:
    subtask: ExecutionSubtask


@dataclass(frozen=True)
class WorkerWorkspaceHandle:
    subtask_id: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class WorkerPreparedWorkspace:
    binding: WorkspaceBinding
    handle: WorkerWorkspaceHandle


class ExecutionWorkerAdapter(Protocol):
    name: str

    def prepare(self, assignment: WorkerAssignment) -> WorkspaceBinding | WorkerPreparedWorkspace: ...

    def export_candidate(self, handle: WorkerWorkspaceHandle) -> dict[str, bytes]: ...
