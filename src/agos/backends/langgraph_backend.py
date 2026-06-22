"""Optional LangGraph orchestration backend shim."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from operator import add
from typing import Annotated, Any, Callable, TypedDict

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


NodeDispatch = Callable[[NodeSpec, dict[str, Any]], dict[str, Any]]


def _merge_output_refs(left: dict[str, str] | None, right: dict[str, str] | None) -> dict[str, str]:
    return {**(left or {}), **(right or {})}


class _LangGraphState(TypedDict, total=False):
    visited_nodes: Annotated[list[str], add]
    output_refs: Annotated[dict[str, str], _merge_output_refs]


class LangGraphBackend:
    """Small shim that preserves the orchestration backend seam."""

    name = "langgraph"

    def __init__(
        self,
        *,
        graph_module: LangGraphModule | None = None,
        node_dispatch: NodeDispatch | None = None,
    ) -> None:
        self._native = NativeAsyncBackend()
        self._graph_module = graph_module
        self._node_dispatch = node_dispatch or _default_node_dispatch
        self._compiled_runs: dict[str, LangGraphCompiledRun] = {}
        self._completed_runs: dict[str, dict[str, Any]] = {}

    @staticmethod
    def is_available() -> bool:
        return find_spec("langgraph") is not None

    def start(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle:
        graph_module = self._graph_module or _load_langgraph_module()
        if graph_module is None:
            raise RuntimeError("langgraph is not installed")

        compiled = _compile_run(spec, graph_module, self._node_dispatch)
        self._compiled_runs[spec.run_id] = compiled
        if hasattr(compiled.graph, "invoke"):
            result = compiled.graph.invoke({"visited_nodes": []})
            if isinstance(result, dict):
                self._completed_runs[spec.run_id] = result
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
        completed = self._completed_runs.get(handle.run_id, {})
        output_refs = {
            **_string_dict(snapshot.get("output_refs", {})),
            **_string_dict(completed.get("output_refs", {})),
        }
        combined = {**snapshot, **completed, "backend": self.name}
        if output_refs:
            combined["output_refs"] = output_refs
        return combined

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


def _compile_run(
    spec: OrchestrationRunSpec,
    graph_module: LangGraphModule,
    node_dispatch: NodeDispatch,
) -> LangGraphCompiledRun:
    graph = graph_module.state_graph(_LangGraphState)
    for node in spec.nodes:
        graph.add_node(node.id, _node_action(node, node_dispatch))

    edges = _graph_edges(spec, graph_module)
    for source, target in edges:
        graph.add_edge(list(source) if isinstance(source, tuple) else source, target)

    return LangGraphCompiledRun(
        run_id=spec.run_id,
        node_count=len(spec.nodes),
        graph=graph.compile(),
        edges=edges,
    )


def _node_action(node: NodeSpec, dispatch: NodeDispatch):
    def action(state: dict[str, Any]) -> dict[str, Any]:
        return dispatch(node, state)

    return action


def _default_node_dispatch(node: NodeSpec, state: dict[str, Any]) -> dict[str, Any]:
    del state
    output_ref = node.metadata.get("output_ref")
    output_refs = {node.id: output_ref} if output_ref else {}
    return {"visited_nodes": [node.id], "output_refs": output_refs}


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


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}
