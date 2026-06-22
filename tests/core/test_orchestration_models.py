from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from agos.core.repo import repo_paths
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


def _node(
    node_id: str,
    *,
    kind: str = "worker",
    backend: str = "local_worker",
    depends_on: list[str] | None = None,
) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        kind=kind,
        backend=backend,
        depends_on=depends_on or [],
    )


def test_orchestration_run_spec_round_trips_with_node_spec():
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[
            NodeSpec(
                id="worker-01",
                kind="worker",
                backend="local_worker",
                depends_on=[],
                metadata={"role": "implementation"},
            ),
            NodeSpec(
                id="reviewer-01",
                kind="reviewer",
                backend="local_reviewer",
                depends_on=["worker-01"],
            ),
        ],
    )

    reloaded = OrchestrationRunSpec.model_validate_json(spec.model_dump_json())

    assert reloaded.run_id == "run-01"
    assert isinstance(reloaded.nodes[0], NodeSpec)
    assert reloaded.nodes[0].metadata == {"role": "implementation"}
    assert reloaded.nodes[1].depends_on == ("worker-01",)


def test_orchestration_run_spec_rejects_duplicate_node_ids():
    with pytest.raises(ValidationError, match="node ids must be unique"):
        OrchestrationRunSpec(
            run_id="run-01",
            task_id="agos-01",
            nodes=[_node("worker-01"), _node("worker-01")],
        )


def test_node_spec_rejects_duplicate_dependencies():
    with pytest.raises(ValidationError, match="depends_on entries must be unique"):
        NodeSpec(
            id="reviewer-01",
            kind="reviewer",
            backend="local_reviewer",
            depends_on=["worker-01", "worker-01"],
        )


def test_orchestration_run_spec_rejects_unknown_dependencies():
    with pytest.raises(ValidationError, match="unknown dependency 'worker-02' for node 'reviewer-01'"):
        OrchestrationRunSpec(
            run_id="run-01",
            task_id="agos-01",
            nodes=[_node("reviewer-01", kind="reviewer", backend="local_reviewer", depends_on=["worker-02"])],
        )


def test_orchestration_run_spec_rejects_dependency_cycles():
    with pytest.raises(ValidationError, match="dependency cycle detected"):
        OrchestrationRunSpec(
            run_id="run-01",
            task_id="agos-01",
            nodes=[
                _node("worker-a", depends_on=["worker-b"]),
                _node("worker-b", depends_on=["worker-a"]),
            ],
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "worker-01 "),
        ("backend", "local_worker "),
    ],
)
def test_node_spec_rejects_whitespace_padded_identifiers(field: str, value: str):
    kwargs = {
        "id": "worker-01",
        "kind": "worker",
        "backend": "local_worker",
    }
    kwargs[field] = value

    with pytest.raises(ValidationError, match="must not contain leading or trailing whitespace"):
        NodeSpec(**kwargs)


def test_orchestration_run_spec_rejects_whitespace_padded_dependency_ids():
    with pytest.raises(ValidationError, match="depends_on entries must not contain leading or trailing whitespace"):
        NodeSpec(
            id="reviewer-01",
            kind="reviewer",
            backend="local_reviewer",
            depends_on=["worker-01 "],
        )


def test_node_spec_is_immutable_after_construction():
    node = NodeSpec(
        id="worker-01",
        kind="worker",
        backend="local_worker",
        depends_on=["reviewer-01"],
        metadata={"role": "implementation"},
    )

    assert isinstance(node.depends_on, tuple)
    assert isinstance(node.metadata, MappingProxyType)

    with pytest.raises(AttributeError):
        node.depends_on.append("arbiter-01")

    with pytest.raises(TypeError):
        node.metadata["role"] = "mutated"


def test_orchestration_run_spec_is_immutable_after_construction():
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[_node("worker-01"), _node("reviewer-01", kind="reviewer", backend="local_reviewer", depends_on=["worker-01"])],
        metadata={"mode": "serial"},
    )

    assert isinstance(spec.nodes, tuple)
    assert isinstance(spec.metadata, MappingProxyType)

    with pytest.raises(AttributeError):
        spec.nodes.append(_node("arbiter-01", kind="arbiter", backend="local_arbiter"))

    with pytest.raises(TypeError):
        spec.metadata["mode"] = "parallel"


def test_repo_paths_include_orchestration_layout(tmp_repo: Path):
    paths = repo_paths(tmp_repo)

    assert paths.orchestration_dir == tmp_repo / ".agos" / "tasks" / "current" / "orchestration"
    assert paths.orchestration_runs == paths.orchestration_dir / "runs"
    assert paths.orchestration_node_states == paths.orchestration_dir / "node_states"
    assert paths.orchestration_logs == paths.evidence / "orchestration"
