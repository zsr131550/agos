from __future__ import annotations

import os

import pytest

from agos.adapters.planner_cli import CliPlannerAdapter
from agos.core.json_text import load_json_object_from_text
from agos.core.task import ExecutorBinding, Task


def _smoke_task() -> Task:
    return Task(
        id="planner-smoke-01",
        title="Add a greeting",
        intent="Print hello from the README.",
        workflow="feature",
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )


@pytest.mark.skipif(os.getenv("AGOS_PLANNER_SMOKE") != "1", reason="opt-in real planner CLI smoke")
def test_planner_cli_produces_plan_json(tmp_path):
    executor = os.getenv("AGOS_PLANNER_EXECUTOR", "codex_cli")
    adapter = CliPlannerAdapter(
        executor=executor,
        command=os.getenv("AGOS_PLANNER_BIN"),
        cwd=tmp_path,
        timeout_seconds=120,
    )

    stdout = adapter.plan_json(_smoke_task(), ["local_worktree"])

    payload = load_json_object_from_text(stdout)
    assert payload is not None
    assert "subtasks" in payload
