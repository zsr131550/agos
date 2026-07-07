from __future__ import annotations

import json
import subprocess

import pytest

from agos.adapters.workers.claude_code import ClaudeWorkerAdapter
from agos.adapters.workers.transport import load_json_list
from agos.core.execution_worker import WorkerStartRequest


def _request(tmp_path, *, run_id="run-01", subtask_id="sub-01", prompt="do the work") -> WorkerStartRequest:
    return WorkerStartRequest(
        run_id=run_id,
        subtask_id=subtask_id,
        prompt=prompt,
        workspace_path=str(tmp_path),
    )


def _proc(stdout: str, args=("claude",)) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")


def _runner(*, bg_stdout='backgrounded · abc12345', agents=None, sync_stdout="{}"):
    """Fake `run_command` that dispatches on the claude subcommand in args."""

    def runner(args, **kwargs):
        if "--bg" in args:
            return _proc(bg_stdout, args=tuple(args))
        if "agents" in args:
            if isinstance(agents, Exception):
                raise agents
            return _proc(agents if agents is not None else "[]", args=tuple(args))
        return _proc(sync_stdout, args=tuple(args))

    return runner


def _adapter(tmp_path, **overrides) -> ClaudeWorkerAdapter:
    kwargs: dict[str, object] = dict(
        name="claude",
        command="claude",
        timeout_seconds=5,
    )
    kwargs.update(overrides)
    return ClaudeWorkerAdapter(**kwargs)  # type: ignore[arg-type]


# --- sync mode (default) -----------------------------------------------------


def test_sync_start_returns_completed_on_success(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def runner(args, **kwargs):
        del kwargs
        calls.append(args)
        return _proc(
            json.dumps({"session_id": "sess-1", "is_error": "false", "result": "ok"}),
            args=tuple(args),
        )

    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        runner,
    )
    adapter = _adapter(tmp_path)

    run = adapter.start(_request(tmp_path))

    assert run.state == "completed"
    assert run.run_id == "sess-1"
    assert calls[0][:4] == ["claude", "--safe-mode", "--permission-mode", "bypassPermissions"]
    assert "Do not ask clarifying questions" in calls[0][-1]


def test_sync_start_returns_failed_when_is_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(sync_stdout=json.dumps({"session_id": "sess-1", "is_error": "true", "result": "boom"})),
    )
    adapter = _adapter(tmp_path)

    run = adapter.start(_request(tmp_path))

    assert run.state == "failed"


def test_sync_start_caches_artifact_refs(monkeypatch, tmp_path):
    (tmp_path / "out.txt").write_text("done", encoding="utf-8")
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(sync_stdout=json.dumps({"session_id": "sess-1", "is_error": "false"})),
    )
    adapter = _adapter(tmp_path, artifact_globs=("*.txt",))

    adapter.start(_request(tmp_path))
    status = adapter.poll("sess-1", subtask_id="sub-01")

    assert status.state == "completed"
    assert "out.txt" in status.output_refs


def test_sync_poll_returns_cached_status(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(sync_stdout=json.dumps({"session_id": "sess-1", "is_error": "false", "result": "ok"})),
    )
    adapter = _adapter(tmp_path)

    adapter.start(_request(tmp_path))
    status = adapter.poll("sess-1", subtask_id="sub-01")

    assert status.state == "completed"
    assert status.detail == "ok"


def test_sync_poll_cache_missing_soft_falls_back_to_running(monkeypatch, tmp_path):
    adapter = _adapter(tmp_path)

    status = adapter.poll("unknown-run", subtask_id="sub-01")

    # Previously this hard-failed; the fix soft-falls back to running.
    assert status.state == "running"
    assert status.detail is not None


# --- async mode (claude_async_poll=True) -------------------------------------


