"""Local worktree worker adapter."""
from __future__ import annotations

from pathlib import Path

from agos.core.execution_workspace import ExecutionWorkspaceManager


class LocalWorktreeWorkerAdapter:
    name = "local_worktree"

    def __init__(self, manager: ExecutionWorkspaceManager) -> None:
        self.manager = manager

    def prepare(self, assignment):
        return self.manager.create_workspace(assignment)

    def export_candidate(self, handle):
        workspace_path = Path(handle.path if hasattr(handle, "path") else handle)
        patch_bytes = self.manager.capture_patch(workspace_path)
        return {"patch_bytes": patch_bytes}
