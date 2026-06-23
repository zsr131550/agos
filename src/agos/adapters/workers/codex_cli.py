"""Codex CLI execution worker adapter."""
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
    WorkerHealth,
    WorkerHealthCheck,
    WorkerRun,
    WorkerRunStatus,
    WorkerStartRequest,
)


STATE_MAP = {
    "queued": "queued",
    "pending": "queued",
    "todo": "queued",
    "running": "running",
    "in_progress": "running",
    "done": "completed",
    "completed": "completed",
    "blocked": "blocked",
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
}


class CodexWorkerAdapter:
    """Run autonomous work through the `codex` CLI JSON boundary."""

    def __init__(
        self,
        *,
        command: str = "codex",
        name: str = "codex",
        timeout_seconds: int = 30,
        poll_interval_seconds: int = 1,
        artifact_globs: tuple[str, ...] | list[str] = (),
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.name = name
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.artifact_globs = tuple(artifact_globs)
        self.env = dict(env or {})
        self._subtasks_by_run_id: dict[str, str] = {}
        self._workspaces_by_run_id: dict[str, str] = {}

    def health(self) -> WorkerHealth:
        resolved = shutil.which(self.command)
        check = WorkerHealthCheck(
            name="command_available",
            state="passed" if resolved else "failed",
            detail=resolved or f"command not found: {self.command}",
        )
        return WorkerHealth(
            name=self.name,
            adapter="codex_cli",
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
            [self.command, "exec", "--json", request.prompt],
            action="codex exec",
            cwd=Path(request.workspace_path),
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            runner=run_command,
        )
        payload = load_json_object(proc.stdout, action="codex exec")
        run_id = str(payload.get("run_id") or payload.get("id") or request.run_id)
        self._subtasks_by_run_id[run_id] = request.subtask_id
        self._workspaces_by_run_id[run_id] = request.workspace_path
        return WorkerRun(
            backend=self.name,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state=_state(payload.get("state"), default="running"),
            metadata=metadata_from_payload(payload),
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        proc = run_worker_command(
            [self.command, "status", run_id, "--json"],
            action="codex status",
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            runner=run_command,
        )
        payload = load_json_object(proc.stdout, action="codex status")
        self._subtasks_by_run_id[run_id] = subtask_id
        return _status(
            self.name,
            run_id,
            subtask_id,
            payload,
            collect_artifact_refs(self._workspaces_by_run_id.get(run_id), self.artifact_globs),
        )

    def cancel(self, run_id: str) -> WorkerRunStatus:
        proc = run_worker_command(
            [self.command, "cancel", run_id, "--json"],
            action="codex cancel",
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            runner=run_command,
        )
        payload = load_json_object(proc.stdout, action="codex cancel")
        subtask_id = self._subtasks_by_run_id.get(run_id, str(payload.get("subtask_id", "unknown")))
        if "state" not in payload:
            payload["state"] = "cancelled"
        return _status(
            self.name,
            run_id,
            subtask_id,
            payload,
            collect_artifact_refs(self._workspaces_by_run_id.get(run_id), self.artifact_globs),
        )


def _state(value: object, *, default: str) -> str:
    return STATE_MAP.get(str(value or default), default)


def _status(
    backend: str,
    run_id: str,
    subtask_id: str,
    payload: dict[str, object],
    artifact_refs: list[str] | None = None,
) -> WorkerRunStatus:
    return WorkerRunStatus(
        backend=backend,
        run_id=run_id,
        subtask_id=subtask_id,
        state=_state(payload.get("state"), default="running"),
        detail=str(payload["detail"]) if payload.get("detail") is not None else None,
        output_refs=merge_output_refs(output_refs_from_payload(payload), artifact_refs or []),
    )

