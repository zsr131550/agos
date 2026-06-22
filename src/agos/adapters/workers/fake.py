"""Fake worker adapter for tests."""
from __future__ import annotations

from dataclasses import dataclass

from agos.core.execution import WorkspaceBinding
from agos.adapters.workers.local_worktree import (
    WorkerAssignment,
    WorkerPreparedWorkspace,
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
