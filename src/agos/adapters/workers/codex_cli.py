"""Codex CLI execution worker adapter."""
from __future__ import annotations

import json
from pathlib import Path

from agos.adapters.workers._health import (
    command_available_check,
    probe_check,
    version_check,
)
from agos.adapters.workers.artifacts import collect_artifact_refs, merge_output_refs
from agos.adapters.workers.transport import (
    load_json_object,
    metadata_from_payload,
    output_refs_from_payload,
    run_worker_command,
)
from agos.adapters.noninteractive import noninteractive_prompt
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
        workspace_manager: ExecutionWorkspaceManager | None = None,
        manager: ExecutionWorkspaceManager | None = None,
        timeout_seconds: int = 30,
        poll_interval_seconds: int = 1,
        artifact_globs: tuple[str, ...] | list[str] = (),
        env: dict[str, str] | None = None,
        health_probe: bool = False,
    ) -> None:
        self.command = command
        self.name = name
        self.workspace_manager = workspace_manager if workspace_manager is not None else manager
        self.manager = self.workspace_manager
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.artifact_globs = tuple(artifact_globs)
        self.env = dict(env or {})
        self.health_probe = health_probe
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
        checks = [
            command_available_check(self.command),
            version_check(self.command, env=self.env),
        ]
        if self.health_probe:
            checks.append(
                probe_check(
                    self.command,
                    ["exec", "--json", "AGOS health probe: reply ok"],
                    env=self.env,
                )
            )
        return WorkerHealth(
            name=self.name,
            adapter="codex_cli",
            checks=checks,
            metadata={
                "command": self.command,
                "timeout_seconds": str(self.timeout_seconds),
                "poll_interval_seconds": str(self.poll_interval_seconds),
                "artifact_globs": ",".join(self.artifact_globs),
                "env_keys": ",".join(sorted(self.env)),
                "health_probe": str(self.health_probe),
            },
        )

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        proc = run_worker_command(
            [
                self.command,
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--dangerously-bypass-approvals-and-sandbox",
                "--json",
                noninteractive_prompt(request.prompt),
            ],
            action="codex exec",
            cwd=Path(request.workspace_path),
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            runner=run_command,
        )
        worker_run, cached_status = _exec_result(self.name, request, proc.stdout)
        run_id = worker_run.run_id
        self._subtasks_by_run_id[run_id] = request.subtask_id
        self._workspaces_by_run_id[run_id] = request.workspace_path
        if cached_status is not None:
            self._statuses_by_run_id[run_id] = cached_status
        return worker_run

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        cached = self._cached_status(run_id, subtask_id=subtask_id)
        if cached is not None:
            return cached
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
        cached = self._cached_status(
            run_id,
            subtask_id=self._subtasks_by_run_id.get(run_id, "unknown"),
        )
        if cached is not None and cached.is_terminal:
            return cached
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

    def _cached_status(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus | None:
        cached = self._statuses_by_run_id.get(run_id)
        if cached is None:
            return None
        self._subtasks_by_run_id[run_id] = subtask_id
        output_refs = merge_output_refs(
            cached.output_refs,
            collect_artifact_refs(self._workspaces_by_run_id.get(run_id), self.artifact_globs),
        )
        updated = cached.model_copy(
            update={
                "subtask_id": subtask_id,
                "output_refs": output_refs,
            }
        )
        self._statuses_by_run_id[run_id] = updated
        return updated

    def _workspace_manager(self, operation: str) -> ExecutionWorkspaceManager:
        if self.workspace_manager is None:
            raise RuntimeError(
                f"worker {self.name!r} requires a workspace manager to {operation}"
            )
        return self.workspace_manager


def _state(value: object, *, default: str) -> str:
    return STATE_MAP.get(str(value or default), default)


def _exec_result(
    backend: str,
    request: WorkerStartRequest,
    stdout: str,
) -> tuple[WorkerRun, WorkerRunStatus | None]:
    if _looks_like_jsonl(stdout):
        return _exec_jsonl_result(backend, request, stdout)
    payload = load_json_object(stdout, action="codex exec")
    run_id = str(payload.get("run_id") or payload.get("id") or request.run_id)
    return (
        WorkerRun(
            backend=backend,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state=_state(payload.get("state"), default="running"),
            metadata=metadata_from_payload(payload),
        ),
        None,
    )


def _looks_like_jsonl(stdout: str) -> bool:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    try:
        events = [json.loads(line) for line in lines]
    except json.JSONDecodeError:
        return False
    return all(isinstance(event, dict) for event in events)


def _exec_jsonl_result(
    backend: str,
    request: WorkerStartRequest,
    stdout: str,
) -> tuple[WorkerRun, WorkerRunStatus]:
    events = _load_jsonl(stdout, action="codex exec")
    run_id = _thread_id(events) or request.run_id
    detail = _last_agent_message(events) or _last_error_message(events)
    state = "failed" if _has_failed_event(events) else "completed"
    metadata = _exec_metadata(events)
    return (
        WorkerRun(
            backend=backend,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state="running",
            metadata=metadata,
        ),
        WorkerRunStatus(
            backend=backend,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state=state,
            detail=detail,
        ),
    )


def _load_jsonl(stdout: str, *, action: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{action} returned invalid JSONL on line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(event, dict):
            raise RuntimeError(f"{action} returned non-object JSONL on line {line_number}")
        events.append(event)
    if not events:
        raise RuntimeError(f"{action} returned empty JSONL")
    return events


def _thread_id(events: list[dict[str, object]]) -> str | None:
    for event in events:
        if event.get("type") == "thread.started" and event.get("thread_id") is not None:
            return str(event["thread_id"])
    return None


def _last_agent_message(events: list[dict[str, object]]) -> str | None:
    for event in reversed(events):
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agent_message" and item.get("text") is not None:
            return str(item["text"])
    return None


def _last_error_message(events: list[dict[str, object]]) -> str | None:
    for event in reversed(events):
        for key in ("error", "message", "detail"):
            value = event.get(key)
            if value is not None:
                return str(value)
    return None


def _has_failed_event(events: list[dict[str, object]]) -> bool:
    return any(str(event.get("type", "")).endswith(".failed") for event in events)


def _exec_metadata(events: list[dict[str, object]]) -> dict[str, str]:
    metadata = {"event_count": str(len(events))}
    for event in reversed(events):
        usage = event.get("usage")
        if isinstance(usage, dict):
            metadata.update({f"usage_{key}": str(value) for key, value in usage.items()})
            break
    return metadata


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

