from __future__ import annotations

import json

import pytest

from agos.backends.external_backend import ExternalBackend
from agos.backends.langgraph_backend import LangGraphBackend, LangGraphModule
from agos.backends.native_async import NativeAsyncBackend
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.registry import OrchestrationRegistry


def _node(
    node_id: str,
    *,
    kind: str = "worker",
    backend: str = "native_async",
    depends_on: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        kind=kind,
        backend=backend,
        depends_on=depends_on or [],
        metadata=metadata or {},
    )


def _manual_review_spec(*, backend: str) -> OrchestrationRunSpec:
    return OrchestrationRunSpec(
        run_id=f"{backend}-run-01",
        task_id="agos-01",
        backend=backend,
        nodes=[
            _node(
                "manual-review",
                kind="wait_for_manual_input",
                backend=backend,
                metadata={"output_ref": "reviews/review-01/raw/manual.json"},
            )
        ],
    )


def _assert_waiting_snapshot(snapshot: dict[str, object], *, backend: str) -> None:
    expected = {
        "run_id": f"{backend}-run-01",
        "backend": backend,
        "state": "waiting",
        "waiting_nodes": ["manual-review"],
        "completed_nodes": [],
        "failed_nodes": [],
        "output_refs": {"manual-review": "reviews/review-01/raw/manual.json"},
    }
    for key, value in expected.items():
        assert snapshot[key] == value


def test_native_backend_collect_matches_parity_contract():
    backend = NativeAsyncBackend()
    handle = backend.start(_manual_review_spec(backend=backend.name))

    _assert_waiting_snapshot(backend.collect(handle), backend=backend.name)


@pytest.mark.skipif(not LangGraphBackend.is_available(), reason="langgraph is not installed")
def test_langgraph_backend_collect_matches_parity_contract():
    backend = LangGraphBackend()
    handle = backend.run(_manual_review_spec(backend=backend.name))

    _assert_waiting_snapshot(backend.collect(handle), backend=backend.name)


def test_langgraph_backend_errors_when_optional_dependency_is_missing(monkeypatch):
    monkeypatch.setattr(LangGraphBackend, "is_available", staticmethod(lambda: False))
    backend = LangGraphBackend()

    with pytest.raises(RuntimeError, match="langgraph is not installed"):
        backend.run(_manual_review_spec(backend=backend.name))


def test_external_backend_run_returns_normalized_submission_handle():
    backend = ExternalBackend()
    spec = _manual_review_spec(backend=backend.name)

    handle = backend.run(spec)

    assert handle.backend == "external"
    assert handle.run_id == "external-run-01"
    assert handle.job_id == "external-run-01"
    assert handle.payload["run_id"] == "external-run-01"
    assert handle.payload["backend"] == "external"
    assert handle.payload["state"] == "submitted"
    assert handle.payload["waiting_nodes"] == ["manual-review"]
    assert handle.payload["completed_nodes"] == []
    assert handle.payload["failed_nodes"] == []
    assert handle.payload["output_refs"] == {"manual-review": "reviews/review-01/raw/manual.json"}
    assert handle.payload["spec"] == json.loads(spec.model_dump_json())


def test_external_backend_respects_dependency_readiness_for_waiting_nodes():
    backend = ExternalBackend()
    spec = OrchestrationRunSpec(
        run_id="external-run-01",
        task_id="agos-01",
        backend=backend.name,
        nodes=[
            _node("worker-01", backend=backend.name),
            _node(
                "manual-review",
                kind="wait_for_manual_input",
                backend=backend.name,
                depends_on=["worker-01"],
                metadata={"output_ref": "reviews/review-01/raw/manual.json"},
            ),
        ],
    )

    handle = backend.run(spec)

    assert handle.payload["waiting_nodes"] == []
    assert handle.payload["output_refs"] == {}
    assert backend.collect(handle)["waiting_nodes"] == []
    assert backend.collect(handle)["output_refs"] == {}


def test_external_backend_collect_returns_defensive_payload_copy():
    backend = ExternalBackend()
    handle = backend.run(_manual_review_spec(backend=backend.name))
    handle.payload["waiting_nodes"] = ["mutated"]
    snapshot = backend.collect(handle)
    snapshot["waiting_nodes"] = ["mutated-again"]

    assert backend.collect(handle)["waiting_nodes"] == ["manual-review"]


