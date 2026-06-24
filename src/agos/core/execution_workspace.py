"""Workspace and patch helpers for execution candidates."""
from __future__ import annotations

import hashlib
import re
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from agos.core.command import run_command
from agos.core.execution import ExecutionSubtask, WorkspaceBinding
from agos.core.repo import AgosPaths


@dataclass(frozen=True)
class PatchApplyEvidence:
    state: Literal["passed", "failed"]
    evidence_ref: str
    command: str


class ExecutionWorkspaceManager:
    """Create isolated git worktrees and turn their changes into candidate patches."""

    def __init__(
        self,
        paths: AgosPaths,
        *,
        task_id: str,
        worktree_root: Path | None = None,
    ) -> None:
        self.paths = paths
        self.task_id = task_id
        self.worktree_root = (
            worktree_root
            if worktree_root is not None
            else paths.root.parent / ".agos-worktrees" / task_id
        )

    def workspace_path_for_subtask(self, subtask_id: str) -> Path:
        return self.validate_workspace_path(self.worktree_root / _safe_path_component(subtask_id))

    def validate_workspace_path(self, path: Path) -> Path:
        resolved = path.resolve()
        governed_root = self.paths.root.resolve()
        root = self.worktree_root.resolve()
        if resolved.is_relative_to(governed_root):
            raise ValueError(f"workspace path is inside the governed working tree: {resolved}")
        if not resolved.is_relative_to(root):
            raise ValueError(f"workspace path must be under task worktree root: {root}")
        return resolved

    def create_workspace(self, subtask: ExecutionSubtask) -> WorkspaceBinding:
        workspace_path = self.workspace_path_for_subtask(subtask.id)
        workspace_path.parent.mkdir(parents=True, exist_ok=True)
        base_commit = _git_text(self.paths.root, "rev-parse", "HEAD")
        base_ref = _git_text(self.paths.root, "rev-parse", "--abbrev-ref", "HEAD")

        run_command(
            ["git", "worktree", "add", "--detach", str(workspace_path), base_commit],
            cwd=self.paths.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return WorkspaceBinding(
            subtask_id=subtask.id,
            path=str(workspace_path),
            base_ref=base_ref,
            base_commit=base_commit,
        )

    def capture_patch(self, workspace: Path) -> bytes:
        workspace = workspace.resolve()
        if not (workspace / ".git").exists():
            raise ValueError(f"workspace is not a git worktree: {workspace}")

        untracked = _git_z(workspace, "ls-files", "--others", "--exclude-standard")
        if untracked:
            run_command(
                ["git", "add", "-N", "--", *untracked],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )

        proc = run_command(
            ["git", "diff", "--binary", "HEAD"],
            cwd=workspace,
            check=True,
            capture_output=True,
        )
        patch = proc.stdout if isinstance(proc.stdout, bytes) else proc.stdout.encode()
        if not patch:
            raise ValueError("candidate patch is empty")
        return patch

    def validate_patch_scope(self, patch_bytes: bytes, write_scope: list[str]) -> None:
        paths = candidate_patch_paths(patch_bytes)
        allowed = {_normalize_git_path(path) for path in write_scope}
        outside = sorted(path for path in paths if not _path_is_allowed(path, allowed))
        if outside:
            joined = ", ".join(outside)
            raise ValueError(f"candidate patch touches files outside write_scope: {joined}")

    def check_patch_applies(
        self,
        *,
        candidate_id: str,
        patch_bytes: bytes,
        evidence_dir: Path,
    ) -> PatchApplyEvidence:
        command = "git apply --check --binary -"
        log_dir = evidence_dir / "gates"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_name = f"{candidate_id}-patch_applies-{_fsafe_ts()}-{uuid4().hex[:8]}.log"
        log_path = log_dir / log_name
        ref = f"gates/{log_name}"
        check_workspace = self._create_verification_workspace(candidate_id)
        try:
            proc = run_command(
                ["git", "apply", "--check", "--binary", "-"],
                cwd=check_workspace,
                input=patch_bytes,
                capture_output=True,
            )
            stdout = _decode_output(proc.stdout)
            stderr = _decode_output(proc.stderr)
            log_path.write_text(
                (
                    f"command: {command}\n"
                    f"workspace: {check_workspace}\n"
                    f"exit_code: {proc.returncode}\n"
                    f"--- stdout ---\n{stdout}\n"
                    f"--- stderr ---\n{stderr}\n"
                ),
                encoding="utf-8",
            )
            state: Literal["passed", "failed"] = "passed" if proc.returncode == 0 else "failed"
            return PatchApplyEvidence(state=state, evidence_ref=ref, command=command)
        finally:
            self._remove_worktree(check_workspace)

    def _create_verification_workspace(self, candidate_id: str) -> Path:
        root = self.validate_workspace_path(
            self.worktree_root / "_verify" / f"{_safe_path_component(candidate_id)}-{uuid4().hex[:8]}"
        )
        root.parent.mkdir(parents=True, exist_ok=True)
        base_commit = _git_text(self.paths.root, "rev-parse", "HEAD")
        run_command(
            ["git", "worktree", "add", "--detach", str(root), base_commit],
            cwd=self.paths.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return root

    def _remove_worktree(self, path: Path) -> None:
        run_command(
            ["git", "worktree", "remove", "--force", str(path)],
            cwd=self.paths.root,
            capture_output=True,
            text=True,
        )
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def patch_bytes_sha256(patch_bytes: bytes) -> str:
    return hashlib.sha256(patch_bytes).hexdigest()


def candidate_patch_paths(patch_bytes: bytes) -> set[str]:
    paths: set[str] = set()
    for raw_line in patch_bytes.splitlines():
        line = raw_line.decode("utf-8", errors="replace")
        if not line.startswith("diff --git "):
            continue
        parts = shlex.split(line)
        if len(parts) < 4:
            continue
        for raw_path in parts[2:4]:
            path = _strip_git_prefix(raw_path)
            if path != "/dev/null":
                paths.add(path)
    return paths


def _git_text(cwd: Path, *args: str) -> str:
    proc = run_command(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _git_z(cwd: Path, *args: str) -> list[str]:
    proc = run_command(
        ["git", *args, "-z"],
        cwd=cwd,
        check=True,
        capture_output=True,
    )
    stdout = proc.stdout if isinstance(proc.stdout, bytes) else proc.stdout.encode()
    return [part.decode("utf-8") for part in stdout.split(b"\0") if part]


def _strip_git_prefix(path: str) -> str:
    if path.startswith(("a/", "b/")):
        return _normalize_git_path(path[2:])
    return _normalize_git_path(path)


def _normalize_git_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def _path_is_allowed(path: str, allowed: set[str]) -> bool:
    normalized = _normalize_git_path(path)
    return any(normalized == scope or normalized.startswith(f"{scope}/") for scope in allowed)


def _safe_path_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    if not component:
        raise ValueError("path component must be non-empty")
    return component


def _fsafe_ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")
