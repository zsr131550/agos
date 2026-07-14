"""Built-in worker adapters."""
from agos.adapters.workers.codex_cli import CodexWorkerAdapter
from agos.adapters.workers.claude_code import ClaudeWorkerAdapter
from agos.adapters.workers.command import CommandWorkerAdapter
from agos.adapters.workers.fake import FakeWorkerAdapter
from agos.adapters.workers.local_worktree import LocalWorktreeWorkerAdapter
from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
from agos.core.execution_worker import (
    WorkerAssignment,
    WorkerHealth,
    WorkerHealthCheck,
    WorkerPreparedWorkspace,
    WorkerRun,
    WorkerRunStatus,
    WorkerStartRequest,
    WorkerWorkspaceHandle,
)

__all__ = [
    "CodexWorkerAdapter",
    "ClaudeWorkerAdapter",
    "CommandWorkerAdapter",
    "FakeWorkerAdapter",
    "LocalWorktreeWorkerAdapter",
    "MulticaWorkerAdapter",
    "OpenHandsWorkerAdapter",
    "WorkerAssignment",
    "WorkerHealth",
    "WorkerHealthCheck",
    "WorkerPreparedWorkspace",
    "WorkerRun",
    "WorkerRunStatus",
    "WorkerStartRequest",
    "WorkerWorkspaceHandle",
]
