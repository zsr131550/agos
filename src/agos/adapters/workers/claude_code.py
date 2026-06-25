"""Claude Code CLI execution worker adapter."""
from __future__ import annotations

import re
from pathlib import Path

from agos.adapters.workers._health import (
    command_available_check,
    probe_check,
    version_check,
)
from agos.adapters.workers.artifacts import collect_artifact_refs, merge_output_refs
from agos.adapters.workers.transport import (
    load_json_list,
    load_json_object,
    metadata_from_payload,
    output_refs_from_payload,
    run_worker_command,
)
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
        health_probe: bool = False,
        claude_async_poll: bool = False,
        claude_resume_on_complete: bool = False,
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
        self.claude_async_poll = claude_async_poll
        self.claude_resume_on_complete = claude_resume_on_complete
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
                    ["-p", "--output-format", "json", "AGOS health probe: reply ok"],
                    env=self.env,
                )
            )
        return WorkerHealth(
            name=self.name,
            adapter="claude_code",
            checks=checks,
            metadata={
                "command": self.command,
                "timeout_seconds": str(self.timeout_seconds),
                "poll_interval_seconds": str(self.poll_interval_seconds),
                "artifact_globs": ",".join(self.artifact_globs),
                "env_keys": ",".join(sorted(self.env)),
                "health_probe": str(self.health_probe),
                "claude_async_poll": str(self.claude_async_poll),
                "claude_resume_on_complete": str(self.claude_resume_on_complete),
            },
        )

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        if self.claude_async_poll:
            return self._start_async(request)
        return self._start_sync(request)

    def _start_sync(self, request: WorkerStartRequest) -> WorkerRun:
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
        status = _status(
            self.name,
            run_id,
            request.subtask_id,
            payload,
            collect_artifact_refs(request.workspace_path, self.artifact_globs),
        )
        self._statuses_by_run_id[run_id] = status
        # `claude -p` blocks until the turn finishes, so start reports the real
        # outcome rather than a placeholder "running" state.
        return WorkerRun(
            backend=self.name,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state=status.state,
            metadata=metadata_from_payload(payload),
        )

    def _start_async(self, request: WorkerStartRequest) -> WorkerRun:
        proc = run_worker_command(
            [self.command, "-p", "--bg", request.prompt],
            action="claude -p --bg",
            cwd=Path(request.workspace_path),
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            runner=run_command,
        )
        run_id = _parse_bg_id(proc.stdout)
        self._subtasks_by_run_id[run_id] = request.subtask_id
        self._workspaces_by_run_id[run_id] = request.workspace_path
        return WorkerRun(
            backend=self.name,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state="running",
            metadata={"async": "true", "bg_id": run_id},
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        if self.claude_async_poll:
            return self._poll_async(run_id, subtask_id)
        return self._poll_sync(run_id, subtask_id=subtask_id)

    def _poll_sync(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        cached = self._statuses_by_run_id.get(run_id)
        # A missing cache is not a hard failure: the run may simply not have been
        # observed yet. Soft-fallback to "running" so the orchestrator keeps
        # polling instead of marking the subtask failed.
        if cached is None:
            return _soft_running(
                self.name,
                run_id,
                subtask_id,
                collect_artifact_refs(self._workspaces_by_run_id.get(run_id), self.artifact_globs),
                "claude worker run status is unavailable",
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

    def _poll_async(self, run_id: str, subtask_id: str) -> WorkerRunStatus:
        refs = collect_artifact_refs(self._workspaces_by_run_id.get(run_id), self.artifact_globs)
        try:
            proc = run_worker_command(
                [self.command, "agents", "--json", "--all"],
                action="claude agents",
                timeout_seconds=self.timeout_seconds,
                env=self.env,
                runner=run_command,
            )
        except Exception as exc:
            return _soft_running(self.name, run_id, subtask_id, refs, f"claude agents query failed: {exc}")
        try:
            sessions = load_json_list(proc.stdout, action="claude agents")
        except Exception as exc:
            return _soft_running(self.name, run_id, subtask_id, refs, f"claude agents returned invalid JSON: {exc}")
        session = _find_session(sessions, run_id)
        if session is None:
            return _soft_running(self.name, run_id, subtask_id, refs, f"claude session {run_id} not found")
        return _session_status(self.name, run_id, subtask_id, session, refs)

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


_BG_ID_PATTERN = re.compile(r"backgrounded.*?([0-9a-fA-F]{4,})", re.S)


def _parse_bg_id(stdout: str) -> str:
    """Extract the background session id from `claude -p --bg` output.

    The CLI prints a line like ``backgrounded · <8-hex-id>`` (middle separator is
    U+00B7) and returns immediately. We fail closed if no id can be parsed.
    """
    match = _BG_ID_PATTERN.search(stdout)
    if match is None:
        raise RuntimeError(f"claude --bg did not return a backgrounded session id: {stdout!r}")
    return match.group(1)


def _find_session(sessions: list[object], run_id: str) -> dict[str, object] | None:
    for session in sessions:
        if not isinstance(session, dict):
            continue
        candidate = session.get("id") or session.get("session_id")
        if str(candidate) == run_id:
            return session
    return None


def _session_status(
    backend: str,
    run_id: str,
    subtask_id: str,
    session: dict[str, object],
    artifact_refs: list[str],
) -> WorkerRunStatus:
    raw_state = str(session.get("state", "")).lower()
    if raw_state == "working":
        return WorkerRunStatus(
            backend=backend,
            run_id=run_id,
            subtask_id=subtask_id,
            state="running",
            output_refs=artifact_refs,
        )
    if raw_state == "done":
        # `done` cannot distinguish success from failure (a stopped session still
        # reports done), so we do not over-claim success: defer judgement to the
        # artifact + reviewer pipeline and label the detail honestly.
        return WorkerRunStatus(
            backend=backend,
            run_id=run_id,
            subtask_id=subtask_id,
            state="completed",
            detail="claude session done; success/failure indistinguishable from agent state, rely on artifact review",
            output_refs=artifact_refs,
        )
    # `blocked` or any unknown state: soft-fallback to running so the
    # orchestrator keeps polling instead of failing the subtask.
    return _soft_running(
        backend,
        run_id,
        subtask_id,
        artifact_refs,
        f"claude session state {raw_state!r} soft-fallback to running",
    )


def _soft_running(
    backend: str,
    run_id: str,
    subtask_id: str,
    artifact_refs: list[str],
    detail: str,
) -> WorkerRunStatus:
    return WorkerRunStatus(
        backend=backend,
        run_id=run_id,
        subtask_id=subtask_id,
        state="running",
        detail=detail,
        output_refs=artifact_refs,
    )
