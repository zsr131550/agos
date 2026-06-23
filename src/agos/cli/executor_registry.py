"""Build configured executor adapters at the CLI boundary."""
from __future__ import annotations

from agos.adapters.local_cli_executor import CodexCliExecutorAdapter, ClaudeCodeExecutorAdapter
from agos.adapters.multica import MulticaAdapter
from agos.core.config import load_config
from agos.core.repo import AgosPaths


def configured_executor_adapter(paths: AgosPaths):
    config = load_config(paths.root)
    executor = config.executor
    if executor.name == "multica":
        return MulticaAdapter(multica_bin=executor.command or "multica")
    if executor.name == "codex_cli":
        return CodexCliExecutorAdapter(
            command=executor.command or "codex",
            evidence_dir=paths.evidence,
            cwd=paths.root,
        )
    if executor.name == "claude_code":
        return ClaudeCodeExecutorAdapter(
            command=executor.command or "claude",
            evidence_dir=paths.evidence,
            cwd=paths.root,
        )
    raise ValueError(f"Unsupported executor '{executor.name}'")
