"""Deterministic structured-argv worker for offline automation."""
from __future__ import annotations

import subprocess
from pathlib import Path

from agos.adapters.workers._health import command_available_check
from agos.adapters.workers.transport import worker_env
from agos.core.command import run_command
from agos.core.execution_worker import (
    WorkerAssignment,
    WorkerHealth,
    WorkerPreparedWorkspace,
    WorkerRun,
    WorkerRunStatus,
    WorkerStartRequest,
    WorkerWorkspaceHandle,
)
from agos.core.execution_workspace import ExecutionWorkspaceManager


class CommandWorkerAdapter:
    """Run an explicit local command inside an AGOS-owned worktree."""

    def __init__(
        self,
        *,
        name: str,
        argv: list[str] | tuple[str, ...],
        workspace_manager: ExecutionWorkspaceManager,
        timeout_seconds: int = 30,
        env: dict[str, str] | None = None,
    ) -> None:
        if not argv or any(not str(item).strip() for item in argv):
            raise ValueError("command worker argv must contain non-empty strings")
        self.name = name
        self.argv = tuple(argv)
        self.workspace_manager = workspace_manager
        self.timeout_seconds = timeout_seconds
        self.env = dict(env or {})
        self._statuses: dict[str, WorkerRunStatus] = {}

    def health(self) -> WorkerHealth:
        return WorkerHealth(
            name=self.name,
            adapter="command",
            checks=[command_available_check(self.argv[0])],
            metadata={"timeout_seconds": str(self.timeout_seconds)},
        )

    def prepare(self, assignment: WorkerAssignment) -> WorkerPreparedWorkspace:
        binding = self.workspace_manager.create_workspace(assignment.subtask)
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
        workspace = Path(handle.metadata["workspace_path"])
        return {"patch_bytes": self.workspace_manager.capture_patch(workspace)}

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        state = "completed"
        detail: str | None = None
        try:
            proc = run_command(
                list(self.argv),
                cwd=Path(request.workspace_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=self.timeout_seconds,
                stdin=subprocess.DEVNULL,
                env=worker_env(self.env),
            )
            if proc.returncode != 0:
                state = "failed"
            detail = _process_detail(proc)
        except subprocess.TimeoutExpired as exc:
            state = "failed"
            timeout = exc.timeout or self.timeout_seconds
            detail = f"command worker timed out after {timeout:g} seconds"
        except OSError as exc:
            state = "failed"
            detail = f"command worker failed: {exc}"

        status = WorkerRunStatus(
            backend=self.name,
            run_id=request.run_id,
            subtask_id=request.subtask_id,
            state=state,
            detail=detail,
        )
        self._statuses[request.run_id] = status
        return WorkerRun(
            backend=self.name,
            run_id=request.run_id,
            subtask_id=request.subtask_id,
            state=state,
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        return self._statuses.get(
            run_id,
            WorkerRunStatus(
                backend=self.name,
                run_id=run_id,
                subtask_id=subtask_id,
                state="failed",
                detail="unknown command worker run",
            ),
        )

    def cancel(self, run_id: str) -> WorkerRunStatus:
        existing = self._statuses.get(run_id)
        if existing is not None and existing.is_terminal:
            return existing
        status = WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=existing.subtask_id if existing is not None else "unknown",
            state="cancelled",
        )
        self._statuses[run_id] = status
        return status


def _process_detail(proc) -> str | None:
    stderr = getattr(proc, "stderr", "") or ""
    stdout = getattr(proc, "stdout", "") or ""
    detail = str(stderr or stdout).strip()
    return detail or None
