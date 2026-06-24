from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agos.core.execution import ExecutionSubtask, ExecutionWorker
from agos.core.execution_workspace import (
    ExecutionWorkspaceManager,
    candidate_patch_paths,
    patch_bytes_sha256,
)
from agos.core.repo import repo_paths


def _subtask(write_scope: list[str]) -> ExecutionSubtask:
    return ExecutionSubtask(
        id="subtask-a",
        title="Subtask A",
        write_scope=write_scope,
        worker=ExecutionWorker(adapter="local_worktree", role="worker_agent"),
    )


def _commit(repo: Path, message: str = "change") -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True, env=env)


def test_workspace_manager_rejects_worktree_inside_governed_repo(tmp_repo):
    manager = ExecutionWorkspaceManager(
        repo_paths(tmp_repo),
        task_id="agos-01",
        worktree_root=tmp_repo / ".agos-worktrees" / "agos-01",
    )

    with pytest.raises(ValueError, match="inside the governed working tree"):
        manager.validate_workspace_path(tmp_repo / ".agos-worktrees" / "agos-01" / "subtask-a")


def test_create_workspace_writes_outside_governed_repo(tmp_repo):
    paths = repo_paths(tmp_repo)
    root = tmp_repo.parent / ".agos-worktrees" / "agos-01"
    manager = ExecutionWorkspaceManager(paths, task_id="agos-01", worktree_root=root)

    binding = manager.create_workspace(_subtask(["src/a.py"]))

    assert Path(binding.path).resolve().is_relative_to(root.resolve())
    assert not Path(binding.path).resolve().is_relative_to(tmp_repo.resolve())
    assert binding.base_commit


def test_capture_patch_includes_tracked_and_untracked_files(tmp_repo):
    paths = repo_paths(tmp_repo)
    manager = ExecutionWorkspaceManager(
        paths,
        task_id="agos-01",
        worktree_root=tmp_repo.parent / ".agos-worktrees" / "agos-01",
    )
    binding = manager.create_workspace(_subtask(["README.md", "src/new.py"]))
    workspace = Path(binding.path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "new.py").write_text("print('hi')\n", encoding="utf-8")

    patch = manager.capture_patch(workspace)
    paths_in_patch = candidate_patch_paths(patch)

    assert b"# changed" in patch
    assert b"print('hi')" in patch
    assert paths_in_patch == {"README.md", "src/new.py"}
    assert len(patch_bytes_sha256(patch)) == 64


def test_validate_patch_scope_rejects_out_of_scope_paths(tmp_repo):
    manager = ExecutionWorkspaceManager(
        repo_paths(tmp_repo),
        task_id="agos-01",
        worktree_root=tmp_repo.parent / ".agos-worktrees" / "agos-01",
    )
    binding = manager.create_workspace(_subtask(["src/allowed.py"]))
    workspace = Path(binding.path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")
    patch = manager.capture_patch(workspace)

    with pytest.raises(ValueError, match="outside write_scope"):
        manager.validate_patch_scope(patch, ["src/allowed.py"])


def test_validate_patch_scope_accepts_directory_scope_for_child_paths(tmp_repo):
    manager = ExecutionWorkspaceManager(
        repo_paths(tmp_repo),
        task_id="agos-01",
        worktree_root=tmp_repo.parent / ".agos-worktrees" / "agos-01",
    )
    binding = manager.create_workspace(_subtask(["src"]))
    workspace = Path(binding.path)
    (workspace / "src").mkdir()
    (workspace / "src" / "allowed.py").write_text("print('ok')\n", encoding="utf-8")
    patch = manager.capture_patch(workspace)

    manager.validate_patch_scope(patch, ["src"])


def test_apply_check_accepts_valid_patch_in_temp_workspace(tmp_repo):
    paths = repo_paths(tmp_repo)
    manager = ExecutionWorkspaceManager(
        paths,
        task_id="agos-01",
        worktree_root=tmp_repo.parent / ".agos-worktrees" / "agos-01",
    )
    binding = manager.create_workspace(_subtask(["README.md"]))
    workspace = Path(binding.path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")
    patch = manager.capture_patch(workspace)

    evidence = manager.check_patch_applies(
        candidate_id="candidate-01",
        patch_bytes=patch,
        evidence_dir=paths.evidence,
    )

    assert evidence.state == "passed"
    assert evidence.evidence_ref.startswith("gates/candidate-01-patch_applies-")


def test_apply_check_records_failed_patch_evidence(tmp_repo):
    paths = repo_paths(tmp_repo)
    manager = ExecutionWorkspaceManager(
        paths,
        task_id="agos-01",
        worktree_root=tmp_repo.parent / ".agos-worktrees" / "agos-01",
    )
    bad_patch = b"diff --git a/missing.txt b/missing.txt\n--- a/missing.txt\n+++ b/missing.txt\n@@ -1 +1 @@\n-a\n+b\n"

    evidence = manager.check_patch_applies(
        candidate_id="candidate-01",
        patch_bytes=bad_patch,
        evidence_dir=paths.evidence,
    )

    assert evidence.state == "failed"
    assert (paths.evidence / evidence.evidence_ref).exists()
