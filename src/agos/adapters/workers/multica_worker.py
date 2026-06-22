"""Multica CLI execution worker adapter."""
from __future__ import annotations

import json

from agos.core.command import run_command
from agos.core.execution_worker import WorkerRun, WorkerRunStatus, WorkerStartRequest


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
    ) -> None:
        self.multica_bin = multica_bin
        self.agent = agent or "Lambda"
        self.name = name
        self._issue_by_run_id: dict[str, str] = {}
        self._subtask_by_run_id: dict[str, str] = {}

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        issue_proc = run_command(
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
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        _raise_on_failure(issue_proc, "multica issue create")
        issue = _load_json(issue_proc.stdout)
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
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state=_state(current.get("status") if current else None, default="running"),
            detail=str(current.get("status")) if current and current.get("status") is not None else None,
        )

    def cancel(self, run_id: str) -> WorkerRunStatus:
        proc = run_command(
            [self.multica_bin, "issue", "cancel", run_id, "--output", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if proc.returncode != 0:
            return WorkerRunStatus(
                backend=self.name,
                run_id=run_id,
                subtask_id=self._subtask_by_run_id.get(run_id, "unknown"),
                state="failed",
                detail=proc.stderr.strip(),
            )
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=self._subtask_by_run_id.get(run_id, "unknown"),
            state="cancelled",
        )

    def _issue_runs(self, issue_id: str) -> list[dict[str, object]]:
        proc = run_command(
            [self.multica_bin, "issue", "runs", issue_id, "--output", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        _raise_on_failure(proc, "multica issue runs")
        payload = _load_json_or_list(proc.stdout)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        runs = payload.get("runs", [])
        if not isinstance(runs, list):
            return []
        return [item for item in runs if isinstance(item, dict)]


def _load_json(stdout: str) -> dict[str, object]:
    payload = _load_json_or_list(stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("multica CLI returned non-object JSON")
    return payload


def _load_json_or_list(stdout: str) -> dict[str, object] | list[object]:
    if not stdout.strip():
        return {}
    payload = json.loads(stdout)
    if isinstance(payload, dict | list):
        return payload
    raise RuntimeError("multica CLI returned unsupported JSON")


def _raise_on_failure(proc, action: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(f"{action} failed with exit {proc.returncode}: {proc.stderr.strip()}")


def _state(value: object, *, default: str) -> str:
    return STATUS_MAP.get(str(value or default), default)


def _matching_run(runs: list[dict[str, object]], run_id: str) -> dict[str, object] | None:
    for run in runs:
        if str(run.get("id")) == run_id:
            return run
    return runs[0] if runs else None
