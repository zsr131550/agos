from __future__ import annotations

import sys

import pytest
import yaml

from agos.core.execution_pipeline import AutoExecutionResult
from agos.core.repo import repo_paths
from agos.core.task import load_task
from agos.core.task_execution import TaskExecutionRequest
from agos.core.task_execution_service import TaskExecutionError


def _write_candidate_config(tmp_repo, *, argv: list[str]) -> None:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "default_workflow": "feature",
                "workflows": {"feature": {"gates": []}},
                "task_execution": {
                    "mode": "candidate",
                    "output_contract": "source_code",
                },
                "workers": {"offline": {"type": "command", "argv": argv}},
                "reviewers": {
                    "clean": {"type": "fake", "role": "reviewer", "required": True}
                },
                "allow_fake_reviewer": True,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_registry_candidate_runner_always_uses_guarded_apply(monkeypatch, tmp_repo) -> None:
    from agos.cli.task_execution_registry import build_task_execution_service

    _write_candidate_config(tmp_repo, argv=[sys.executable, "-c", "pass"])
    captured = {}

    def fake_run_auto_execution(service, **kwargs):
        captured.update(kwargs)
        task = load_task(service.paths.task_yaml)
        return AutoExecutionResult(
            plan_id=f"auto-plan-{task.id}",
            task_id=task.id,
            run_id=f"auto-run-{task.id}",
            run_state="completed",
            dry_run=False,
        )

    monkeypatch.setattr(
        "agos.cli.task_execution_registry.run_auto_execution",
        fake_run_auto_execution,
    )

    result = build_task_execution_service(tmp_repo).start(
        TaskExecutionRequest(title="Offline candidate")
    )

    assert result.mode == "candidate"
    assert captured["apply"] is True
    assert captured["resume_run_id"] is None


def test_registry_readiness_rejects_missing_local_command_before_publish(tmp_repo) -> None:
    from agos.cli.task_execution_registry import build_task_execution_service

    _write_candidate_config(tmp_repo, argv=["agos-command-that-does-not-exist"])

    with pytest.raises(TaskExecutionError, match="command not found"):
        build_task_execution_service(tmp_repo).start(
            TaskExecutionRequest(title="Unavailable candidate")
        )

    assert not repo_paths(tmp_repo).task_yaml.exists()
