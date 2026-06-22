from __future__ import annotations

from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


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
    assert reloaded.nodes[1].depends_on == ["worker-01"]
