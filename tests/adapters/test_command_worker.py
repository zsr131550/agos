from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agos.adapters.workers.command import CommandWorkerAdapter
from agos.core.config import WorkerConfig
from agos.core.execution import ExecutionSubtask, ExecutionWorker, WorkspaceBinding
from agos.core.execution_worker import WorkerAssignment, WorkerStartRequest


class _WorkspaceManager:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.captured: list[Path] = []

    def create_workspace(self, subtask: ExecutionSubtask) -> WorkspaceBinding:
        return WorkspaceBinding(
            subtask_id=subtask.id,
            path=str(self.workspace),
            base_ref="main",
            base_commit="a" * 40,
        )

    def capture_patch(self, workspace: Path) -> bytes:
        self.captured.append(workspace)
        content = (workspace / "README.md").read_text(encoding="utf-8")
        return (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1 @@\n"
            "-# original\n"
            f"+{content.rstrip()}\n"
        ).encode("utf-8")


def _subtask() -> ExecutionSubtask:
    return ExecutionSubtask(
        id="offline-edit",
        title="Edit README",
        intent="Make a deterministic local edit.",
        write_scope=["README.md"],
        worker=ExecutionWorker(adapter="offline"),
    )


def _request(workspace: Path) -> WorkerStartRequest:
    return WorkerStartRequest(
        run_id="offline-run-01",
        subtask_id="offline-edit",
        prompt="Edit README",
        workspace_path=str(workspace),
    )


def test_command_worker_config_requires_nonempty_argv() -> None:
    with pytest.raises(ValueError, match="argv"):
        WorkerConfig(type="command", argv=[])

    with pytest.raises(ValueError, match="argv"):
        WorkerConfig(type="command")


def test_command_worker_runs_argv_in_prepared_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# original\n", encoding="utf-8")
    manager = _WorkspaceManager(workspace)
    adapter = CommandWorkerAdapter(
        name="offline",
        argv=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('README.md').write_text('# offline\\n')",
        ],
        workspace_manager=manager,
        timeout_seconds=10,
    )
    prepared = adapter.prepare(WorkerAssignment(subtask=_subtask()))

    run = adapter.start(_request(workspace))
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)
    exported = adapter.export_candidate(prepared.handle)

    assert run.state == "completed"
    assert status.state == "completed"
    assert (workspace / "README.md").read_text(encoding="utf-8") == "# offline\n"
    assert exported["patch_bytes"].startswith(b"diff --git")
    assert manager.captured == [workspace]


def test_command_worker_passes_structured_argv_without_shell(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_command(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr("agos.adapters.workers.command.run_command", fake_run_command)
    argv = [sys.executable, "-c", "print('ok')"]
    adapter = CommandWorkerAdapter(
        name="offline",
        argv=argv,
        workspace_manager=_WorkspaceManager(tmp_path),
        env={"AGOS_OFFLINE": "1"},
    )

    run = adapter.start(_request(tmp_path))

    assert run.state == "completed"
    assert captured["args"] == argv
    assert captured.get("shell") in {None, False}
    assert captured["stdin"] == subprocess.DEVNULL
    assert captured["cwd"] == tmp_path
    assert captured["env"]["AGOS_OFFLINE"] == "1"


def test_command_worker_records_nonzero_and_timeout_as_failed(monkeypatch, tmp_path: Path) -> None:
    adapter = CommandWorkerAdapter(
        name="offline",
        argv=[sys.executable, "-c", "pass"],
        workspace_manager=_WorkspaceManager(tmp_path),
    )
    monkeypatch.setattr(
        "agos.adapters.workers.command.run_command",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=7, stdout="", stderr="bad edit"),
    )
    failed = adapter.start(_request(tmp_path))
    assert adapter.poll(failed.run_id, subtask_id=failed.subtask_id).detail == "bad edit"

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired([sys.executable], timeout=3)

    monkeypatch.setattr("agos.adapters.workers.command.run_command", timeout)
    timed_out = adapter.start(_request(tmp_path).model_copy(update={"run_id": "timeout-run"}))
    status = adapter.poll(timed_out.run_id, subtask_id=timed_out.subtask_id)
    assert status.state == "failed"
    assert "timed out" in (status.detail or "")


def test_command_worker_health_checks_local_executable(tmp_path: Path) -> None:
    adapter = CommandWorkerAdapter(
        name="offline",
        argv=[sys.executable, "-c", "pass"],
        workspace_manager=_WorkspaceManager(tmp_path),
    )

    health = adapter.health()

    assert health.state == "healthy"
    assert health.checks[0].name == "command_available"
