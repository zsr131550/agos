"""CLI-backed execution planner adapter."""
from __future__ import annotations

from pathlib import Path

from agos.core.command import run_command
from agos.core.json_text import load_json_object_from_text
from agos.core.task import Task
from agos.adapters.workers.transport import run_worker_command


class CliPlannerAdapter:
    """Run `codex exec --json` or `claude -p --output-format json` as the planner."""

    def __init__(
        self,
        *,
        executor: str = "codex_cli",
        command: str | None = None,
        cwd: Path,
        timeout_seconds: int = 60,
    ) -> None:
        self.executor = executor
        self.command = command
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds

    def plan_json(self, task: Task, available_workers: list[str]) -> str:
        prompt = _planner_prompt(task, available_workers)
        proc = run_worker_command(
            self._args(prompt),
            action=f"{self.executor} planner",
            cwd=self.cwd,
            timeout_seconds=self.timeout_seconds,
            runner=run_command,
        )
        return proc.stdout

    def _args(self, prompt: str) -> list[str]:
        command = self.command or _default_command(self.executor)
        if self.executor == "codex_cli":
            return [command, "exec", "--json", prompt]
        if self.executor == "claude_code":
            return [command, "-p", "--output-format", "json", prompt]
        raise ValueError(f"unsupported planner executor: {self.executor}")


def _default_command(executor: str) -> str:
    if executor == "codex_cli":
        return "codex"
    if executor == "claude_code":
        return "claude"
    raise ValueError(f"unsupported planner executor: {executor}")


def _planner_prompt(task: Task, available_workers: list[str]) -> str:
    worker_lines = "\n".join(f"- {worker}" for worker in available_workers) or "- <none>"
    acceptance = (
        "\n".join(f"- {item}" for item in task.acceptance) if task.acceptance else "- <none>"
    )
    return (
        "You are the AGOS planner. Return JSON only.\n\n"
        f"Task id: {task.id}\n"
        f"Task title: {task.title}\n"
        f"Task intent: {task.intent or '<none>'}\n"
        f"Acceptance:\n{acceptance}\n\n"
        f"Available workers:\n{worker_lines}\n\n"
        "Return a single JSON object matching ExecutionPlan with fields "
        'id, task_id, max_parallel, requires_candidate_review, subtasks. '
        "Each subtask must include id, title, intent, depends_on, write_scope, worker.adapter."
    )


def parse_planner_json(text: str) -> dict[str, object] | None:
    return load_json_object_from_text(text)
