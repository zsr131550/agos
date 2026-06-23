"""Local worktree worker adapter."""
from __future__ import annotations

from pathlib import Path

from agos.core.execution_worker import (
    WorkerHealth,
    WorkerHealthCheck,
    WorkerAssignment,
    WorkerPreparedWorkspace,
    WorkerRun,
    WorkerRunStatus,
    WorkerStartRequest,
    WorkerWorkspaceHandle,
)
from agos.core.execution_workspace import ExecutionWorkspaceManager


class LocalWorktreeWorkerAdapter:
    name = "local_worktree"

    def __init__(self, manager: ExecutionWorkspaceManager, *, name: str = "local_worktree") -> None:
        self.manager = manager
        self.name = name

    def health(self) -> WorkerHealth:
        return WorkerHealth(
            name=self.name,
            adapter="local_worktree",
            checks=[WorkerHealthCheck(name="local_workspace", state="passed", detail="ready")],
        )

    def prepare(self, assignment: WorkerAssignment) -> WorkerPreparedWorkspace:
        binding = self.manager.create_workspace(assignment.subtask)
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

    def export_candidate(self, handle: WorkerWorkspaceHandle) -> dict[str, bytes]:
        workspace_path = Path(handle.metadata["workspace_path"])
        patch_bytes = self.manager.capture_patch(workspace_path)
        return {"patch_bytes": patch_bytes}

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        return WorkerRun(
            backend=self.name,
            run_id=request.run_id,
            subtask_id=request.subtask_id,
            state="completed",
            metadata={"workspace_path": request.workspace_path},
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state="completed",
        )

    def cancel(self, run_id: str) -> WorkerRunStatus:
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id="unknown",
            state="cancelled",
        )
