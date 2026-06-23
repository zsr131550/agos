"""Multica CLI execution worker adapter."""
from __future__ import annotations

import os

from agos.adapters.workers.artifacts import collect_artifact_refs, merge_output_refs
from agos.adapters.workers.transport import (
    load_json_object,
    load_json_object_or_list,
    output_refs_from_payload,
    process_detail,
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


STATUS_MAP = {
    "todo": "queued",
    "pending": "queued",
    "in_progress": "running",
    "running": "running",
    "in_review": "running",
    "done": "completed",
    "completed": "completed",
    "blocked": "blocked",
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
}


class MulticaWorkerAdapter:
    """Dispatch AGOS subtasks through Multica's issue/run CLI workflow."""

    def __init__(
        self,
        *,
        multica_bin: str = "multica",
        agent: str | None = None,
        name: str = "multica",
        timeout_seconds: int = 30,
        poll_interval_seconds: int = 1,
        artifact_globs: tuple[str, ...] | list[str] = (),
        env: dict[str, str] | None = None,
    ) -> None:
        self.multica_bin = multica_bin
        self.agent = agent or "Lambda"
        self.name = name
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.artifact_globs = tuple(artifact_globs)
        self.env = dict(env or {})
        self._issue_by_run_id: dict[str, str] = {}
        self._subtask_by_run_id: dict[str, str] = {}
        self._workspaces_by_run_id: dict[str, str] = {}

    def health(self) -> WorkerHealth:
        return WorkerHealth(
            name=self.name,
            adapter="multica",
            checks=[
                self._health_command("daemon_status", [self.multica_bin, "daemon", "status"]),
                self._workspace_list_health(),
            ],
            metadata={
                "command": self.multica_bin,
                "agent": self.agent,
                "timeout_seconds": str(self.timeout_seconds),
                "poll_interval_seconds": str(self.poll_interval_seconds),
                "artifact_globs": ",".join(self.artifact_globs),
                "env_keys": ",".join(sorted(self.env)),
            },
        )

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        issue_proc = run_worker_command(
            [
                self.multica_bin,
                "issue",
                "create",
                "--title",
                request.subtask_id,
                "--description",
                request.prompt,
                "--assignee",
                self.agent,
                "--allow-duplicate",
                "--output",
                "json",
            ],
            action="multica issue create",
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            runner=run_command,
        )
        issue = load_json_object(issue_proc.stdout, action="multica issue create")
        issue_id = str(issue.get("identifier") or issue.get("id") or "")
        if not issue_id:
            raise RuntimeError("multica issue create did not return an issue identifier")

        runs = self._issue_runs(issue_id)
        if not runs:
            raise RuntimeError("multica issue runs returned no task run id")
        first = runs[0]
        run_id = str(first.get("id") or request.run_id)
        self._issue_by_run_id[run_id] = issue_id
        self._subtask_by_run_id[run_id] = request.subtask_id
        self._workspaces_by_run_id[run_id] = request.workspace_path
        return WorkerRun(
            backend=self.name,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state=_state(first.get("status"), default="running"),
            metadata={"issue_id": issue_id},
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        issue_id = self._issue_by_run_id.get(run_id, run_id)
        runs = self._issue_runs(issue_id)
        current = _matching_run(runs, run_id)
        self._subtask_by_run_id[run_id] = subtask_id
        local_refs = collect_artifact_refs(
            self._workspaces_by_run_id.get(run_id),
            self.artifact_globs,
        )
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state=_state(current.get("status") if current else None, default="running"),
            detail=str(current.get("status")) if current and current.get("status") is not None else None,
            output_refs=merge_output_refs(output_refs_from_payload(current), local_refs),
        )

    def cancel(self, run_id: str) -> WorkerRunStatus:
        proc = run_worker_command(
            [self.multica_bin, "issue", "cancel", run_id, "--output", "json"],
            action="multica issue cancel",
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            runner=run_command,
        )
        payload = load_json_object(proc.stdout, action="multica issue cancel")
        if "state" not in payload:
            payload["state"] = "cancelled"
        subtask_id = self._subtask_by_run_id.get(run_id, str(payload.get("subtask_id", "unknown")))
        local_refs = collect_artifact_refs(
            self._workspaces_by_run_id.get(run_id),
            self.artifact_globs,
        )
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state=_state(payload.get("state"), default="cancelled"),
            detail=str(payload["detail"]) if payload.get("detail") is not None else None,
            output_refs=merge_output_refs(output_refs_from_payload(payload), local_refs),
        )

    def _health_command(self, name: str, args: list[str]) -> WorkerHealthCheck:
        try:
            proc = run_command(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_seconds,
                env={**os.environ, **self.env},
            )
        except Exception as exc:
            return WorkerHealthCheck(name=name, state="failed", detail=str(exc))
        if proc.returncode != 0:
            return WorkerHealthCheck(name=name, state="failed", detail=process_detail(proc))
        return WorkerHealthCheck(name=name, state="passed", detail=process_detail(proc) or "ok")

    def _workspace_list_health(self) -> WorkerHealthCheck:
        try:
            proc = run_command(
                [self.multica_bin, "workspace", "list", "--output", "json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout_seconds,
                env={**os.environ, **self.env},
            )
        except Exception as exc:
            return WorkerHealthCheck(name="workspace_list", state="failed", detail=str(exc))
        if proc.returncode != 0:
            return WorkerHealthCheck(name="workspace_list", state="failed", detail=process_detail(proc))
        try:
            load_json_object_or_list(proc.stdout or "", action="multica workspace list")
        except Exception as exc:
            return WorkerHealthCheck(name="workspace_list", state="failed", detail=str(exc))
        return WorkerHealthCheck(name="workspace_list", state="passed", detail=process_detail(proc) or "ok")

    def _issue_runs(self, issue_id: str) -> list[dict[str, object]]:
        proc = run_worker_command(
            [self.multica_bin, "issue", "runs", issue_id, "--output", "json"],
            action="multica issue runs",
            timeout_seconds=self.timeout_seconds,
            env=self.env,
            runner=run_command,
        )
        payload = load_json_object_or_list(proc.stdout, action="multica issue runs")
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        runs = payload.get("runs", [])
        if not isinstance(runs, list):
            return []
        return [item for item in runs if isinstance(item, dict)]


def _state(value: object, *, default: str) -> str:
    return STATUS_MAP.get(str(value or default), default)


def _matching_run(runs: list[dict[str, object]], run_id: str) -> dict[str, object] | None:
    for run in runs:
        if str(run.get("id")) == run_id:
            return run
    return runs[0] if runs else None

