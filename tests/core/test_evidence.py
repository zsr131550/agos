"""Tests for the evidence store (two trees: agent_diff vs repo_anchor)."""
from __future__ import annotations

import json
from pathlib import Path

from agos.core.evidence import EvidenceStore


def test_write_run(tmp_path: Path):
    store = EvidenceStore(tmp_path / "ev")
    path = store.write_run("run-1", {"adapter": "multica", "issue_id": "MUL-1"})
    assert path.name == "run-1.json"
    assert path.parent.name == "runs"
    assert json.loads(path.read_text(encoding="utf-8"))["issue_id"] == "MUL-1"


def test_append_message_appends_lines(tmp_path: Path):
    store = EvidenceStore(tmp_path / "ev")
    store.append_message("run-1", {"seq": 1, "kind": "text", "content": "a"})
    path = store.append_message("run-1", {"seq": 2, "kind": "text", "content": "b"})
    assert path.name == "run-1.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["seq"] == 2


def test_write_gate_log(tmp_path: Path):
    store = EvidenceStore(tmp_path / "ev")
    path = store.write_gate_log("tests_pass", "20260621T000000Z", "ok\n", "", 0)
    assert path.parent.name == "gates"
    assert "tests_pass" in path.name
    assert "ok" in path.read_text(encoding="utf-8")


def test_write_agent_diff_distinct_from_repo_anchor(tmp_path: Path):
    """agent_diff holds executor-reported diff; repo_anchor holds governed-repo state."""

    store = EvidenceStore(tmp_path / "ev")
    agent_diff = store.write_agent_diff("run-1", "--- a\n+++ b\n")
    repo_anchor = store.write_repo_anchor("20260621T000000Z", "deadbeef", " M file.txt\n")
    assert agent_diff.parent.name == "agent_diff"
    assert repo_anchor.parent.name == "repo_anchor"
    anchor = json.loads(repo_anchor.read_text(encoding="utf-8"))
    assert anchor["head"] == "deadbeef"
    assert anchor["status_porcelain"] == " M file.txt\n"
    assert "claim" not in anchor or anchor.get("claim") is None
