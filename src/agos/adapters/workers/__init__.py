"""Built-in worker adapters."""
from agos.adapters.workers.fake import FakeWorkerAdapter
from agos.adapters.workers.local_worktree import LocalWorktreeWorkerAdapter
from agos.core.execution_worker import (
    WorkerAssignment,
    WorkerPreparedWorkspace,
    WorkerWorkspaceHandle,
)

__all__ = [
    "FakeWorkerAdapter",
    "LocalWorktreeWorkerAdapter",
    "WorkerAssignment",
    "WorkerPreparedWorkspace",
    "WorkerWorkspaceHandle",
]
