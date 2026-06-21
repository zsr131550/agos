""".agos/ path layout and git helpers (governed-repo side only)."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgosPaths:
    """Resolved .agos/ paths for a governed repo root."""

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


def repo_paths(repo_root: Path) -> AgosPaths:
    """Build the .agos/ path layout for the given repo root."""
    agos = repo_root / ".agos"
    current = agos / "tasks" / "current"
    return AgosPaths(
        root=repo_root,
        agos_dir=agos,
        agos_yaml=agos / "agos.yaml",
        repo_ledger=agos / "repo_ledger.jsonl",
        tasks=agos / "tasks",
        current_task=current,
        task_yaml=current / "task.yaml",
        status_json=current / "status.json",
        ledger=current / "ledger.jsonl",
        evidence=current / "evidence",
        hooks=agos / "hooks",
    )


def git_head(repo_root: Path) -> str:
    """Return the full SHA of HEAD in the governed repo."""
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def git_status_porcelain(repo_root: Path) -> str:
    """Return `git status --porcelain` output (may be empty)."""
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (cwd if None) to the first dir containing .agos/."""
    here = (start or Path.cwd()).resolve()
    for cand in [here, *here.parents]:
        if (cand / ".agos").is_dir():
            return cand
    raise FileNotFoundError("No .agos/ found walking up from " + str(here))
