"""Local worktree worker adapter."""
from __future__ import annotations

from pathlib import Path

from agos.core.execution_worker import WorkerAssignment, WorkerPreparedWorkspace, WorkerWorkspaceHandle
from agos.core.execution_workspace import ExecutionWorkspaceManager


class LocalWorktreeWorkerAdapter:
    name = "local_worktree"

    def __init__(self, manager: ExecutionWorkspaceManager) -> None:
        self.manager = manager

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
