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

    def __init__(self) -> None:
        self._runs: dict[str, OrchestrationRunSpec] = {}

    def start(self, spec: OrchestrationRunSpec) -> BackendRunHandle:
        self._runs[spec.run_id] = spec
        return BackendRunHandle(backend=self.name, run_id=spec.run_id)

    def run(self, spec: OrchestrationRunSpec) -> BackendRunHandle:
        """Compatibility entrypoint for the Task 1 orchestration registry seam."""

        return self.start(spec)

    def poll(self, handle: BackendRunHandle) -> BackendRunStatus:
        spec = self._require_run(handle)
        ready_nodes = runnable_nodes(spec.nodes, {})
        waiting_nodes = tuple(
            node_id
            for node_id in ready_nodes
            if _is_waiting_manual_node(_node_by_id(spec, node_id))
        )
        if waiting_nodes:
            return BackendRunStatus(
                run_id=handle.run_id,
                state="waiting",
                waiting_nodes=waiting_nodes,
            )

        if ready_nodes:
            return BackendRunStatus(run_id=handle.run_id, state="running")

        return BackendRunStatus(run_id=handle.run_id, state="completed")

    def collect(self, handle: BackendRunHandle) -> dict[str, object]:
        status = self.poll(handle)
        return {
            "run_id": status.run_id,
            "state": status.state,
            "waiting_nodes": list(status.waiting_nodes),
            "completed_nodes": list(status.completed_nodes),
            "failed_nodes": list(status.failed_nodes),
        }

    def _require_run(self, handle: BackendRunHandle) -> OrchestrationRunSpec:
        try:
            return self._runs[handle.run_id]
        except KeyError as exc:
            raise ValueError(f"unknown orchestration run handle: {handle.run_id}") from exc


def _is_waiting_manual_node(node: NodeSpec) -> bool:
    return node.kind == "wait_for_manual_input"


def _node_by_id(spec: OrchestrationRunSpec, node_id: str) -> NodeSpec:
    for node in spec.nodes:
        if node.id == node_id:
            return node
    raise ValueError(f"unknown node in orchestration run: {node_id}")
