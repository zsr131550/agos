"""Native DAG runtime for orchestration node dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from agos.core.execution import utc_now_iso
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.registry import OrchestrationRegistry
from agos.core.orchestration.runtime import PersistedNodeState, load_node_state, save_node_state
from agos.core.orchestration.scheduler import runnable_nodes


class RuntimePolicy(BaseModel):
    max_parallel: int = Field(default=1, ge=1)
    max_retries: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class RuntimeSnapshot:
    run_id: str
    running_nodes: tuple[str, ...] = ()
    completed_nodes: tuple[str, ...] = ()
    failed_nodes: tuple[str, ...] = ()
    waiting_nodes: tuple[str, ...] = ()
    cancelled_nodes: tuple[str, ...] = ()


class GraphRuntime:
    """Dispatch runnable nodes through registered worker/reviewer/arbiter backends."""

    def __init__(
        self,
        *,
        registry: OrchestrationRegistry,
        state_dir: Path,
        policy: RuntimePolicy | None = None,
    ) -> None:
        self.registry = registry
        self.state_dir = state_dir
        self.policy = policy or RuntimePolicy()

    def tick(self, spec: OrchestrationRunSpec) -> RuntimeSnapshot:
        states = self._load_states(spec)
        scheduler_states = _retryable_failed_as_ready(states, self.policy.max_retries)
        running_count = sum(1 for state in states.values() if state.state == "running")
        capacity = max(0, self.policy.max_parallel - running_count)

        for node_id in runnable_nodes(spec.nodes, scheduler_states)[:capacity]:
            node = _node_by_id(spec, node_id)
            previous = states.get(node_id)
            attempts = (previous.attempts if previous else 0) + 1
            try:
                handle = self._backend_for(node).start(spec, node)
            except Exception as exc:
                state = PersistedNodeState(
                    node_id=node.id,
                    state="failed",
                    attempts=attempts,
                    backend=node.backend,
                    error=str(exc),
                    updated_at=utc_now_iso(),
                )
            else:
                state = PersistedNodeState(
                    node_id=node.id,
                    state="running",
                    attempts=attempts,
                    backend=handle.backend,
                    job_id=handle.job_id,
                    started_at=utc_now_iso(),
                    updated_at=utc_now_iso(),
                )
            states[node_id] = state
            save_node_state(self._state_path(spec.run_id, node_id), state)

        return _snapshot(spec.run_id, spec.nodes, states)

    def cancel(self, spec: OrchestrationRunSpec) -> RuntimeSnapshot:
        states = self._load_states(spec)
        for node in spec.nodes:
            state = states.get(node.id)
            if state is None or state.state != "running":
                continue
            cancelled = PersistedNodeState(
                node_id=node.id,
                state="cancelled",
                attempts=state.attempts,
                backend=state.backend,
                job_id=state.job_id,
                started_at=state.started_at,
                updated_at=utc_now_iso(),
            )
            states[node.id] = cancelled
            save_node_state(self._state_path(spec.run_id, node.id), cancelled)
        return _snapshot(spec.run_id, spec.nodes, states)

    def _backend_for(self, node: NodeSpec):
        if node.kind in {"worker", "worker_submit"}:
            return self.registry.resolve_worker(node.backend)
        if node.kind in {"reviewer", "candidate_review_subgraph"}:
            return self.registry.resolve_reviewer(node.backend)
        if node.kind == "arbiter":
            return self.registry.resolve_arbiter(node.backend)
        return self.registry.resolve_orchestration(node.backend)

    def _load_states(self, spec: OrchestrationRunSpec) -> dict[str, PersistedNodeState]:
        states: dict[str, PersistedNodeState] = {}
        for node in spec.nodes:
            path = self._state_path(spec.run_id, node.id)
            if path.exists():
                states[node.id] = load_node_state(path)
        return states

    def _state_path(self, run_id: str, node_id: str) -> Path:
        return self.state_dir / run_id / f"{node_id}.json"


def _retryable_failed_as_ready(
    states: dict[str, PersistedNodeState],
    max_retries: int,
) -> dict[str, PersistedNodeState]:
    scheduler_states: dict[str, PersistedNodeState] = {}
    for node_id, state in states.items():
        if state.state == "failed" and state.attempts <= max_retries:
            continue
        scheduler_states[node_id] = state
    return scheduler_states


def _snapshot(
    run_id: str,
    nodes: tuple[NodeSpec, ...],
    states: dict[str, PersistedNodeState],
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        run_id=run_id,
        running_nodes=_nodes_in_state(nodes, states, "running"),
        completed_nodes=_nodes_in_state(nodes, states, "completed"),
        failed_nodes=_nodes_in_state(nodes, states, "failed"),
        waiting_nodes=_nodes_in_state(nodes, states, "waiting"),
        cancelled_nodes=_nodes_in_state(nodes, states, "cancelled"),
    )


def _nodes_in_state(
    nodes: tuple[NodeSpec, ...],
    states: dict[str, PersistedNodeState],
    state_name: str,
) -> tuple[str, ...]:
    return tuple(node.id for node in nodes if states.get(node.id, None) is not None and states[node.id].state == state_name)


def _node_by_id(spec: OrchestrationRunSpec, node_id: str) -> NodeSpec:
    for node in spec.nodes:
        if node.id == node_id:
            return node
    raise ValueError(f"unknown node in orchestration run: {node_id}")
