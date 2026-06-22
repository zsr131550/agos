"""External orchestration backend shim."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from agos.core.orchestration.models import (
    NodeSpec,
    OrchestrationRunSpec,
    OrchestratorRunStatus,
)
from agos.core.orchestration.scheduler import runnable_nodes


@dataclass(frozen=True)
class ExternalRunHandle:
    """Handle returned after a spec is submitted to an external orchestrator."""

    backend: str
    run_id: str
    job_id: str
    payload: dict[str, object]


class ExternalBackend:
    """Serialize orchestration specs for an external runtime."""

    name = "external"

    def __init__(self) -> None:
        self._submitted: dict[str, dict[str, object]] = {}

    def start(self, spec: OrchestrationRunSpec) -> ExternalRunHandle:
        payload = self._submission_payload(spec)
        self._submitted[spec.run_id] = deepcopy(payload)
        return ExternalRunHandle(
            backend=self.name,
            run_id=spec.run_id,
            job_id=spec.run_id,
            payload=deepcopy(payload),
        )

    def run(self, spec: OrchestrationRunSpec) -> ExternalRunHandle:
        return self.start(spec)

    def poll(self, handle: ExternalRunHandle) -> OrchestratorRunStatus:
        snapshot = self.collect(handle)
        return OrchestratorRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            state=_state(snapshot.get("state")),
            waiting_nodes=tuple(str(node) for node in snapshot.get("waiting_nodes", [])),
            completed_nodes=tuple(str(node) for node in snapshot.get("completed_nodes", [])),
            failed_nodes=tuple(str(node) for node in snapshot.get("failed_nodes", [])),
            output_refs={
                str(key): str(value)
                for key, value in dict(snapshot.get("output_refs", {})).items()
            },
        )

    def cancel(self, handle: ExternalRunHandle) -> OrchestratorRunStatus:
        payload = self.collect(handle)
        payload["state"] = "cancelled"
        self._submitted[handle.run_id] = deepcopy(payload)
        return OrchestratorRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            state="cancelled",
        )

    def collect(self, handle: ExternalRunHandle) -> dict[str, object]:
        try:
            return deepcopy(self._submitted[handle.run_id])
        except KeyError as exc:
            raise ValueError(f"unknown orchestration run handle: {handle.run_id}") from exc

    def _submission_payload(self, spec: OrchestrationRunSpec) -> dict[str, object]:
        ready_nodes = runnable_nodes(spec.nodes, {})
        waiting_nodes = [
            node_id
            for node_id in ready_nodes
            if _node_by_id(spec, node_id).kind == "wait_for_manual_input"
        ]
        output_refs = _output_refs_for_nodes(spec, waiting_nodes)
        return {
            "run_id": spec.run_id,
            "backend": self.name,
            "state": "submitted",
            "waiting_nodes": waiting_nodes,
            "completed_nodes": [],
            "failed_nodes": [],
            "output_refs": output_refs,
            "spec": spec.model_dump(mode="json"),
        }


def _node_by_id(spec: OrchestrationRunSpec, node_id: str) -> NodeSpec:
    for node in spec.nodes:
        if node.id == node_id:
            return node
    raise ValueError(f"unknown node in orchestration run: {node_id}")


def _output_refs_for_nodes(
    spec: OrchestrationRunSpec,
    node_ids: list[str],
) -> dict[str, str]:
    output_refs: dict[str, str] = {}
    for node_id in node_ids:
        output_ref = _node_by_id(spec, node_id).metadata.get("output_ref")
        if output_ref:
            output_refs[node_id] = output_ref
    return output_refs


def _state(value: object) -> str:
    state = str(value or "queued")
    return "queued" if state == "submitted" else state
