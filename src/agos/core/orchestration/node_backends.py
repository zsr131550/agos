"""Adapters that expose AGOS agents as graph runtime node backends."""
from __future__ import annotations

from typing import Any

from agos.core.execution_worker import ExecutionWorkerAdapter, WorkerStartRequest, ensure_worker_ready
from agos.core.orchestration.models import AgentJobHandle, NodeRunStatus, NodeSpec, OrchestrationRunSpec
from agos.core.review import ReviewPacket
from agos.core.review_adapter import ReviewerAdapter, ReviewerStartRequest


class WorkerNodeBackend:
    """Expose an execution worker adapter through the graph node lifecycle."""

    def __init__(self, adapter: ExecutionWorkerAdapter) -> None:
        self.adapter = adapter
        self.name = adapter.name

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        ensure_worker_ready(self.adapter)
        subtask_id = node.metadata.get("subtask_id", node.id)
        worker_run_id = f"{run.run_id}:{node.id}"
        worker_run = self.adapter.start(
            WorkerStartRequest(
                run_id=worker_run_id,
                subtask_id=subtask_id,
                prompt=str(node.inputs.get("prompt", node.metadata.get("prompt", ""))),
                workspace_path=node.metadata.get("workspace_path", ""),
                metadata={"orchestration_run_id": run.run_id, "node_id": node.id},
            )
        )
        return AgentJobHandle(
            backend=self.name,
            job_id=worker_run.run_id,
            node_id=node.id,
            run_id=run.run_id,
        )

    def poll(self, handle: AgentJobHandle) -> NodeRunStatus:
        status = self.adapter.poll(handle.job_id, subtask_id=handle.node_id)
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state=_node_state(status.state),
            detail=status.detail,
            output_refs=_single_output_ref(handle.node_id, status.output_refs),
        )

    def cancel(self, handle: AgentJobHandle) -> NodeRunStatus:
        status = self.adapter.cancel(handle.job_id)
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state=_node_state(status.state),
            detail=status.detail,
            output_refs=_single_output_ref(handle.node_id, status.output_refs),
        )

    def collect(self, handle: AgentJobHandle) -> dict[str, object]:
        return {"run_id": handle.run_id, "node_id": handle.node_id, "job_id": handle.job_id}


class ReviewerNodeBackend:
    """Expose a reviewer adapter through the graph node lifecycle."""

    def __init__(self, adapter: ReviewerAdapter) -> None:
        self.adapter = adapter
        self.name = adapter.name

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        review_run_id = f"{run.run_id}:{node.id}"
        reviewer_id = node.metadata.get("reviewer_id", node.id)
        reviewer_run = self.adapter.start(
            ReviewerStartRequest(
                run_id=review_run_id,
                reviewer_id=reviewer_id,
                role=node.metadata.get("role", "reviewer"),
                packet=_review_packet(run, node, reviewer_id),
                metadata={"orchestration_run_id": run.run_id, "node_id": node.id},
            )
        )
        return AgentJobHandle(
            backend=self.name,
            job_id=reviewer_run.run_id,
            node_id=node.id,
            run_id=run.run_id,
        )

    def poll(self, handle: AgentJobHandle) -> NodeRunStatus:
        status = self.adapter.poll(handle.job_id, reviewer_id=handle.node_id)
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state=_node_state(status.state),
            detail=status.detail,
            output_refs={handle.node_id: status.raw_ref} if status.raw_ref else {},
        )

    def cancel(self, handle: AgentJobHandle) -> NodeRunStatus:
        status = self.adapter.cancel(handle.job_id)
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state=_node_state(status.state),
            detail=status.detail,
        )

    def collect(self, handle: AgentJobHandle) -> dict[str, object]:
        return {"run_id": handle.run_id, "node_id": handle.node_id, "job_id": handle.job_id}


class ArbiterNodeBackend:
    """Deterministic local backend for arbiter graph nodes."""

    def __init__(self, *, name: str = "arbiter", output_refs: dict[str, str] | None = None) -> None:
        self.name = name
        self.output_refs = dict(output_refs or {})

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        return AgentJobHandle(
            backend=self.name,
            job_id=f"{run.run_id}:{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )

    def poll(self, handle: AgentJobHandle) -> NodeRunStatus:
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state="completed",
            output_refs=self.output_refs,
        )

    def cancel(self, handle: AgentJobHandle) -> NodeRunStatus:
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state="cancelled",
        )

    def collect(self, handle: AgentJobHandle) -> dict[str, object]:
        return {"run_id": handle.run_id, "node_id": handle.node_id, "job_id": handle.job_id}


def _single_output_ref(node_id: str, output_refs: list[str]) -> dict[str, str]:
    return {node_id: output_refs[-1]} if output_refs else {}


def _node_state(state: str) -> str:
    if state == "blocked":
        return "waiting"
    if state in {"queued", "running", "waiting", "completed", "failed", "cancelled"}:
        return state
    return "failed"


def _review_packet(run: OrchestrationRunSpec, node: NodeSpec, reviewer_id: str) -> ReviewPacket:
    metadata: dict[str, Any] = dict(node.metadata)
    return ReviewPacket(
        review_id=metadata.get("review_id", f"{run.run_id}:{node.id}"),
        task_id=run.task_id,
        task_title=metadata.get("task_title", node.id),
        task_intent=metadata.get("task_intent", ""),
        diff_kind=metadata.get("diff_kind", "governed_repo_diff"),
        diff_evidence_ref=metadata.get("diff_evidence_ref"),
        ledger_head_hash=metadata.get("ledger_head_hash", run.run_id),
        subject={"reviewer_id": reviewer_id, "node_id": node.id},
        context_refs=[ref for ref in [metadata.get("context_ref")] if ref],
    )
