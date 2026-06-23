"""Worker adapter seam for execution orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from agos.core.execution import ExecutionSubtask, WorkspaceBinding

WorkerRunState = Literal["queued", "running", "completed", "failed", "cancelled", "blocked"]
WorkerHealthCheckState = Literal["passed", "failed", "warning"]
WorkerHealthState = Literal["healthy", "unhealthy"]


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


class WorkerStartRequest(BaseModel):
    run_id: str
    subtask_id: str
    prompt: str
    workspace_path: str
    metadata: dict[str, str] = Field(default_factory=dict)


class WorkerHealthCheck(BaseModel):
    name: str
    state: WorkerHealthCheckState
    detail: str | None = None


class WorkerHealth(BaseModel):
    name: str
    adapter: str
    checks: list[WorkerHealthCheck]
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def state(self) -> WorkerHealthState:
        return "unhealthy" if any(check.state == "failed" for check in self.checks) else "healthy"

    @property
    def is_healthy(self) -> bool:
        return self.state == "healthy"


class WorkerReadinessError(RuntimeError):
    """Raised when a worker adapter is not ready to start real work."""


def ensure_worker_ready(adapter: "ExecutionWorkerAdapter") -> WorkerHealth:
    """Return adapter health or raise a clear readiness error."""

    try:
        health = adapter.health()
    except Exception as exc:
        name = getattr(adapter, "name", "unknown")
        raise WorkerReadinessError(
            f"worker {name!r} is not ready: health_check failed: {exc}"
        ) from exc

    failed = [check for check in health.checks if check.state == "failed"]
    if failed:
        details = "; ".join(_failed_check_detail(check) for check in failed)
        raise WorkerReadinessError(f"worker {health.name!r} is not ready: {details}")
    return health


def _failed_check_detail(check: WorkerHealthCheck) -> str:
    detail = f": {check.detail}" if check.detail else ""
    return f"{check.name} failed{detail}"


class WorkerRun(BaseModel):
    backend: str
    run_id: str
    subtask_id: str
    state: WorkerRunState
    metadata: dict[str, str] = Field(default_factory=dict)


class WorkerRunStatus(BaseModel):
    backend: str
    run_id: str
    subtask_id: str
    state: WorkerRunState
    detail: str | None = None
    output_refs: list[str] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.state in {"completed", "failed", "cancelled", "blocked"}


class ExecutionWorkerAdapter(Protocol):
    name: str

    def prepare(self, assignment: WorkerAssignment) -> WorkspaceBinding | WorkerPreparedWorkspace: ...

    def health(self) -> WorkerHealth: ...

    def start(self, request: WorkerStartRequest) -> WorkerRun: ...

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus: ...

    def cancel(self, run_id: str) -> WorkerRunStatus: ...

    def export_candidate(self, handle: WorkerWorkspaceHandle) -> dict[str, bytes]: ...
