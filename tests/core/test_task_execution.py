from __future__ import annotations

from pathlib import Path

from agos.core.config import AGOSConfig
from agos.core.task import ExecutorBinding, Task, load_task
from agos.core.task_execution import (
    TaskExecutionResult,
    effective_task_mode,
    task_requires_output_directory,
)


def _task(**updates) -> Task:
    task = Task(
        id="agos-01",
        title="Execution mode task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    return task.model_copy(update=updates)


def test_old_config_defaults_to_legacy_execution() -> None:
    config = AGOSConfig.model_validate({"executor": {"agent": "Lambda"}})

    assert config.task_execution.mode == "legacy"
    assert config.task_execution.output_contract == "legacy"


def test_explicit_candidate_source_code_config_loads() -> None:
    config = AGOSConfig.model_validate(
        {
            "executor": {"agent": "Lambda"},
            "task_execution": {
                "mode": "candidate",
                "output_contract": "source_code",
            },
        }
    )

    assert config.task_execution.mode == "candidate"
    assert config.task_execution.output_contract == "source_code"


def test_old_task_yaml_keeps_legacy_execution_and_output_contract(tmp_path: Path) -> None:
    path = tmp_path / "task.yaml"
    path.write_text(
        "\n".join(
            [
                "id: agos-old",
                "title: Old task",
                "workflow: feature",
                "gates: []",
                "executor:",
                "  adapter: multica",
                "  agent: Lambda",
                "",
            ]
        ),
        encoding="utf-8",
    )

    task = load_task(path)

    assert task.execution_mode is None
    assert task.output_contract is None
    assert effective_task_mode(task) == "legacy"
    assert task_requires_output_directory(task) is True


def test_explicit_source_code_task_does_not_require_output_directory() -> None:
    task = _task(execution_mode="candidate", output_contract="source_code")

    assert effective_task_mode(task) == "candidate"
    assert task_requires_output_directory(task) is False


def test_standalone_task_requires_output_directory() -> None:
    task = _task(execution_mode="legacy", output_contract="standalone")

    assert task_requires_output_directory(task) is True


def test_normalized_result_has_stable_fields() -> None:
    result = TaskExecutionResult(
        task_id="agos-01",
        mode="candidate",
        run_id="auto-run-01",
        state="completed",
        candidate_ids=["candidate-01"],
        applied_candidate_ids=["candidate-01"],
    )

    assert result.model_dump(mode="json") == {
        "task_id": "agos-01",
        "mode": "candidate",
        "run_id": "auto-run-01",
        "state": "completed",
        "issue_id": None,
        "candidate_ids": ["candidate-01"],
        "applied_candidate_ids": ["candidate-01"],
        "blocked_stage": None,
        "blocked_reason": None,
        "compatibility_warnings": [],
    }
