"""Register configured execution worker adapters at the CLI boundary."""
from __future__ import annotations

from agos.adapters.workers import (
    CodexWorkerAdapter,
    LocalWorktreeWorkerAdapter,
    MulticaWorkerAdapter,
    OpenHandsWorkerAdapter,
)
from agos.core.config import WorkerConfig, load_config
from agos.core.execution_service import ExecutionService


def register_configured_worker_adapters(service: ExecutionService) -> None:
    """Install worker adapters declared in `.agos/agos.yaml` onto a service."""

    config = load_config(service.paths.root)
    workers = config.workers or {"local_worktree": WorkerConfig(type="local_worktree")}
    for name, worker in workers.items():
        if worker.type == "local_worktree":
            service.register_worker_adapter(LocalWorktreeWorkerAdapter(service.workspace_manager))
        elif worker.type == "codex_cli":
            service.register_worker_adapter(
                CodexWorkerAdapter(name=name, command=worker.command or "codex")
            )
        elif worker.type == "multica":
            service.register_worker_adapter(
                MulticaWorkerAdapter(
                    name=name,
                    multica_bin=worker.command or "multica",
                    agent=worker.agent or config.executor.agent,
                )
            )
        elif worker.type == "openhands":
            if worker.endpoint is None:
                raise ValueError(f"worker {name!r} requires endpoint")
            service.register_worker_adapter(
                OpenHandsWorkerAdapter(
                    name=name,
                    endpoint=worker.endpoint,
                    token=worker.token,
                )
            )
        else:
            raise ValueError(f"unsupported worker adapter type: {worker.type}")
