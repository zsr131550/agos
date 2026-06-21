from __future__ import annotations

from pathlib import Path

from tests.integration.test_round_trip import (
    _extract_messages,
    _integration_agent,
    _integration_title,
)


def test_integration_agent_defaults_to_lambda(monkeypatch):
    monkeypatch.delenv("AGOS_INTEGRATION_AGENT", raising=False)

    assert _integration_agent() == "Lambda"


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
