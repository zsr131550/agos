"""Build unified task execution services at the CLI boundary."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from agos.adapters.local_cli_executor import LocalCliExecutorAdapter
from agos.cli.executor_registry import configured_executor_adapter, executor_adapter_for
from agos.cli.orchestration_registry import register_configured_orchestration_backends
from agos.cli.planner_registry import configured_planner_adapter
from agos.cli.reviewer_registry import configured_reviewer_adapters, configured_reviewer_specs
from agos.cli.worker_registry import register_configured_worker_adapters
from agos.core.config import AGOSConfig
from agos.core.execution_pipeline import run_auto_execution
from agos.core.execution_service import ExecutionService
from agos.core.repo import AgosPaths, repo_paths
from agos.core.task_execution import ExecutorSelection
from agos.core.task_execution_service import ResolvedLegacyExecutor, TaskExecutionService


def build_task_execution_service(repo_root: Path) -> TaskExecutionService:
    """Construct the provider-neutral task service with configured CLI adapters."""

    root = Path(repo_root)
    paths = repo_paths(root)

    def legacy_executor_factory(
        staging_paths: AgosPaths,
        selection: ExecutorSelection | None,
    ) -> ResolvedLegacyExecutor:
        adapter = (
            executor_adapter_for(
                staging_paths,
                selection.adapter,
                command=selection.command,
            )
            if selection is not None
            else configured_executor_adapter(staging_paths)
        )
        return ResolvedLegacyExecutor(
            adapter=adapter,
            synchronous=isinstance(adapter, LocalCliExecutorAdapter),
        )

    def candidate_runner(candidate_paths: AgosPaths, resume_run_id: str | None):
        service = ExecutionService(candidate_paths)
        register_configured_worker_adapters(service)
        register_configured_orchestration_backends(service)
        config = AGOSConfig.load(candidate_paths.agos_yaml)
        planner = (
            configured_planner_adapter(candidate_paths.root)
            if config.orchestration.planner.enabled
            else None
        )
        return run_auto_execution(
            service,
            apply=True,
            planner=planner,
            reviewer_adapters=configured_reviewer_adapters(candidate_paths.root),
            reviewer_specs=configured_reviewer_specs(candidate_paths.root),
            resume_run_id=resume_run_id,
        )

    return TaskExecutionService(
        paths,
        legacy_executor_factory=legacy_executor_factory,
        candidate_runner=candidate_runner,
        candidate_readiness=lambda config: candidate_readiness_issues(config, root),
    )


def candidate_readiness_issues(config: AGOSConfig, repo_root: Path) -> list[str]:
    """Return local structural readiness failures without contacting providers."""

    commands: list[tuple[str, str]] = []
    for name, worker in config.workers.items():
        if worker.type == "command" and worker.argv:
            commands.append((f"worker {name}", worker.argv[0]))
        elif worker.type == "codex_cli":
            commands.append((f"worker {name}", worker.command or "codex"))
        elif worker.type == "claude_code":
            commands.append((f"worker {name}", worker.command or "claude"))
        elif worker.type == "multica":
            commands.append((f"worker {name}", worker.command or "multica"))

    for name, reviewer in config.reviewers.items():
        if reviewer.type == "codex_cli":
            commands.append((f"reviewer {name}", reviewer.command or "codex"))
        elif reviewer.type == "claude_code":
            commands.append((f"reviewer {name}", reviewer.command or "claude"))

    planner = config.orchestration.planner
    if planner.enabled:
        default = "codex" if planner.executor == "codex_cli" else "claude"
        commands.append(("planner", planner.command or default))

    return [
        f"{component} command not found: {command}"
        for component, command in commands
        if not _command_available(command, repo_root)
    ]


def _command_available(command: str, repo_root: Path) -> bool:
    candidate = Path(command).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        resolved = candidate if candidate.is_absolute() else repo_root / candidate
        return resolved.is_file() and os.access(resolved, os.X_OK)
    return shutil.which(command) is not None
