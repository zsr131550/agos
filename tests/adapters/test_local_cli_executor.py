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


def _source_code_task() -> Task:
    return _task().model_copy(
        update={
            "execution_mode": "legacy",
            "output_contract": "source_code",
        }
    )


def test_local_cli_executor_records_success_events_and_status(monkeypatch, tmp_repo):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run_command(args, **kwargs):
        output_dir = tmp_repo / "outputs" / "agos-01"
        assert output_dir.is_dir()
        (output_dir / "result.txt").write_text("done", encoding="utf-8")
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


def test_local_cli_executor_status_rejects_existing_completed_run_with_empty_current_output(tmp_repo):
    task = _task()
    current_dir = tmp_repo / ".agos" / "tasks" / "current"
    task.save(current_dir / "task.yaml")
    output_dir = tmp_repo / "outputs" / task.id
    output_dir.mkdir(parents=True)
    adapter = _TestExecutor(evidence_dir=current_dir / "evidence", cwd=tmp_repo)
    path = adapter._state_path("empty-output-run")
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"state": "completed", "detail": "agent asked for approval", "events": []}',
        encoding="utf-8",
    )

    status = adapter.status("empty-output-run")

    assert status.state == "failed"
    assert "completed without writing files to outputs/agos-01" in status.detail


def test_codex_and_claude_executor_args(tmp_repo):
    codex = CodexCliExecutorAdapter(command="codex", evidence_dir=tmp_repo / "e", cwd=tmp_repo)
    claude = ClaudeCodeExecutorAdapter(command="claude", evidence_dir=tmp_repo / "e", cwd=tmp_repo)

    codex_args = codex._start_args("Do work")
    claude_args = claude._start_args("Do work")

    assert codex_args == [
        "codex",
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
        "Do work",
    ]
    assert claude_args == [
        "claude",
        "--safe-mode",
        "--permission-mode",
        "bypassPermissions",
        "-p",
        "--output-format",
        "json",
        "Do work",
    ]


def test_task_prompt_includes_intent_and_acceptance():
    prompt = _task_prompt(_task())

    assert "Task: Implement feature" in prompt
    assert "Update README" in prompt
    assert "- Tests pass" in prompt
    assert "- Docs updated" in prompt


def test_task_prompt_is_noninteractive_and_declares_output_directory():
    prompt = _task_prompt(_task())

    assert "Do not ask clarifying questions" in prompt
    assert "outputs/agos-01" in prompt
    assert "Report the output directory" in prompt


def test_task_prompt_declares_background_executor_mode():
    prompt = _task_prompt(_task())

    assert "You are running as an AGOS background executor/subagent" in prompt
    assert "This AGOS execution contract overrides any local skill" in prompt
    assert "Do not wait for user approval" in prompt
    assert "Do not invoke brainstorming or design-approval gates" in prompt
    assert "Implement immediately" in prompt


def test_local_cli_executor_fails_when_successful_run_leaves_output_empty(monkeypatch, tmp_repo):
    monkeypatch.setattr(
        "agos.adapters.local_cli_executor.run_command",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="I need approval before implementing.",
            stderr="",
        ),
    )
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos" / "evidence", cwd=tmp_repo)

    run = adapter.start(_task())
    status = adapter.status(run.run_id)
    events = list(adapter.stream_events(run.run_id))

    assert status.state == "failed"
    assert "completed without writing files to outputs/agos-01" in status.detail
    assert [event.kind for event in events] == ["error", "error"]


def test_local_cli_executor_retries_once_when_agent_asks_instead_of_writing(
    monkeypatch,
    tmp_repo,
):
    calls: list[list[str]] = []

    def fake_run_command(args, **kwargs):
        del kwargs
        calls.append(args)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=0,
                stdout="Do you want me to use a browser companion before I implement?",
                stderr="",
            )
        assert "previous response stopped without writing output" in args[-1]
        assert "Do not ask questions" in args[-1]
        (tmp_repo / "outputs" / "agos-01" / "index.html").write_text(
            "<!doctype html><title>done</title>",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr("agos.adapters.local_cli_executor.run_command", fake_run_command)
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos" / "evidence", cwd=tmp_repo)

    run = adapter.start(_task())
    status = adapter.status(run.run_id)

    assert len(calls) == 2
    assert status.state == "completed"
    assert status.detail == "done"


def test_source_code_executor_accepts_repo_edit_without_outputs(monkeypatch, tmp_repo):
    calls: list[list[str]] = []

    def fake_run_command(args, **kwargs):
        del kwargs
        calls.append(args)
        (tmp_repo / "README.md").write_text("# changed by executor\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="changed README", stderr="")

    monkeypatch.setattr("agos.adapters.local_cli_executor.run_command", fake_run_command)
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos" / "evidence", cwd=tmp_repo)

    run = adapter.start(_source_code_task())
    status = adapter.status(run.run_id)

    assert len(calls) == 1
    assert status.state == "completed"
    assert status.detail == "changed README"
    assert not (tmp_repo / "outputs" / "agos-01").exists()
    assert "Change governed repository files directly" in calls[0][-1]


def test_source_code_executor_retries_and_rejects_no_repo_change(monkeypatch, tmp_repo):
    calls: list[list[str]] = []

    def fake_run_command(args, **kwargs):
        del kwargs
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="answered only", stderr="")

    monkeypatch.setattr("agos.adapters.local_cli_executor.run_command", fake_run_command)
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos" / "evidence", cwd=tmp_repo)

    run = adapter.start(_source_code_task())
    status = adapter.status(run.run_id)

    assert len(calls) == 2
    assert "previous response stopped without changing governed source files" in calls[1][-1]
    assert status.state == "failed"
    assert "completed without changing governed source files" in status.detail


def test_source_code_executor_does_not_accept_preexisting_dirty_state(monkeypatch, tmp_repo):
    (tmp_repo / "README.md").write_text("# dirty before start\n", encoding="utf-8")
    monkeypatch.setattr(
        "agos.adapters.local_cli_executor.run_command",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="no edit", stderr=""),
    )
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos" / "evidence", cwd=tmp_repo)

    run = adapter.start(_source_code_task())

    assert adapter.status(run.run_id).state == "failed"


def test_source_code_completed_status_does_not_require_output_directory(tmp_repo):
    task = _source_code_task()
    current_dir = tmp_repo / ".agos" / "tasks" / "current"
    task.save(current_dir / "task.yaml")
    adapter = _TestExecutor(evidence_dir=current_dir / "evidence", cwd=tmp_repo)
    path = adapter._state_path("source-code-run")
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"state": "completed", "detail": "changed source", "events": []}',
        encoding="utf-8",
    )

    status = adapter.status("source-code-run")

    assert status.state == "completed"
    assert status.detail == "changed source"
