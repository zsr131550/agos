from __future__ import annotations

import yaml

from agos.cli.executor_registry import configured_executor_adapter
from agos.core.repo import repo_paths


def test_configured_executor_adapter_supports_codex_and_claude(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)

    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "codex_cli", "agent": "codex", "command": "codex.cmd"},
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    codex = configured_executor_adapter(paths)
    assert codex.name == "codex_cli"
    assert codex.command == "codex.cmd"

    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "claude_code", "agent": "claude", "command": "claude.cmd"},
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    claude = configured_executor_adapter(paths)
    assert claude.name == "claude_code"
    assert claude.command == "claude.cmd"
