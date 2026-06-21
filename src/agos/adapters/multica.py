"""Multica CLI adapter."""
from __future__ import annotations

import json
import subprocess

from agos.core.adapter import ExecutorRun
from agos.core.task import Task


class MulticaAdapter:
    """Dispatch AGOS tasks through the local `multica` CLI."""

    name = "multica"

    def start(self, task: Task) -> ExecutorRun:
        description_lines = [f"AGOS workflow: {task.workflow}"]
        if task.intent:
            description_lines.append("")
            description_lines.append(task.intent)
        if task.gates:
            description_lines.append("")
            description_lines.append("Locked gates:")
            description_lines.extend(f"- {gate.id}" for gate in task.gates)
        completed = subprocess.run(
            [
                "multica",
                "issue",
                "create",
                "--title",
                task.title,
                "--description",
                "\n".join(description_lines),
                "--assignee",
                task.executor.agent,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "multica issue create failed")

        payload = json.loads(completed.stdout)
        issue = payload.get("issue", payload)
        run_id = issue.get("id")
        if not run_id:
            raise RuntimeError("multica issue create did not return an issue id")
        return ExecutorRun(
            adapter=self.name,
            run_id=run_id,
            issue_id=issue.get("identifier"),
        )

