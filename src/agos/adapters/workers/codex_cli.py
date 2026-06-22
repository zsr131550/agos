"""Codex CLI execution worker adapter."""
from __future__ import annotations

import json
import os
from pathlib import Path

from agos.adapters.workers.artifacts import collect_artifact_refs, merge_output_refs
from agos.core.command import run_command
from agos.core.execution_worker import WorkerRun, WorkerRunStatus, WorkerStartRequest


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

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        proc = run_command(
            [self.command, "exec", "--json", request.prompt],
            cwd=Path(request.workspace_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout_seconds,
            env={**os.environ, **self.env},
        )
        _raise_on_failure(proc, "codex exec")
        payload = _load_json(proc.stdout)
        run_id = str(payload.get("run_id") or payload.get("id") or request.run_id)
        self._subtasks_by_run_id[run_id] = request.subtask_id
        self._workspaces_by_run_id[run_id] = request.workspace_path
        return WorkerRun(
            backend=self.name,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state=_state(payload.get("state"), default="running"),
            metadata=_metadata(payload),
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        proc = run_command(
            [self.command, "status", run_id, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout_seconds,
            env={**os.environ, **self.env},
        )
        _raise_on_failure(proc, "codex status")
        payload = _load_json(proc.stdout)
        self._subtasks_by_run_id[run_id] = subtask_id
        return _status(
            self.name,
            run_id,
            subtask_id,
            payload,
            collect_artifact_refs(self._workspaces_by_run_id.get(run_id), self.artifact_globs),
        )

    def cancel(self, run_id: str) -> WorkerRunStatus:
        proc = run_command(
            [self.command, "cancel", run_id, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=self.timeout_seconds,
            env={**os.environ, **self.env},
        )
        _raise_on_failure(proc, "codex cancel")
        payload = _load_json(proc.stdout)
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


def _load_json(stdout: str) -> dict[str, object]:
    if not stdout.strip():
        return {}
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("codex CLI returned non-object JSON")
    return payload


def _raise_on_failure(proc, action: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(f"{action} failed with exit {proc.returncode}: {proc.stderr.strip()}")


def _state(value: object, *, default: str) -> str:
    return STATE_MAP.get(str(value or default), default)


def _status(
    backend: str,
    run_id: str,
    subtask_id: str,
    payload: dict[str, object],
    artifact_refs: list[str] | None = None,
) -> WorkerRunStatus:
    output_refs = payload.get("output_refs", [])
    if not isinstance(output_refs, list):
        output_refs = []
    return WorkerRunStatus(
        backend=backend,
        run_id=run_id,
        subtask_id=subtask_id,
        state=_state(payload.get("state"), default="running"),
        detail=str(payload["detail"]) if payload.get("detail") is not None else None,
        output_refs=merge_output_refs([str(ref) for ref in output_refs], artifact_refs or []),
    )


def _metadata(payload: dict[str, object]) -> dict[str, str]:
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    return {str(key): str(value) for key, value in metadata.items()}

