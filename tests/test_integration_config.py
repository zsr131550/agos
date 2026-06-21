from __future__ import annotations

from pathlib import Path

from tests.integration.test_round_trip import (
    _extract_messages,
    _integration_agent,
    _multica_ready,
    _integration_title,
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
