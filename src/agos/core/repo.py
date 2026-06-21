""".agos/ path layout and git helpers (governed-repo side only)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agos.core.command import run_command


@dataclass(frozen=True)
class AgosPaths:
    """Resolved `.agos/` paths for a governed repo root."""

    root: Path
    agos_dir: Path
    agos_yaml: Path
    repo_ledger: Path
    tasks: Path
    current_task: Path
    task_yaml: Path
    status_json: Path
    ledger: Path
    evidence: Path
    hooks: Path


def task_paths(repo_root: Path, task_dir: Path) -> AgosPaths:
    """Build `.agos/` paths for a specific task directory."""

    agos = repo_root / ".agos"
    return AgosPaths(
        root=repo_root,
        agos_dir=agos,
        agos_yaml=agos / "agos.yaml",
        repo_ledger=agos / "repo_ledger.jsonl",
        tasks=agos / "tasks",
        current_task=task_dir,
        task_yaml=task_dir / "task.yaml",
        status_json=task_dir / "status.json",
        ledger=task_dir / "ledger.jsonl",
        evidence=task_dir / "evidence",
        hooks=agos / "hooks",
    )


def repo_paths(repo_root: Path) -> AgosPaths:
    """Build the `.agos/` path layout for the current task."""

    return task_paths(repo_root, repo_root / ".agos" / "tasks" / "current")


def staging_task_dir(repo_root: Path, task_id: str) -> Path:
    return repo_root / ".agos" / "tasks" / "staging" / task_id


def agos_dir(repo_root: Path) -> Path:
    return repo_paths(repo_root).agos_dir


def config_path(repo_root: Path) -> Path:
    return repo_paths(repo_root).agos_yaml


def repo_ledger_path(repo_root: Path) -> Path:
    return repo_paths(repo_root).repo_ledger


def tasks_dir(repo_root: Path) -> Path:
    return repo_paths(repo_root).tasks


def current_task_dir(repo_root: Path) -> Path:
    return repo_paths(repo_root).current_task


def current_task_is_active(task_dir: Path) -> bool:
    return task_dir.exists() and any(task_dir.iterdir())


def git_head(repo_root: Path) -> str:
    """Return the full SHA of `HEAD` in the governed repo."""

    out = run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def git_status_porcelain(repo_root: Path) -> str:
    """Return `git status --porcelain` output."""

    out = run_command(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` to the nearest git repo or `.agos/` root."""

    here = (start or Path.cwd()).resolve()
    for cand in [here, *here.parents]:
        if (cand / ".agos").is_dir() or (cand / ".git").exists():
            return cand
    raise FileNotFoundError("No git repository or .agos/ found walking up from " + str(here))


def find_initialized_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` to a repo root with `.agos/agos.yaml` present."""

    here = (start or Path.cwd()).resolve()
    for cand in [here, *here.parents]:
        if (cand / ".agos" / "agos.yaml").is_file():
            return cand
    raise FileNotFoundError("No initialized AGOS repository found walking up from " + str(here))
