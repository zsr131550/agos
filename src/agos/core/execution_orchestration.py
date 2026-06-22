"""Execution orchestration compiler for execution plans."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import yaml

from agos.core.execution import ExecutionPlan
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec
from agos.core.repo import AgosPaths
from agos.core.task import load_task


@dataclass(frozen=True)
class ExecutionOrchestration:
    spec: OrchestrationRunSpec
    plan: ExecutionPlan


class ExecutionOrchestrator:
    """Compile execution plans into persisted orchestration specs."""

    def __init__(self, paths: AgosPaths) -> None:
        self.paths = paths

    def build_spec(self, plan_path: Path) -> OrchestrationRunSpec:
        payload = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
        plan = ExecutionPlan.model_validate(payload)
        task = load_task(self.paths.task_yaml)
        if plan.task_id != task.id:
            raise ValueError(f"execution plan task_id {plan.task_id!r} does not match active task {task.id!r}")

        nodes = [
            NodeSpec(
                id="validate_plan",
                kind="validate_plan",
                backend="native_async",
                inputs={"plan_path": str(plan_path)},
                policy={},
            )
        ]
        for subtask in plan.subtasks:
            prepare_id = f"prepare-{subtask.id}"
            worker_id = f"worker-{subtask.id}"
            review_id = f"review-{subtask.id}"
            dependency_nodes = tuple(f"review-{dep}" for dep in subtask.depends_on)
            nodes.append(
                NodeSpec(
                    id=prepare_id,
                    kind="prepare_workspace",
                    backend="native_async",
                    adapter=subtask.worker.adapter,
                    depends_on=dependency_nodes,
                    inputs={"subtask_id": subtask.id},
                    policy={},
                )
            )
            nodes.append(
                NodeSpec(
                    id=worker_id,
                    kind="worker_submit",
                    backend="native_async",
                    adapter=subtask.worker.adapter,
                    depends_on=(prepare_id, *dependency_nodes),
                    inputs={"subtask_id": subtask.id},
                    policy={},
                )
            )
            nodes.append(
                NodeSpec(
                    id=review_id,
                    kind="candidate_review_subgraph",
                    backend="native_async",
                    depends_on=(worker_id,),
                    inputs={"subtask_id": subtask.id},
                    policy={},
                )
            )

        spec = OrchestrationRunSpec(
            run_id=_new_run_id(),
            task_id=plan.task_id,
            kind="execution_run",
            backend="native_async",
            entry_nodes=("validate_plan",),
            nodes=tuple(nodes),
            limits={"max_parallel": plan.max_parallel},
            artifacts={},
            metadata={
                "plan_id": plan.id,
                "requires_candidate_review": str(plan.requires_candidate_review).lower(),
            },
        )
        self._save_spec(spec)
        return spec

    def _save_spec(self, spec: OrchestrationRunSpec) -> None:
        path = self.paths.orchestration_runs / f"{spec.run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")


def _new_run_id() -> str:
    return f"execution-run-{uuid4().hex[:12]}"
