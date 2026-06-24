from __future__ import annotations

import subprocess
from types import SimpleNamespace

from agos.adapters.local_cli_executor import (
    ClaudeCodeExecutorAdapter,
    CodexCliExecutorAdapter,
    LocalCliExecutorAdapter,
    _task_prompt,
)
from agos.core.task import ExecutorBinding, Task


class _TestExecutor(LocalCliExecutorAdapter):
    def __init__(self, *, evidence_dir, cwd) -> None:
        super().__init__(name="test_cli", command="agent", evidence_dir=evidence_dir, cwd=cwd)

    def _start_args(self, prompt: str) -> list[str]:
        return [self.command, "--prompt", prompt]


def _task() -> Task:
    return Task(
        id="agos-01",
        title="Implement feature",
        intent="Update README",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="test_cli", agent="agent"),
        acceptance=["Tests pass", "Docs updated"],
    )


def test_local_cli_executor_records_success_events_and_status(monkeypatch, tmp_repo):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run_command(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr("agos.adapters.local_cli_executor.run_command", fake_run_command)
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos" / "evidence", cwd=tmp_repo)

    run = adapter.start(_task())
    events = list(adapter.stream_events(run.run_id))
    status = adapter.status(run.run_id)

    assert run.adapter == "test_cli"
    assert calls[0][0][0:2] == ["agent", "--prompt"]
    assert calls[0][1]["cwd"] == tmp_repo
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert [event.kind for event in events] == ["text", "run_complete"]
    assert [event.seq for event in adapter.stream_events(run.run_id, since=1)] == [2]
    assert status.state == "completed"
    assert status.detail == "done"


def test_local_cli_executor_records_nonzero_failure(monkeypatch, tmp_repo):
    monkeypatch.setattr(
        "agos.adapters.local_cli_executor.run_command",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="", stderr="bad"),
    )
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos" / "evidence", cwd=tmp_repo)

    run = adapter.start(_task())
    events = list(adapter.stream_events(run.run_id))
    status = adapter.status(run.run_id)

    assert [event.kind for event in events] == ["error", "error"]
    assert status.state == "failed"
    assert status.detail == "bad"


def test_local_cli_executor_records_timeout_and_oserror(monkeypatch, tmp_repo):
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos" / "evidence", cwd=tmp_repo)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["agent"], timeout=5)

    monkeypatch.setattr("agos.adapters.local_cli_executor.run_command", timeout)
    timeout_run = adapter.start(_task())
    assert adapter.status(timeout_run.run_id).detail == "timed out after 5 seconds"

    monkeypatch.setattr(
        "agos.adapters.local_cli_executor.run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")),
    )
    os_run = adapter.start(_task())
    assert adapter.status(os_run.run_id).detail == "missing"


def test_local_cli_executor_status_handles_missing_and_unknown_state(tmp_repo):
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos" / "evidence", cwd=tmp_repo)

    assert adapter.status("missing-run").state == "failed"
    path = adapter._state_path("weird-run")
    path.parent.mkdir(parents=True)
    path.write_text('{"state": "surprising", "detail": 123}', encoding="utf-8")

    status = adapter.status("weird-run")

    assert status.state == "failed"
    assert status.detail == "123"


def test_codex_and_claude_executor_args(tmp_repo):
    codex = CodexCliExecutorAdapter(command="codex", evidence_dir=tmp_repo / "e", cwd=tmp_repo)
    claude = ClaudeCodeExecutorAdapter(command="claude", evidence_dir=tmp_repo / "e", cwd=tmp_repo)

    assert codex._start_args("Do work") == ["codex", "exec", "--json", "Do work"]
    assert claude._start_args("Do work") == ["claude", "-p", "--output-format", "json", "Do work"]


def test_task_prompt_includes_intent_and_acceptance():
    prompt = _task_prompt(_task())

    assert "Task: Implement feature" in prompt
    assert "Update README" in prompt
    assert "- Tests pass" in prompt
    assert "- Docs updated" in prompt
