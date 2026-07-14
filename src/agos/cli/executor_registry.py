"""Build configured executor adapters at the CLI boundary."""
from __future__ import annotations

from agos.adapters.local_cli_executor import CodexCliExecutorAdapter, ClaudeCodeExecutorAdapter
from agos.adapters.multica import MulticaAdapter
from agos.core.config import load_config
from agos.core.repo import AgosPaths


def configured_executor_adapter(paths: AgosPaths):
    config = load_config(paths.root)
    executor = config.executor
    return executor_adapter_for(
        paths,
        executor.name,
        command=executor.command,
        dangerously_bypass_permissions=executor.dangerously_bypass_permissions,
    )


def executor_adapter_for(
    paths: AgosPaths,
    adapter_name: str,
    *,
    command: str | None = None,
    dangerously_bypass_permissions: bool = False,
):
    """Build an executor adapter by name without mutating agos.yaml."""

    if adapter_name == "multica":
        return MulticaAdapter(multica_bin=command or "multica")
    if adapter_name == "codex_cli":
        return CodexCliExecutorAdapter(
            command=command or "codex",
            evidence_dir=paths.evidence,
            cwd=paths.root,
            dangerously_bypass_permissions=dangerously_bypass_permissions,
        )
    if adapter_name == "claude_code":
        return ClaudeCodeExecutorAdapter(
            command=command or "claude",
            evidence_dir=paths.evidence,
            cwd=paths.root,
            dangerously_bypass_permissions=dangerously_bypass_permissions,
        )
    raise ValueError(f"Unsupported executor '{adapter_name}'")
