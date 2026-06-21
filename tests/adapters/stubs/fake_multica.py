#!/usr/bin/env python3
"""Stub `multica` CLI for adapter tests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _write_state(filename: str, payload: object) -> None:
    state_dir = os.environ.get("FAKE_MULTICA_STATE")
    if not state_dir:
        return
    path = Path(state_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / filename).write_text(json.dumps(payload), encoding="utf-8")


def _arg_value(argv: list[str], flag: str, default: str = "") -> str:
    for index, arg in enumerate(argv):
        if arg == flag and index + 1 < len(argv):
            return argv[index + 1]
    return default


def main(argv: list[str]) -> int:
    _write_state("last_call.json", argv)

    if argv[:2] == ["issue", "create"]:
        issue = {
            "id": "fake-issue-uuid",
            "identifier": "MUL-1",
            "title": _arg_value(argv, "--title"),
        }
        _write_state("created.json", issue)
        print(json.dumps(issue))
        return 0

    if argv[:2] == ["issue", "run-messages"]:
        since = int(_arg_value(argv, "--since", "0") or "0")
        messages = [
            {"seq": 1, "ts": "2026-06-21T00:00:01Z", "kind": "text", "content": "starting"},
            {"seq": 2, "ts": "2026-06-21T00:00:02Z", "kind": "tool_call", "content": "edit file.py"},
            {"seq": 3, "ts": "2026-06-21T00:00:03Z", "kind": "run_complete", "content": "done"},
        ]
        print(json.dumps({"messages": [message for message in messages if message["seq"] > since]}))
        return 0

    if argv[:2] == ["issue", "runs"]:
        print(json.dumps({"runs": [{"status": "done", "id": "fake-issue-uuid"}]}))
        return 0

    print(json.dumps({"error": "unknown subcommand"}), file=sys.stderr)
    return 5


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
