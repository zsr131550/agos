"""Synchronous local CLI executors for Codex and Claude Code."""
from __future__ import annotations

import json
import hashlib
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from agos.adapters.agent_permissions import claude_permission_args, codex_permission_args
from agos.core.adapter import Event, ExecutorRun, RunStatus
from agos.core.command import run_command, run_command as _run_git_command
from agos.core.execution import utc_now_iso
from agos.core.task import Task, load_task, task_output_ref
from agos.core.task_execution import task_requires_output_directory


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
        dangerously_bypass_permissions: bool = False,
    ) -> None:
        self.name = name
        self.command = command
        self.evidence_dir = evidence_dir
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self.dangerously_bypass_permissions = dangerously_bypass_permissions

    def start(self, task: Task) -> ExecutorRun:
        run_id = f"{self.name}-{uuid4().hex[:12]}"
        output_ref = task_output_ref(task)
        output_dir = self.cwd / output_ref
        requires_output_directory = task_requires_output_directory(task)
        baseline = (
            None if requires_output_directory else _repository_change_fingerprint(self.cwd)
        )
        if requires_output_directory:
            output_dir.mkdir(parents=True, exist_ok=True)
        prompt = _task_prompt(task)
        args = self._start_args(prompt)
        proc = self._run_args(args)
        if proc.returncode == 0 and not _task_contract_satisfied(
            task,
            repo_root=self.cwd,
            output_dir=output_dir,
            baseline=baseline,
        ):
            retry_prompt = _retry_prompt(task, _detail(proc))
            retry_args = self._start_args(retry_prompt)
            retry_proc = self._run_args(retry_args)
            if retry_proc.returncode != 0 or _task_contract_satisfied(
                task,
                repo_root=self.cwd,
                output_dir=output_dir,
                baseline=baseline,
            ):
                proc = retry_proc

        state = "completed" if proc.returncode == 0 else "failed"
        if state == "completed" and not _task_contract_satisfied(
            task,
            repo_root=self.cwd,
            output_dir=output_dir,
            baseline=baseline,
        ):
            original_output = _detail(proc)
            message = _missing_output_message(task)
            if original_output:
                message = f"{message}\n\nAgent output:\n{original_output}"
            proc = subprocess.CompletedProcess(
                getattr(proc, "args", args),
                1,
                stdout="",
                stderr=message,
            )
            state = "failed"
        events = _events_from_process(proc, state=state)
        self._write_run_state(run_id, state=state, detail=_detail(proc), events=events)
        return ExecutorRun(adapter=self.name, run_id=run_id, issue_id=None)

    def _run_args(self, args: list[str]) -> subprocess.CompletedProcess:
        try:
            return run_command(
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
            return subprocess.CompletedProcess(
                args,
                124,
                stdout="",
                stderr=f"timed out after {exc.timeout or self.timeout_seconds:g} seconds",
            )
        except OSError as exc:
            return subprocess.CompletedProcess(args, 127, stdout="", stderr=str(exc))

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
        if state == "completed":
            empty_output_detail = self._empty_output_detail()
            if empty_output_detail is not None:
                return RunStatus(state="failed", detail=empty_output_detail)
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

    def _empty_output_detail(self) -> str | None:
        task_path = self.evidence_dir.parent / "task.yaml"
        if not task_path.is_file():
            return None
        try:
            task = load_task(task_path)
        except Exception:
            return None
        if not task_requires_output_directory(task):
            return None
        output_ref = task_output_ref(task)
        if not _output_dir_is_empty(self.cwd / output_ref):
            return None
        return (
            f"Executor completed without writing files to {output_ref}. "
            "This usually means the agent stopped for clarification or approval instead of implementing."
        )


class CodexCliExecutorAdapter(LocalCliExecutorAdapter):
    def __init__(
        self,
        *,
        command: str = "codex",
        evidence_dir: Path,
        cwd: Path,
        dangerously_bypass_permissions: bool = False,
    ) -> None:
        super().__init__(
            name="codex_cli",
            command=command,
            evidence_dir=evidence_dir,
            cwd=cwd,
            dangerously_bypass_permissions=dangerously_bypass_permissions,
        )

    def _start_args(self, prompt: str) -> list[str]:
        return [
            self.command,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            *codex_permission_args(
                dangerously_bypass_permissions=self.dangerously_bypass_permissions
            ),
            "--json",
            prompt,
        ]


class ClaudeCodeExecutorAdapter(LocalCliExecutorAdapter):
    def __init__(
        self,
        *,
        command: str = "claude",
        evidence_dir: Path,
        cwd: Path,
        dangerously_bypass_permissions: bool = False,
    ) -> None:
        super().__init__(
            name="claude_code",
            command=command,
            evidence_dir=evidence_dir,
            cwd=cwd,
            dangerously_bypass_permissions=dangerously_bypass_permissions,
        )

    def _start_args(self, prompt: str) -> list[str]:
        return [
            self.command,
            *claude_permission_args(
                dangerously_bypass_permissions=self.dangerously_bypass_permissions
            ),
            "-p",
            "--output-format",
            "json",
            prompt,
        ]


def _task_prompt(task: Task) -> str:
    output_ref = task_output_ref(task)
    contract = [
        "AGOS execution contract:",
        "- You are running as an AGOS background executor/subagent, not as an interactive assistant.",
        "- This AGOS execution contract overrides any local skill, project rule, plugin workflow, or prompt that would require asking the user before implementation.",
        "- Run non-interactively. Do not ask clarifying questions; make reasonable assumptions and implement the task.",
        "- Do not wait for user approval, browser-companion approval, design approval, or additional confirmation.",
        "- Do not invoke brainstorming or design-approval gates; treat this prompt as the approved implementation request.",
        "- Implement immediately and write concrete artifacts before returning.",
    ]
    if task_requires_output_directory(task):
        contract.extend(
            [
                f"- Use `{output_ref}` as the default output directory for standalone deliverables.",
                "- If the task is a game, demo, or website, create a runnable entrypoint such as `index.html` in that output directory.",
                "- Report the output directory and key files in the final response.",
            ]
        )
    else:
        contract.extend(
            [
                "- Change governed repository files directly; do not stop after describing a solution.",
                "- A standalone outputs directory is not required for this source-code task.",
                "- Finish only after producing a concrete, valid repository change.",
            ]
        )
    parts = [f"Task: {task.title}", "\n".join(contract)]
    if task.intent.strip():
        parts.append(task.intent.strip())
    if task.acceptance:
        parts.append("Acceptance:\n" + "\n".join(f"- {item}" for item in task.acceptance))
    return "\n\n".join(parts)


def _retry_prompt(task: Task, previous_output: str) -> str:
    output_ref = task_output_ref(task)
    if not task_requires_output_directory(task):
        directive = [
            "AGOS retry directive:",
            "- Your previous response stopped without changing governed source files.",
            "- Do not ask questions or request approval.",
            "- Make reasonable assumptions and implement the requested source change now.",
            "- Before returning, make at least one concrete, valid repository change.",
        ]
    else:
        directive = [
            "AGOS retry directive:",
            f"- Your previous response stopped without writing output to `{output_ref}`.",
            "- Do not ask questions or request approval.",
            "- Ignore any skill, plugin, browser-companion, brainstorming, or design process that would require user input.",
            "- Make reasonable assumptions and implement now.",
            f"- Before returning, write at least one concrete file under `{output_ref}`.",
        ]
    parts = [
        _task_prompt(task),
        "\n".join(directive),
    ]
    if previous_output:
        parts.append(f"Previous executor output:\n{previous_output}")
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


def _output_dir_is_empty(output_dir: Path) -> bool:
    try:
        return not any(output_dir.iterdir())
    except FileNotFoundError:
        return True


def _task_contract_satisfied(
    task: Task,
    *,
    repo_root: Path,
    output_dir: Path,
    baseline: str | None,
) -> bool:
    if task_requires_output_directory(task):
        return not _output_dir_is_empty(output_dir)
    if baseline is None or _repository_change_fingerprint(repo_root) == baseline:
        return False
    return _repository_diff_is_valid(repo_root)


def _missing_output_message(task: Task) -> str:
    if not task_requires_output_directory(task):
        return (
            "Executor completed without changing governed source files. "
            "This usually means the agent stopped for clarification or approval instead of implementing."
        )
    output_ref = task_output_ref(task)
    return (
        f"Executor completed without writing files to {output_ref}. "
        "This usually means the agent stopped for clarification or approval instead of implementing."
    )


def _repository_change_fingerprint(repo_root: Path) -> str:
    pathspec = ["--", ".", ":(exclude).agos", ":(exclude)outputs"]
    diff = _run_git_command(
        ["git", "diff", "--binary", "--no-ext-diff", "HEAD", *pathspec],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    untracked = _run_git_command(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            *pathspec,
        ],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    digest = hashlib.sha256()
    digest.update(_as_bytes(diff.stdout))
    for raw_path in _as_bytes(untracked.stdout).split(b"\0"):
        if not raw_path:
            continue
        digest.update(raw_path)
        path = repo_root / raw_path.decode("utf-8", errors="surrogateescape")
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _repository_diff_is_valid(repo_root: Path) -> bool:
    checked = _run_git_command(
        [
            "git",
            "diff",
            "--check",
            "HEAD",
            "--",
            ".",
            ":(exclude).agos",
            ":(exclude)outputs",
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    return checked.returncode == 0


def _as_bytes(value: str | bytes | None) -> bytes:
    if value is None:
        return b""
    return value.encode("utf-8") if isinstance(value, str) else value
