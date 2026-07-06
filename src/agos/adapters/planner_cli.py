"""CLI-backed execution planner adapter."""
from __future__ import annotations

import json
from pathlib import Path

from agos.adapters.workers.transport import run_worker_command
from agos.core.command import run_command
from agos.core.json_text import load_json_object_from_text
from agos.core.task import Task


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
        payload = load_json_object_from_text(proc.stdout)
        if isinstance(payload, dict) and _looks_like_execution_plan(payload):
            return json.dumps(payload)
        return json.dumps(
            _fallback_plan_payload(task, available_workers),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _args(self, prompt: str) -> list[str]:
        command = self.command or _default_command(self.executor)
        if self.executor == "codex_cli":
            return [command, "exec", "--skip-git-repo-check", "--json", prompt]
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
    example = _fallback_plan_payload(task, available_workers)
    return (
        "Machine output task. No tools. No questions. No markdown. "
        "Return only one JSON object matching the required shape.\n\n"
        f"Task id: {task.id}\n"
        f"Task title: {task.title}\n"
        f"Task intent: {task.intent or '<none>'}\n"
        f"Acceptance:\n{acceptance}\n\n"
        f"Available workers:\n{worker_lines}\n\n"
        "Required fields: id, task_id, max_parallel, requires_candidate_review, subtasks. "
        "The subtasks array must contain at least one subtask. "
        "Each subtask must include id, title, intent, depends_on, write_scope, worker.adapter. "
        "Use the first available worker when no better worker is specified. "
        "Use this exact JSON shape, adjusted only if needed:\n"
        f"{json.dumps(example, ensure_ascii=False, separators=(',', ':'))}"
    )


def _fallback_plan_payload(task: Task, available_workers: list[str]) -> dict[str, object]:
    worker = available_workers[0] if available_workers else "local_worktree"
    task_text = f"{task.title}\n{task.intent or ''}".lower()
    write_scope = ["README.md"] if "readme" in task_text else ["README.md", "src/agos", "tests", "docs"]
    return {
        "id": f"auto-plan-{task.id}",
        "task_id": task.id,
        "max_parallel": 1,
        "requires_candidate_review": True,
        "subtasks": [
            {
                "id": f"auto-subtask-{task.id}",
                "title": task.title,
                "intent": task.intent or task.title,
                "depends_on": [],
                "write_scope": write_scope,
                "worker": {"adapter": worker, "role": "worker_agent"},
            }
        ],
    }


def _looks_like_execution_plan(payload: dict[str, object]) -> bool:
    return all(
        key in payload
        for key in ("id", "task_id", "max_parallel", "requires_candidate_review", "subtasks")
    ) and isinstance(payload.get("subtasks"), list)


def parse_planner_json(text: str) -> dict[str, object] | None:
    return load_json_object_from_text(text)
