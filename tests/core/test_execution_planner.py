from __future__ import annotations

import json

import pytest

from agos.core.config import AGOSConfig
from agos.core.execution_planner import create_execution_plan
from agos.core.task import ExecutorBinding, Task


def _task(task_id: str = "agos-01") -> Task:
    return Task(
        id=task_id,
        title="Update README",
        intent="Change the README heading.",
        workflow="feature",
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )


def _config(
    *,
    workers: dict[str, dict[str, object]] | None = None,
    max_parallel: int = 3,
    planner_enabled: bool = False,
) -> AGOSConfig:
    orchestration: dict[str, object] = {"max_parallel": max_parallel}
    if planner_enabled:
        orchestration["planner"] = {"enabled": True}
    return AGOSConfig.model_validate(
        {
            "executor": {"name": "multica", "agent": "Lambda"},
            "workers": workers if workers is not None else {"alpha": {"type": "local_worktree"}},
            "orchestration": orchestration,
            "workflows": {"feature": {"gates": []}},
        }
    )


def test_fallback_plan_uses_active_task_first_worker_and_configured_parallelism() -> None:
    config = _config(workers={"alpha": {"type": "local_worktree"}, "beta": {"type": "local_worktree"}}, max_parallel=4)

    plan = create_execution_plan(_task(), config, config.workers)

    assert plan.id == "auto-plan-agos-01"
    assert plan.task_id == "agos-01"
    assert plan.max_parallel == 4
    assert [subtask.worker.adapter for subtask in plan.subtasks] == ["alpha"]
    assert plan.subtasks[0].write_scope == ["README.md", "src/agos", "tests", "docs"]


def test_fallback_plan_uses_local_worktree_when_no_workers_are_configured() -> None:
    config = _config(workers={})

    plan = create_execution_plan(_task(), config, config.workers)

    assert plan.subtasks[0].worker.adapter == "local_worktree"


def test_valid_planner_json_is_parsed_and_task_id_is_normalized() -> None:
    config = _config()
    planner_json = json.dumps(
        {
            "id": "planner-plan",
            "task_id": "wrong-task",
            "max_parallel": 2,
            "requires_candidate_review": True,
            "subtasks": [
                {
                    "id": "subtask-docs",
                    "title": "Update docs",
                    "write_scope": ["README.md"],
                    "worker": {"adapter": "alpha", "role": "worker_agent"},
                }
            ],
        }
    )

    plan = create_execution_plan(_task("active-task"), config, config.workers, planner_json=planner_json)

    assert plan.id == "planner-plan"
    assert plan.task_id == "active-task"
    assert plan.subtasks[0].worker.adapter == "alpha"


def test_invalid_json_falls_back_to_deterministic_plan() -> None:
    config = _config(max_parallel=2)

    plan = create_execution_plan(_task(), config, config.workers, planner_json="{not-json")

    assert plan.id == "auto-plan-agos-01"
    assert plan.max_parallel == 2


def test_planner_json_with_unknown_worker_is_rejected() -> None:
    config = _config()
    planner_json = json.dumps(
        {
            "id": "bad-plan",
            "task_id": "agos-01",
            "subtasks": [
                {
                    "id": "subtask-docs",
                    "title": "Update docs",
                    "write_scope": ["README.md"],
                    "worker": {"adapter": "ghost"},
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="unknown worker"):
        create_execution_plan(_task(), config, config.workers, planner_json=planner_json)


def test_impossible_planner_json_is_rejected() -> None:
    config = _config()
    planner_json = json.dumps(
        {
            "id": "bad-plan",
            "task_id": "agos-01",
            "subtasks": [
                {
                    "id": "subtask-a",
                    "title": "A",
                    "depends_on": ["missing"],
                    "write_scope": ["README.md"],
                    "worker": {"adapter": "alpha"},
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="unknown dependency"):
        create_execution_plan(_task(), config, config.workers, planner_json=planner_json)


def test_fallback_plan_uses_configured_fallback_write_scope() -> None:
    config = AGOSConfig.model_validate(
        {
            "executor": {"name": "multica", "agent": "Lambda"},
            "workers": {"alpha": {"type": "local_worktree"}},
            "orchestration": {
                "max_parallel": 3,
                "fallback_write_scope": ["custom/path", "other"],
            },
            "workflows": {"feature": {"gates": []}},
        }
    )

    plan = create_execution_plan(_task(), config, config.workers)

    assert plan.subtasks[0].write_scope == ["custom/path", "other"]


class _FakePlanner:
    """Minimal PlannerAdapter for exercising the LLM planner path."""

    def __init__(self, json_text: str | None = None, *, raises: BaseException | None = None) -> None:
        self._json_text = json_text
        self._raises = raises
        self.calls = 0

    def plan_json(self, task, available_workers) -> str:  # noqa: ANN001 - Protocol shape
        del task, available_workers
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._json_text or ""


def test_llm_planner_valid_json_used() -> None:
    config = _config(planner_enabled=True)
    planner = _FakePlanner(
        json.dumps(
            {
                "id": "llm-plan",
                "task_id": "agos-01",
                "max_parallel": 2,
                "requires_candidate_review": True,
                "subtasks": [
                    {
                        "id": "llm-subtask",
                        "title": "Update docs",
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "alpha", "role": "worker_agent"},
                    }
                ],
            }
        )
    )

    plan = create_execution_plan(_task(), config, config.workers, planner=planner)

    assert plan.id == "llm-plan"
    assert plan.subtasks[0].id == "llm-subtask"
    assert planner.calls == 1


def test_llm_planner_cli_failure_falls_back() -> None:
    config = _config(planner_enabled=True)
    planner = _FakePlanner(raises=OSError("codex not installed"))

    plan = create_execution_plan(_task(), config, config.workers, planner=planner)

    assert plan.id == "auto-plan-agos-01"
    assert planner.calls == 1


def test_llm_planner_invalid_structure_raises() -> None:
    config = _config(planner_enabled=True)
    planner = _FakePlanner(
        json.dumps(
            {
                "id": "llm-plan",
                "task_id": "agos-01",
                "max_parallel": 1,
                "requires_candidate_review": True,
                "subtasks": [],
            }
        )
    )

    with pytest.raises(ValueError, match="at least one subtask"):
        create_execution_plan(_task(), config, config.workers, planner=planner)


def test_llm_planner_disabled_uses_fallback() -> None:
    config = _config(planner_enabled=False)
    planner = _FakePlanner(json.dumps({"id": "llm-plan", "subtasks": []}))

    plan = create_execution_plan(_task(), config, config.workers, planner=planner)

    assert plan.id == "auto-plan-agos-01"
    assert planner.calls == 0
