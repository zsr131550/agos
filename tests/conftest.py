"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
TMP_ROOT = REPO_ROOT.parent / ".pytest_tmp"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def pytest_configure(config: pytest.Config) -> None:
    """Force pytest temp dirs into the repo to avoid unreadable global temp roots."""
    TMP_ROOT.mkdir(exist_ok=True)
    config.option.basetemp = str(TMP_ROOT)


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A throwaway git repo root for tests that need .agos/ + .git/."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    (repo / "README.md").write_text("# t\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, env=env)
    return repo
