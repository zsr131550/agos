"""Multica executor adapter backed by the installed `multica` CLI."""
from __future__ import annotations

import json
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


class MulticaAdapter(ExecutorAdapter):
    """Dispatch tasks and poll run state via the `multica` CLI."""

    name = "multica"

    def __init__(self, multica_bin: str = "multica") -> None:
        self._multica_bin = multica_bin

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
            ]
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"multica issue create failed with exit {proc.returncode}: {proc.stderr.strip()}"
            )

        issue = self._load_json(proc.stdout)
        return ExecutorRun(
            adapter=self.name,
            run_id=issue["id"],
            issue_id=issue.get("identifier"),
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
        for message in payload.get("messages", []):
            yield Event(
                seq=message["seq"],
                ts=message.get("ts", ""),
                kind=message.get("kind", "text"),
                content=message.get("content", ""),
                raw=message,
            )

    def status(self, run_id: str) -> RunStatus:
        proc = self._run(["issue", "runs", run_id])
        if proc.returncode == EXIT_NOT_FOUND:
            return RunStatus(state="failed", detail="not found")
        if proc.returncode != 0:
            raise RuntimeError(
                f"multica issue runs failed with exit {proc.returncode}: {proc.stderr.strip()}"
            )

        payload = self._load_json(proc.stdout)
        runs = payload.get("runs", [])
        status = runs[0].get("status") if runs else None
        state = STATUS_MAP.get(status or "", "running")
        return RunStatus(state=state, detail=status)