def test_async_start_uses_bg_and_returns_running_with_bg_id(monkeypatch, tmp_path):
    seen_args: list[list[str]] = []

    def runner(args, **kwargs):
        seen_args.append(list(args))
        return _proc("backgrounded · 1a2b3c4d", args=tuple(args))

    monkeypatch.setattr("agos.adapters.workers.claude_code.run_command", runner)
    adapter = _adapter(tmp_path, claude_async_poll=True)

    run = adapter.start(_request(tmp_path))

    assert run.state == "running"
    assert run.run_id == "1a2b3c4d"
    assert "--bg" in seen_args[0]
    assert run.metadata.get("async") == "true"


def test_async_start_raises_when_bg_id_unparseable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(bg_stdout="claude did not background this turn"),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True)

    with pytest.raises(RuntimeError, match="backgrounded session id"):
        adapter.start(_request(tmp_path))


def test_async_poll_working_maps_to_running(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(agents=json.dumps([{"id": "abc12345", "state": "working"}])),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True)

    adapter.start(_request(tmp_path))
    status = adapter.poll("abc12345", subtask_id="sub-01")

    assert status.state == "running"


def test_async_poll_done_maps_to_completed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(agents=json.dumps([{"id": "abc12345", "state": "done"}])),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True)

    adapter.start(_request(tmp_path))
    status = adapter.poll("abc12345", subtask_id="sub-01")

    assert status.state == "completed"


def test_async_poll_done_detail_notes_indistinguishable_outcome(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(agents=json.dumps([{"id": "abc12345", "state": "done"}])),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True)

    adapter.start(_request(tmp_path))
    status = adapter.poll("abc12345", subtask_id="sub-01")

    assert status.detail is not None
    assert "indistinguishable" in status.detail


def test_async_poll_blocked_soft_falls_back_to_running(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(agents=json.dumps([{"id": "abc12345", "state": "blocked"}])),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True)

    adapter.start(_request(tmp_path))
    status = adapter.poll("abc12345", subtask_id="sub-01")

    assert status.state == "running"


def test_async_poll_unknown_state_soft_falls_back_to_running(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(agents=json.dumps([{"id": "abc12345", "state": "something_new"}])),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True)

    adapter.start(_request(tmp_path))
    status = adapter.poll("abc12345", subtask_id="sub-01")

    assert status.state == "running"


def test_async_poll_session_not_found_soft_falls_back_to_running(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(agents=json.dumps([{"id": "other-id", "state": "done"}])),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True)

    adapter.start(_request(tmp_path))
    status = adapter.poll("abc12345", subtask_id="sub-01")

    assert status.state == "running"
    assert "not found" in status.detail


def test_async_poll_invalid_json_soft_falls_back_to_running(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(agents="not json at all"),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True)

    adapter.start(_request(tmp_path))
    status = adapter.poll("abc12345", subtask_id="sub-01")

    assert status.state == "running"
    assert "invalid JSON" in status.detail


def test_async_poll_agents_command_failure_soft_falls_back_to_running(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(agents=RuntimeError("claude agents failed with exit 1: boom")),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True)

    adapter.start(_request(tmp_path))
    status = adapter.poll("abc12345", subtask_id="sub-01")

    assert status.state == "running"
    assert "agents query failed" in status.detail


def test_async_poll_done_collects_artifact_refs(monkeypatch, tmp_path):
    (tmp_path / "report.md").write_text("# done", encoding="utf-8")
    monkeypatch.setattr(
        "agos.adapters.workers.claude_code.run_command",
        _runner(agents=json.dumps([{"id": "abc12345", "state": "done"}])),
    )
    adapter = _adapter(tmp_path, claude_async_poll=True, artifact_globs=("*.md",))

    adapter.start(_request(tmp_path))
    status = adapter.poll("abc12345", subtask_id="sub-01")

    assert status.state == "completed"
    assert "report.md" in status.output_refs


# --- transport helper --------------------------------------------------------


def test_load_json_list_rejects_non_list():
    with pytest.raises(RuntimeError, match="non-list JSON"):
        load_json_list('{"id": "abc", "state": "done"}', action="claude agents")


def test_load_json_list_returns_list():
    assert load_json_list('[{"id": "abc"}]', action="claude agents") == [{"id": "abc"}]
