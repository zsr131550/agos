from __future__ import annotations

import json

import agos.adapters.planner_cli as planner_cli_module
from agos.adapters.planner_cli import CliPlannerAdapter
from agos.adapters.planner_cli import _planner_prompt
from agos.core.config import AGOSConfig
from agos.core.execution_planner import create_execution_plan_with_provenance
from agos.core.task import ExecutorBinding, Task


def test_codex_planner_args_skip_git_repo_check(tmp_path):
    # The planner may run from a scratch directory that is not a trusted git
    # repository, while the planning context is delivered entirely in the
    # prompt. Codex must not refuse to start on the git-trust check.
    adapter = CliPlannerAdapter(executor="codex_cli", command="codex", cwd=tmp_path)
    args = adapter._args("plan this task")

    assert "--skip-git-repo-check" in args
    assert args.index("--skip-git-repo-check") < args.index("plan this task")


def test_planner_prompt_uses_machine_json_template():
    task = Task(
        id="planner-smoke-01",
        title="Add a greeting",
        intent="Print hello from the README.",
        workflow="feature",
        executor=ExecutorBinding(adapter="codex_cli", agent="codex"),
    )

    prompt = _planner_prompt(task, ["local_worktree"])

    assert "Return ONLY this JSON object" in prompt
    assert "byte-for-byte except whitespace" in prompt
    assert '"subtasks"' in prompt
    assert '"adapter":"local_worktree"' in prompt
    assert '"write_scope":["README.md"]' in prompt


def test_plan_json_preserves_structured_non_execution_plan_output(monkeypatch, tmp_path):
    class FakeProc:
        returncode = 0
        stdout = json.dumps({"plan": [{"step": "not an ExecutionPlan"}]})
        stderr = ""

    monkeypatch.setattr(planner_cli_module, "run_command", lambda *_args, **_kwargs: FakeProc())
    task = Task(
        id="planner-smoke-01",
        title="Add a greeting",
        intent="Print hello from the README.",
        workflow="feature",
        executor=ExecutorBinding(adapter="codex_cli", agent="codex"),
    )
    adapter = CliPlannerAdapter(executor="codex_cli", command="codex", cwd=tmp_path)

    payload = json.loads(adapter.plan_json(task, ["local_worktree"]))

    assert payload == {"plan": [{"step": "not an ExecutionPlan"}]}


def test_cli_planner_no_json_reports_fallback_provenance(monkeypatch, tmp_path):
    class FakeProc:
        returncode = 0
        stdout = "planner produced prose but no machine JSON"
        stderr = ""

    monkeypatch.setattr(planner_cli_module, "run_command", lambda *_args, **_kwargs: FakeProc())
    task = Task(
        id="planner-smoke-01",
        title="Add a greeting",
        intent="Print hello from the README.",
        workflow="feature",
        executor=ExecutorBinding(adapter="codex_cli", agent="codex"),
    )
    config = AGOSConfig.model_validate(
        {
            "executor": {"name": "codex_cli", "agent": "codex"},
            "workers": {"local_worktree": {"type": "local_worktree"}},
            "orchestration": {"planner": {"enabled": True}},
            "workflows": {"feature": {"gates": []}},
        }
    )
    adapter = CliPlannerAdapter(executor="codex_cli", command="codex", cwd=tmp_path)

    result = create_execution_plan_with_provenance(task, config, config.workers, planner=adapter)

    assert result.source == "fallback"
    assert result.plan.id == "auto-plan-planner-smoke-01"
