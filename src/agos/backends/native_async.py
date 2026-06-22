"""Minimal native orchestration backend skeleton."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.scheduler import runnable_nodes


@dataclass(frozen=True)
class BackendRunHandle:
    """Handle returned when a native orchestration run starts."""

    backend: str
    run_id: str


@dataclass(frozen=True)
class BackendRunStatus:
    """High-level status snapshot for a native orchestration run."""

    run_id: str
    state: Literal["running", "waiting", "completed", "failed"]
    waiting_nodes: tuple[str, ...] = ()
    completed_nodes: tuple[str, ...] = ()
    failed_nodes: tuple[str, ...] = ()


class NativeAsyncBackend:
    """Semantic reference backend for simple orchestration flows."""

    name = "native_async"

    def start(self, spec: OrchestrationRunSpec) -> BackendRunHandle:
        return BackendRunHandle(backend=self.name, run_id=spec.run_id)

    def poll(self, handle: BackendRunHandle, spec: OrchestrationRunSpec) -> BackendRunStatus:
        waiting_nodes = tuple(
            node.id for node in spec.nodes if _is_waiting_manual_node(node)
        )
        if waiting_nodes:
            return BackendRunStatus(
                run_id=handle.run_id,
                state="waiting",
                waiting_nodes=waiting_nodes,
            )

        ready_nodes = runnable_nodes(spec.nodes, {})
        if ready_nodes:
            return BackendRunStatus(run_id=handle.run_id, state="running")

        return BackendRunStatus(run_id=handle.run_id, state="completed")

    def collect(self, handle: BackendRunHandle, spec: OrchestrationRunSpec) -> dict[str, object]:
        status = self.poll(handle, spec)
        return {
            "run_id": status.run_id,
            "state": status.state,
            "waiting_nodes": list(status.waiting_nodes),
            "completed_nodes": list(status.completed_nodes),
            "failed_nodes": list(status.failed_nodes),
        }


def _is_waiting_manual_node(node: NodeSpec) -> bool:
    return node.kind == "reviewer" and node.backend == "manual"
