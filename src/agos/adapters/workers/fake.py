"""Fake worker adapter for tests."""
from __future__ import annotations

from dataclasses import dataclass

from agos.core.execution import WorkspaceBinding
from agos.core.execution_worker import (
    WorkerAssignment,
    WorkerPreparedWorkspace,
    WorkerRun,
    WorkerRunStatus,
    WorkerStartRequest,
    WorkerWorkspaceHandle,
)


@dataclass(frozen=True)
class FakeWorkerHandle:
    subtask_id: str
    workspace_path: str
    patch_bytes: bytes = b""


class FakeWorkerAdapter:
    name = "fake"

    def __init__(self, patch_bytes: bytes = b"") -> None:
        self.patch_bytes = patch_bytes
        self._runs: dict[str, WorkerRunStatus] = {}

    def prepare(self, assignment: WorkerAssignment) -> WorkerPreparedWorkspace:
        binding = WorkspaceBinding(
            subtask_id=assignment.subtask.id,
            path=f"fake/{assignment.subtask.id}",
            base_ref="fake",
            base_commit="fake",
        )
        return WorkerPreparedWorkspace(
            binding=binding,
            handle=WorkerWorkspaceHandle(
                subtask_id=assignment.subtask.id,
                metadata={
                    "workspace_path": binding.path,
                    "workspace_ref": binding.ref,
                },
            ),
        )

    def export_candidate(self, handle: WorkerWorkspaceHandle):
        patch_bytes = self.patch_bytes
        return {"patch_bytes": patch_bytes}

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        self._runs[request.run_id] = WorkerRunStatus(
            backend=self.name,
            run_id=request.run_id,
            subtask_id=request.subtask_id,
            state="completed",
        )
        return WorkerRun(
            backend=self.name,
            run_id=request.run_id,
            subtask_id=request.subtask_id,
            state="completed",
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        return self._runs.get(
            run_id,
            WorkerRunStatus(
                backend=self.name,
                run_id=run_id,
                subtask_id=subtask_id,
                state="failed",
                detail="unknown fake worker run",
            ),
        )

    def cancel(self, run_id: str) -> WorkerRunStatus:
        previous = self._runs.get(run_id)
        status = WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=previous.subtask_id if previous else "unknown",
            state="cancelled",
        )
        self._runs[run_id] = status
        return status
