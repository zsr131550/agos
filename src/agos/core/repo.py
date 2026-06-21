"""Helpers for locating and managing the governed repo."""
from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upward from `start` until a git repo root is found."""

    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise FileNotFoundError("No git repository found from the current working directory")


def agos_dir(repo_root: Path) -> Path:
    return repo_root / ".agos"


def config_path(repo_root: Path) -> Path:
    return agos_dir(repo_root) / "agos.yaml"


def repo_ledger_path(repo_root: Path) -> Path:
    return agos_dir(repo_root) / "repo_ledger.jsonl"


def tasks_dir(repo_root: Path) -> Path:
    return agos_dir(repo_root) / "tasks"


def current_task_dir(repo_root: Path) -> Path:
    return tasks_dir(repo_root) / "current"


def current_task_is_active(task_dir: Path) -> bool:
    return task_dir.exists() and any(task_dir.iterdir())

