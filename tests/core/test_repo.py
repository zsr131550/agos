"""Tests for .agos/ path layout and git helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from agos.core.repo import AgosPaths, find_repo_root, git_head, git_status_porcelain, repo_paths


def test_repo_paths_layout(tmp_repo: Path) -> None:
    p = repo_paths(tmp_repo)
    assert isinstance(p, AgosPaths)
    assert p.root == tmp_repo
    assert p.agos_dir == tmp_repo / ".agos"
    assert p.agos_yaml == tmp_repo / ".agos" / "agos.yaml"
    assert p.repo_ledger == tmp_repo / ".agos" / "repo_ledger.jsonl"
    assert p.tasks == tmp_repo / ".agos" / "tasks"
    assert p.current_task == tmp_repo / ".agos" / "tasks" / "current"
    assert p.task_yaml == tmp_repo / ".agos" / "tasks" / "current" / "task.yaml"
    assert p.status_json == tmp_repo / ".agos" / "tasks" / "current" / "status.json"
    assert p.ledger == tmp_repo / ".agos" / "tasks" / "current" / "ledger.jsonl"
    assert p.evidence == tmp_repo / ".agos" / "tasks" / "current" / "evidence"
    assert p.hooks == tmp_repo / ".agos" / "hooks"


def test_git_head_returns_sha(tmp_repo: Path) -> None:
    head = git_head(tmp_repo)
    assert len(head) == 40
    assert all(c in "0123456789abcdef" for c in head)


def test_git_status_porcelain_empty(tmp_repo: Path) -> None:
    assert git_status_porcelain(tmp_repo) == ""


def test_git_status_porcelain_dirty(tmp_repo: Path) -> None:
    (tmp_repo / "new.txt").write_text("x", encoding="utf-8")
    out = git_status_porcelain(tmp_repo)
    assert "new.txt" in out


def test_find_repo_root_walks_up(tmp_repo: Path) -> None:
    (tmp_repo / ".agos").mkdir()
    nested = tmp_repo / "a" / "b"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tmp_repo


def test_find_repo_root_none(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        find_repo_root(tmp_path)
