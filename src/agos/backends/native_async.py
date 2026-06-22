"""Minimal native orchestration backend skeleton."""
from __future__ import annotations

from agos.core.orchestration.models import (
    NodeSpec,
    OrchestrationRunSpec,
    OrchestratorRunHandle,
    OrchestratorRunStatus,
)
from agos.core.orchestration.scheduler import runnable_nodes


BackendRunHandle = OrchestratorRunHandle
BackendRunStatus = OrchestratorRunStatus


class NativeAsyncBackend:
    """Semantic reference backend for simple orchestration flows."""

    name = "native_async"

    def __init__(self) -> None:
        self._runs: dict[str, OrchestrationRunSpec] = {}
        self._cancelled: set[str] = set()

    def start(self, spec: OrchestrationRunSpec) -> BackendRunHandle:
        self._runs[spec.run_id] = spec
        self._cancelled.discard(spec.run_id)
        return BackendRunHandle(backend=self.name, run_id=spec.run_id)

    def run(self, spec: OrchestrationRunSpec) -> BackendRunHandle:
        """Compatibility entrypoint for the Task 1 orchestration registry seam."""

        return self.start(spec)

    def poll(self, handle: BackendRunHandle) -> BackendRunStatus:
        spec = self._require_run(handle)
        if handle.run_id in self._cancelled:
            return BackendRunStatus(
                backend=self.name,
                run_id=handle.run_id,
                state="cancelled",
            )
        ready_nodes = runnable_nodes(spec.nodes, {})
        waiting_nodes = tuple(
            node_id
            for node_id in ready_nodes
            if _is_waiting_manual_node(_node_by_id(spec, node_id))
        )
        if waiting_nodes:
            return BackendRunStatus(
                backend=self.name,
                run_id=handle.run_id,
                state="waiting",
                waiting_nodes=waiting_nodes,
            )

        if ready_nodes:
            return BackendRunStatus(backend=self.name, run_id=handle.run_id, state="running")

        return BackendRunStatus(backend=self.name, run_id=handle.run_id, state="completed")

    def cancel(self, handle: BackendRunHandle) -> BackendRunStatus:
        self._require_run(handle)
        self._cancelled.add(handle.run_id)
        return BackendRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            state="cancelled",
        )

    def collect(self, handle: BackendRunHandle) -> dict[str, object]:
        status = self.poll(handle)
        return {
            "run_id": status.run_id,
            "backend": self.name,
            "state": status.state,
            "waiting_nodes": list(status.waiting_nodes),
            "completed_nodes": list(status.completed_nodes),
            "failed_nodes": list(status.failed_nodes),
            "output_refs": _output_refs_for_nodes(
                self._require_run(handle),
                status.waiting_nodes + status.completed_nodes,
            ),
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


def _output_refs_for_nodes(
    spec: OrchestrationRunSpec,
    node_ids: tuple[str, ...],
) -> dict[str, str]:
    output_refs: dict[str, str] = {}
    for node_id in node_ids:
        output_ref = _node_by_id(spec, node_id).metadata.get("output_ref")
        if output_ref:
            output_refs[node_id] = output_ref
    return output_refs
