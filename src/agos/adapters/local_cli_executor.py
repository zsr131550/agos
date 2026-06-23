"""Synchronous local CLI executors for Codex and Claude Code."""
from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from agos.core.adapter import Event, ExecutorRun, RunStatus
from agos.core.command import run_command
from agos.core.execution import utc_now_iso
from agos.core.task import Task


class LocalCliExecutorAdapter:
    """Run a local agent CLI synchronously and persist a checkpoint-readable transcript."""

    def __init__(
        self,
        *,
        name: str,
        command: str,
        evidence_dir: Path,
        cwd: Path,
        timeout_seconds: int = 900,
    ) -> None:
        self.name = name
        self.command = command
        self.evidence_dir = evidence_dir
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds

    def start(self, task: Task) -> ExecutorRun:
        run_id = f"{self.name}-{uuid4().hex[:12]}"
        prompt = _task_prompt(task)
        args = self._start_args(prompt)
        try:
            proc = run_command(
                args,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=self.timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            proc = subprocess.CompletedProcess(
                args,
                124,
                stdout="",
                stderr=f"timed out after {exc.timeout or self.timeout_seconds:g} seconds",
            )
        except OSError as exc:
            proc = subprocess.CompletedProcess(args, 127, stdout="", stderr=str(exc))

        state = "completed" if proc.returncode == 0 else "failed"
        events = _events_from_process(proc, state=state)
        self._write_run_state(run_id, state=state, detail=_detail(proc), events=events)
        return ExecutorRun(adapter=self.name, run_id=run_id, issue_id=None)

    def stream_events(self, run_id: str, since: int | None = None) -> Iterator[Event]:
        for payload in self._read_run_state(run_id).get("events", []):
            if not isinstance(payload, dict):
                continue
            seq = int(payload.get("seq", 0))
            if since is not None and seq <= since:
                continue
            yield Event(
                seq=seq,
                ts=str(payload.get("ts", "")),
                kind=str(payload.get("kind", "text")),
                content=str(payload.get("content", "")),
                raw=payload,
            )

    def status(self, run_id: str, issue_id: str | None = None) -> RunStatus:
        del issue_id
        try:
            payload = self._read_run_state(run_id)
        except FileNotFoundError:
            return RunStatus(state="failed", detail="not found")
        state = str(payload.get("state", "failed"))
        if state not in {"running", "completed", "failed", "blocked"}:
            state = "failed"
        detail = payload.get("detail")
        return RunStatus(state=state, detail=str(detail) if detail is not None else None)

    def _start_args(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def _state_path(self, run_id: str) -> Path:
        return self.evidence_dir / "executor_runs" / f"{run_id}.json"

    def _write_run_state(
        self,
        run_id: str,
        *,
        state: str,
        detail: str,
        events: list[dict[str, object]],
    ) -> None:
        path = self._state_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "adapter": self.name,
                    "state": state,
                    "detail": detail,
                    "events": events,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    def _read_run_state(self, run_id: str) -> dict[str, object]:
        return json.loads(self._state_path(run_id).read_text(encoding="utf-8"))


class CodexCliExecutorAdapter(LocalCliExecutorAdapter):
    def __init__(self, *, command: str = "codex", evidence_dir: Path, cwd: Path) -> None:
        super().__init__(name="codex_cli", command=command, evidence_dir=evidence_dir, cwd=cwd)

    def _start_args(self, prompt: str) -> list[str]:
        return [self.command, "exec", "--json", prompt]


class ClaudeCodeExecutorAdapter(LocalCliExecutorAdapter):
    def __init__(self, *, command: str = "claude", evidence_dir: Path, cwd: Path) -> None:
        super().__init__(name="claude_code", command=command, evidence_dir=evidence_dir, cwd=cwd)

    def _start_args(self, prompt: str) -> list[str]:
        return [self.command, "-p", "--output-format", "json", prompt]


def _task_prompt(task: Task) -> str:
    parts = [f"Task: {task.title}"]
    if task.intent.strip():
        parts.append(task.intent.strip())
    if task.acceptance:
        parts.append("Acceptance:\n" + "\n".join(f"- {item}" for item in task.acceptance))
    return "\n\n".join(parts)


def _events_from_process(proc: subprocess.CompletedProcess, *, state: str) -> list[dict[str, object]]:
    output = _detail(proc)
    kind = "text" if proc.returncode == 0 else "error"
    return [
        {
            "seq": 1,
            "ts": utc_now_iso(),
            "kind": kind,
            "content": output,
            "returncode": proc.returncode,
        },
        {
            "seq": 2,
            "ts": utc_now_iso(),
            "kind": "run_complete" if state == "completed" else "error",
            "content": state,
            "returncode": proc.returncode,
        },
    ]


def _detail(proc: subprocess.CompletedProcess) -> str:
    stdout = getattr(proc, "stdout", "") or ""
    stderr = getattr(proc, "stderr", "") or ""
    return str(stdout or stderr or f"exit {proc.returncode}").strip()