def test_external_backend_registers_through_orchestration_registry():
    registry = OrchestrationRegistry()
    backend = ExternalBackend()

    registry.register_orchestration(backend)
    handle = registry.resolve_orchestration("external").run(_manual_review_spec(backend=backend.name))

    assert handle.backend == "external"


class _FakeCompiledGraph:
    def __init__(self) -> None:
        self.invocations: list[dict[str, object]] = []

    def invoke(self, state: dict[str, object]) -> dict[str, object]:
        self.invocations.append(state)
        return {"visited_nodes": ["worker-01"]}


class _FakeStateGraph:
    def __init__(self, state_schema: object) -> None:
        self.state_schema = state_schema
        self.nodes: list[str] = []
        self.edges: list[tuple[object, str]] = []

    def add_node(self, name: str, action: object) -> None:
        self.nodes.append(name)

    def add_edge(self, source: object, target: str) -> None:
        self.edges.append((source, target))

    def compile(self) -> _FakeCompiledGraph:
        return _FakeCompiledGraph()


def test_langgraph_backend_compiles_orchestration_spec_with_injected_graph_module():
    fake_module = LangGraphModule(
        state_graph=_FakeStateGraph,
        start="__start__",
        end="__end__",
    )
    backend = LangGraphBackend(graph_module=fake_module)
    spec = OrchestrationRunSpec(
        run_id="langgraph-run-01",
        task_id="agos-01",
        backend=backend.name,
        nodes=[
            _node("worker-01", backend=backend.name),
            _node(
                "manual-review",
                kind="wait_for_manual_input",
                backend=backend.name,
                depends_on=["worker-01"],
                metadata={"output_ref": "reviews/review-01/raw/manual.json"},
            ),
        ],
    )

    handle = backend.run(spec)

    compiled = backend.compiled_run(handle)
    assert isinstance(compiled.graph, _FakeCompiledGraph)
    assert compiled.node_count == 2
    assert compiled.edges == (
        ("__start__", "worker-01"),
        ("worker-01", "manual-review"),
        ("manual-review", "__end__"),
    )


def test_langgraph_backend_invokes_compiled_graph_with_injected_graph_module():
    backend = LangGraphBackend(
        graph_module=LangGraphModule(
            state_graph=_FakeStateGraph,
            start="__start__",
            end="__end__",
        )
    )
    spec = OrchestrationRunSpec(
        run_id="langgraph-run-invoke",
        task_id="agos-01",
        backend=backend.name,
        nodes=[_node("worker-01", backend=backend.name)],
    )

    handle = backend.run(spec)
    compiled = backend.compiled_run(handle)
    snapshot = backend.collect(handle)

    assert compiled.graph.invocations == [{"visited_nodes": []}]
    assert snapshot["visited_nodes"] == ["worker-01"]


def test_langgraph_backend_preserves_join_dependencies_in_compiled_graph():
    backend = LangGraphBackend(
        graph_module=LangGraphModule(
            state_graph=_FakeStateGraph,
            start="__start__",
            end="__end__",
        )
    )
    spec = OrchestrationRunSpec(
        run_id="langgraph-run-join",
        task_id="agos-01",
        backend=backend.name,
        nodes=[
            _node("worker-a", backend=backend.name),
            _node("worker-b", backend=backend.name),
            _node("merge-review", backend=backend.name, depends_on=["worker-a", "worker-b"]),
        ],
    )

    handle = backend.run(spec)

    assert backend.compiled_run(handle).edges == (
        ("__start__", "worker-a"),
        ("__start__", "worker-b"),
        (("worker-a", "worker-b"), "merge-review"),
        ("merge-review", "__end__"),
    )


def test_langgraph_backend_registers_through_orchestration_registry_with_injected_graph_module():
    registry = OrchestrationRegistry()
    backend = LangGraphBackend(
        graph_module=LangGraphModule(
            state_graph=_FakeStateGraph,
            start="__start__",
            end="__end__",
        )
    )

    registry.register_orchestration(backend)
    handle = registry.resolve_orchestration("langgraph").run(_manual_review_spec(backend=backend.name))

    assert handle.backend == "langgraph"
