"""Build the configured LLM planner adapter at the CLI boundary.

Mirrors ``reviewer_registry`` / ``worker_registry``: the adapter is constructed
here so ``core`` never imports an adapter. ``create_execution_plan`` gates
actual use on ``orchestration.planner.enabled``.
"""
from __future__ import annotations

from pathlib import Path

from agos.adapters.planner_cli import CliPlannerAdapter
from agos.core.config import load_config


def configured_planner_adapter(repo_root: Path) -> CliPlannerAdapter:
    """Return the configured LLM planner adapter for ``repo_root``."""

    planner_config = load_config(repo_root).orchestration.planner
    return CliPlannerAdapter(
        executor=planner_config.executor,
        command=planner_config.command,
        cwd=repo_root,
        timeout_seconds=planner_config.timeout_seconds,
    )
