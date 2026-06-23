"""Claude Code CLI execution worker adapter."""
from __future__ import annotations

import shutil
from pathlib import Path

from agos.adapters.workers.artifacts import collect_artifact_refs, merge_output_refs
from agos.adapters.workers.transport import (
    load_json_object,
    metadata_from_payload,
    output_refs_from_payload,
    run_worker_command,
)
from agos.core.command import run_command
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
from agos.core.execution_workspace import ExecutionWorkspaceManager


class ClaudeWorkerAdapter:
    """Run autonomous work through the `claude` CLI JSON boundary."""

    def __init__(
        self,
        *,
        command: str = "claude",
        name: str = "claude",
        workspace_manager: ExecutionWorkspaceManager | None = None,
        manager: ExecutionWorkspaceManager | None = None,
        timeout_seconds: int = 30,
        poll_interval_seconds: int = 1,
        artifact_globs: tuple[str, ...] | list[str] = (),
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.name = name
        self.workspace_manager = workspace_manager if workspace_manager is not None else manager
        self.manager = self.workspace_manager
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.artifact_globs = tuple(artifact_globs)
        self.env = dict(env or {})
        self._subtasks_by_run_id: dict[str, str] = {}
        self._workspaces_by_run_id: dict[str, str] = {}
        self._statuses_by_run_id: dict[str, WorkerRunStatus] = {}

    def prepare(self, assignment: WorkerAssignment) -> WorkerPreparedWorkspace:
        manager = self._workspace_manager("prepare")
        binding = manager.create_workspace(assignment.subtask)
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
        manager = self._workspace_manager("export_candidate")
        patch_bytes = manager.capture_patch(Path(handle.metadata["workspace_path"]))
        return {"patch_bytes": patch_bytes}

    def health(self) -> WorkerHealth:
        resolved = shutil.which(self.command)
        check = WorkerHealthCheck(
            name="command_available",
            state="passed" if resolved else "failed",
            detail=resolved or f"command not found: {self.command}",
        )
        return WorkerHealth(
            name=self.name,
            adapter="claude_code",
            checks=[check],
            metadata={
                "command": self.command,
                "timeout_seconds": str(self.timeout_seconds),
                "poll_interval_seconds": str(self.poll_interval_seconds),
                "artifact_globs": ",".join(self.artifact_globs),
                "env_keys": ",".join(sorted(self.env)),
            },
        )

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        proc = run_worker_command(
            [self.command, "-p", "--output-format", "json", request.prompt],
            action="claude -p",
            cwd=Path(request.workspace_path),
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            runner=run_command,
        )
        payload = load_json_object(proc.stdout, action="claude -p")
        run_id = str(payload.get("session_id") or payload.get("run_id") or request.run_id)
        self._subtasks_by_run_id[run_id] = request.subtask_id
        self._workspaces_by_run_id[run_id] = request.workspace_path
        self._statuses_by_run_id[run_id] = _status(
            self.name,
            run_id,
            request.subtask_id,
            payload,
            collect_artifact_refs(request.workspace_path, self.artifact_globs),
        )
        return WorkerRun(
            backend=self.name,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state="running",
            metadata=metadata_from_payload(payload),
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        cached = self._statuses_by_run_id.get(run_id)
        if cached is None:
            return WorkerRunStatus(
                backend=self.name,
                run_id=run_id,
                subtask_id=subtask_id,
                state="failed",
                detail="claude worker run status is unavailable",
            )
        refs = collect_artifact_refs(self._workspaces_by_run_id.get(run_id), self.artifact_globs)
        updated = cached.model_copy(
            update={
                "subtask_id": subtask_id,
                "output_refs": merge_output_refs(cached.output_refs, refs),
            }
        )
        self._statuses_by_run_id[run_id] = updated
        return updated

    def cancel(self, run_id: str) -> WorkerRunStatus:
        subtask_id = self._subtasks_by_run_id.get(run_id, "unknown")
        cached = self._statuses_by_run_id.get(run_id)
        if cached is not None and cached.is_terminal:
            return cached
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state="cancelled",
        )

    def _workspace_manager(self, operation: str) -> ExecutionWorkspaceManager:
        if self.workspace_manager is None:
            raise RuntimeError(
                f"worker {self.name!r} requires a workspace manager to {operation}"
            )
        return self.workspace_manager


def _status(
    backend: str,
    run_id: str,
    subtask_id: str,
    payload: dict[str, object],
    artifact_refs: list[str] | None = None,
) -> WorkerRunStatus:
    state = "completed" if str(payload.get("is_error", "false")).lower() != "true" else "failed"
    detail = payload.get("result") or payload.get("detail")
    return WorkerRunStatus(
        backend=backend,
        run_id=run_id,
        subtask_id=subtask_id,
        state=state,
        detail=str(detail) if detail is not None else None,
        output_refs=merge_output_refs(output_refs_from_payload(payload), artifact_refs or []),
    )
