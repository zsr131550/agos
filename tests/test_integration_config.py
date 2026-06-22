from __future__ import annotations

import json
from pathlib import Path

import pytest

import tests.integration.test_round_trip as round_trip
from tests.integration.test_round_trip import (
    _extract_messages,
    _extract_runs,
    _integration_agent,
    _multica_ready,
    _integration_title,
    _wait_for_terminal_run,
)


def test_integration_agent_skips_when_env_missing(monkeypatch):
    import pytest

    monkeypatch.delenv("AGOS_INTEGRATION_AGENT", raising=False)

    with pytest.raises(pytest.skip.Exception):
        _integration_agent()


def test_integration_agent_uses_env_override(monkeypatch):
    monkeypatch.setenv("AGOS_INTEGRATION_AGENT", "codex-gpt-5.4 xhigh")

    assert _integration_agent() == "codex-gpt-5.4 xhigh"


def test_integration_title_uses_unique_repo_context():
    title = _integration_title(Path(r"E:\repo\.basetemp_integration\test_round_trip0\repo"))

    assert title.startswith("smoke-")
    assert title.endswith("test_round_trip0")


def test_extract_messages_accepts_list_payload():
    payload = [{"seq": 1, "kind": "text", "content": "hi"}]

    assert _extract_messages(payload) == payload


def test_extract_runs_accepts_list_payload():
    payload = [{"id": "run-01", "status": "completed"}]

    assert _extract_runs(payload) == payload


def test_wait_for_terminal_run_returns_completed_run(monkeypatch, tmp_path):
    class FakeProc:
        returncode = 0
        stdout = json.dumps(
            [
                {
                    "id": "run-01",
                    "status": "completed",
                    "started_at": "2026-06-22T00:00:00Z",
                    "completed_at": "2026-06-22T00:00:01Z",
                }
            ]
        )
        stderr = ""

    monkeypatch.setattr(round_trip, "_run", lambda *args, **kwargs: FakeProc())

    run = _wait_for_terminal_run(
        run_id="run-01",
        issue_id="AGO-1",
        cwd=tmp_path,
        timeout_seconds=1,
    )

    assert run["status"] == "completed"


def test_wait_for_terminal_run_timeout_reports_run_context(monkeypatch, tmp_path):
    class FakeProc:
        returncode = 0
        stdout = json.dumps(
            [
                {
                    "id": "run-01",
                    "status": "dispatched",
                    "started_at": None,
                    "completed_at": None,
                }
            ]
        )
        stderr = ""

    now = iter([0.0, 0.5, 2.0])
    monkeypatch.setattr(round_trip, "_run", lambda *args, **kwargs: FakeProc())
    monkeypatch.setattr(round_trip.time, "monotonic", lambda: next(now))
    monkeypatch.setattr(round_trip.time, "sleep", lambda _seconds: None)

    with pytest.raises(pytest.fail.Exception) as exc:
        _wait_for_terminal_run(
            run_id="run-01",
            issue_id="AGO-1",
            cwd=tmp_path,
            timeout_seconds=1,
        )

    message = str(exc.value)
    assert "timed out waiting for Multica run to finish" in message
    assert "issue_id='AGO-1'" in message
    assert "run_id='run-01'" in message
    assert "status='dispatched'" in message
    assert "started_at=None" in message


def test_multica_ready_rejects_stopped_daemon(monkeypatch):
    class FakeProc:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    outputs = iter(
        [
            FakeProc("Daemon: stopped\n"),
            FakeProc("[]\n"),
        ]
    )

    monkeypatch.setattr("tests.integration.test_round_trip.shutil.which", lambda _name: r"C:\tools\multica.cmd")
    monkeypatch.setattr(
        "tests.integration.test_round_trip.subprocess.run",
        lambda *args, **kwargs: next(outputs),
    )

    assert _multica_ready() is False
