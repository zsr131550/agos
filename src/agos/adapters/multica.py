"""Multica executor adapter backed by the installed `multica` CLI."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Iterator

from agos.core.adapter import Event, ExecutorAdapter, ExecutorRun, RunStatus
from agos.core.task import Task

EXIT_NETWORK = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_VALIDATION = 5

RETRYABLE_EXITS = {EXIT_NETWORK, EXIT_AUTH}
STATUS_MAP = {
    "todo": "running",
    "in_progress": "running",
    "in_review": "running",
    "done": "completed",
    "blocked": "blocked",
    "cancelled": "failed",
}


def resolve_multica_bin(multica_bin: str = "multica") -> str:
    """Resolve the configured Multica CLI command to an executable path when possible."""

    return shutil.which(multica_bin) or shutil.which(f"{multica_bin}.exe") or multica_bin


class MulticaAdapter(ExecutorAdapter):
    """Dispatch tasks and poll run state via the `multica` CLI."""

    name = "multica"

    def __init__(self, multica_bin: str = "multica") -> None:
        self._multica_bin = resolve_multica_bin(multica_bin)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        full_args = [self._multica_bin, *args]
        if "--output" not in args:
            full_args.extend(["--output", "json"])

        delay = 2
        last_proc: subprocess.CompletedProcess[str] | None = None
        for attempt in range(3):
            last_proc = subprocess.run(
                full_args,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if last_proc.returncode not in RETRYABLE_EXITS:
                return last_proc
            if attempt < 2:
                time.sleep(min(delay, 30))
                delay = min(delay * 2, 30)

        assert last_proc is not None
        return last_proc

    @staticmethod
    def _load_json(stdout: str) -> dict:
        if not stdout.strip():
            return {}
        return json.loads(stdout)

    @staticmethod
    def _extract_runs(payload: dict | list) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return payload.get("runs", [])

    @staticmethod
    def _extract_messages(payload: dict | list) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return payload.get("messages", [])

    def start(self, task: Task) -> ExecutorRun:
        description_parts = [part for part in [task.intent.strip()] if part]
        if task.acceptance:
            bullet_list = "\n".join(f"- {item}" for item in task.acceptance)
            description_parts.append(f"Acceptance:\n{bullet_list}")
        description = "\n\n".join(description_parts)

        proc = self._run(
            [
                "issue",
                "create",
                "--title",
                task.title,
                "--description",
                description,
                "--assignee",
                task.executor.agent,
                "--allow-duplicate",
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"multica issue create failed with exit {proc.returncode}: {proc.stderr.strip()}"
            )

        issue = self._load_json(proc.stdout)
        issue_id = issue.get("identifier")
        if not issue_id:
            raise RuntimeError("multica issue create did not return an issue identifier")

        runs_proc = self._run(["issue", "runs", issue_id])
        if runs_proc.returncode != 0:
            raise RuntimeError(
                f"multica issue runs failed with exit {runs_proc.returncode}: {runs_proc.stderr.strip()}"
            )
        runs_payload = self._load_json(runs_proc.stdout)
        runs = self._extract_runs(runs_payload)
        if not runs or not runs[0].get("id"):
            raise RuntimeError("multica issue runs returned no task run id")

        return ExecutorRun(
            adapter=self.name,
            run_id=runs[0]["id"],
            issue_id=issue_id,
        )

    def stream_events(self, run_id: str, since: int | None = None) -> Iterator[Event]:
        args = ["issue", "run-messages", run_id]
        if since is not None:
            args.extend(["--since", str(since)])

        proc = self._run(args)
        if proc.returncode == EXIT_NOT_FOUND:
            return
        if proc.returncode != 0:
            raise RuntimeError(
                f"multica issue run-messages failed with exit {proc.returncode}: {proc.stderr.strip()}"
            )

        payload = self._load_json(proc.stdout)
        for message in self._extract_messages(payload):
            yield Event(
                seq=message["seq"],
                ts=message.get("ts", ""),
                kind=message.get("kind", "text"),
                content=message.get("content", ""),
                raw=message,
            )

    def status(self, run_id: str, issue_id: str | None = None) -> RunStatus:
        proc = self._run(["issue", "runs", issue_id or run_id])
        if proc.returncode == EXIT_NOT_FOUND:
            return RunStatus(state="failed", detail="not found")
        if proc.returncode != 0:
            raise RuntimeError(
                f"multica issue runs failed with exit {proc.returncode}: {proc.stderr.strip()}"
            )

        payload = self._load_json(proc.stdout)
        runs = self._extract_runs(payload)
        status = runs[0].get("status") if runs else None
        state = STATUS_MAP.get(status or "", "running")
        return RunStatus(state=state, detail=status)
