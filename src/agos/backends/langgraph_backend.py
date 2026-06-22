"""Optional LangGraph orchestration backend shim."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any, TypedDict

from agos.backends.native_async import NativeAsyncBackend
from agos.core.orchestration.models import (
    NodeSpec,
    OrchestrationRunSpec,
    OrchestratorRunHandle,
    OrchestratorRunStatus,
)


@dataclass(frozen=True)
class LangGraphModule:
    """Small import boundary for optional LangGraph symbols."""

    state_graph: type
    start: str
    end: str


@dataclass(frozen=True)
class LangGraphCompiledRun:
    """Backend-local compiled LangGraph run metadata."""

    run_id: str
    node_count: int
    graph: object
    edges: tuple[tuple[str | tuple[str, ...], str], ...]


class _LangGraphState(TypedDict, total=False):
    visited_nodes: list[str]


class LangGraphBackend:
    """Small shim that preserves the orchestration backend seam."""

    name = "langgraph"

    def __init__(self, *, graph_module: LangGraphModule | None = None) -> None:
        self._native = NativeAsyncBackend()
        self._graph_module = graph_module
        self._compiled_runs: dict[str, LangGraphCompiledRun] = {}

    @staticmethod
    def is_available() -> bool:
        return find_spec("langgraph") is not None

    def start(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle:
        graph_module = self._graph_module or _load_langgraph_module()
        if graph_module is None:
            raise RuntimeError("langgraph is not installed")

        self._compiled_runs[spec.run_id] = _compile_run(spec, graph_module)
        self._native.start(spec.model_copy(update={"backend": self.name}))
        return OrchestratorRunHandle(backend=self.name, run_id=spec.run_id)

    def run(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle:
        return self.start(spec)

    def poll(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus:
        status = self._native.poll(
            OrchestratorRunHandle(backend=self._native.name, run_id=handle.run_id)
        )
        return OrchestratorRunStatus(
            backend=self.name,
            run_id=status.run_id,
            state=status.state,
            waiting_nodes=status.waiting_nodes,
            completed_nodes=status.completed_nodes,
            failed_nodes=status.failed_nodes,
            output_refs=status.output_refs,
        )

    def cancel(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus:
        status = self._native.cancel(
            OrchestratorRunHandle(backend=self._native.name, run_id=handle.run_id)
        )
        return OrchestratorRunStatus(
            backend=self.name,
            run_id=status.run_id,
            state=status.state,
            waiting_nodes=status.waiting_nodes,
            completed_nodes=status.completed_nodes,
            failed_nodes=status.failed_nodes,
            output_refs=status.output_refs,
        )

    def collect(self, handle: OrchestratorRunHandle) -> dict[str, object]:
        if handle.run_id not in self._compiled_runs:
            raise ValueError(f"unknown orchestration run handle: {handle.run_id}")

        snapshot = self._native.collect(
            OrchestratorRunHandle(backend=self._native.name, run_id=handle.run_id)
        )
        return {**snapshot, "backend": self.name}

    def compiled_run(self, handle: OrchestratorRunHandle) -> LangGraphCompiledRun:
        try:
            return self._compiled_runs[handle.run_id]
        except KeyError as exc:
            raise ValueError(f"unknown orchestration run handle: {handle.run_id}") from exc


def _load_langgraph_module() -> LangGraphModule | None:
    if not LangGraphBackend.is_available():
        return None

    from langgraph.graph import END, START, StateGraph

    return LangGraphModule(state_graph=StateGraph, start=START, end=END)


def _compile_run(spec: OrchestrationRunSpec, graph_module: LangGraphModule) -> LangGraphCompiledRun:
    graph = graph_module.state_graph(_LangGraphState)
    for node in spec.nodes:
        graph.add_node(node.id, _node_action(node))

    edges = _graph_edges(spec, graph_module)
    for source, target in edges:
        graph.add_edge(list(source) if isinstance(source, tuple) else source, target)

    return LangGraphCompiledRun(
        run_id=spec.run_id,
        node_count=len(spec.nodes),
        graph=graph.compile(),
        edges=edges,
    )


def _node_action(node: NodeSpec):
    def action(state: dict[str, Any]) -> dict[str, Any]:
        visited = list(state.get("visited_nodes", []))
        return {**state, "visited_nodes": [*visited, node.id]}

    return action


def _graph_edges(
    spec: OrchestrationRunSpec,
    graph_module: LangGraphModule,
) -> tuple[tuple[str | tuple[str, ...], str], ...]:
    node_ids = {node.id for node in spec.nodes}
    dependents = {node_id: set[str]() for node_id in node_ids}
    dependency_edges: list[tuple[str | tuple[str, ...], str]] = []

    for node in spec.nodes:
        if len(node.depends_on) == 1:
            dependency_edges.append((node.depends_on[0], node.id))
        elif len(node.depends_on) > 1:
            dependency_edges.append((tuple(node.depends_on), node.id))
        for dependency in node.depends_on:
            dependents[dependency].add(node.id)

    entry_nodes = spec.entry_nodes or tuple(
        node.id
        for node in spec.nodes
        if not node.depends_on
    )
    edges = [(graph_module.start, node_id) for node_id in entry_nodes] + dependency_edges
    edges.extend(
        (node.id, graph_module.end)
        for node in spec.nodes
        if not dependents[node.id]
    )
    return tuple(edges)
