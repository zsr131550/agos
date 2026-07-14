from __future__ import annotations

import sys

import pytest
import yaml

from agos.core.config import AGOSConfig
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


def test_registry_readiness_reports_all_missing_local_cli_commands(
    monkeypatch,
    tmp_repo,
) -> None:
    from agos.cli.task_execution_registry import candidate_readiness_issues

    config = AGOSConfig.model_validate(
        {
            "executor": {"agent": "unused"},
            "workers": {
                "codex_worker": {"type": "codex_cli"},
                "claude_worker": {"type": "claude_code"},
                "multica_worker": {"type": "multica"},
            },
            "reviewers": {
                "codex_review": {"type": "codex_cli", "role": "reviewer"},
                "claude_review": {"type": "claude_code", "role": "reviewer"},
            },
            "orchestration": {
                "planner": {"enabled": True, "executor": "claude_code"}
            },
        }
    )
    monkeypatch.setattr("agos.cli.task_execution_registry.shutil.which", lambda _command: None)

    issues = candidate_readiness_issues(config, tmp_repo)

    assert issues == [
        "worker codex_worker command not found: codex",
        "worker claude_worker command not found: claude",
        "worker multica_worker command not found: multica",
        "reviewer codex_review command not found: codex",
        "reviewer claude_review command not found: claude",
        "planner command not found: claude",
    ]


def test_registry_readiness_accepts_executable_relative_to_repo(tmp_repo) -> None:
    from agos.cli.task_execution_registry import candidate_readiness_issues

    executable = tmp_repo / "tools" / "offline-worker"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    config = AGOSConfig.model_validate(
        {
            "executor": {"agent": "unused"},
            "workers": {
                "offline": {"type": "command", "argv": ["tools/offline-worker"]}
            },
        }
    )

    assert candidate_readiness_issues(config, tmp_repo) == []
