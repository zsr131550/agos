from __future__ import annotations

import pytest
from pydantic import ValidationError

from agos.core.execution import (
    ArbiterDecision,
    CandidatePatch,
    CandidateTestRun,
    ExecutionPlan,
    ExecutionSubtask,
    ExecutionWorker,
    ReviewBinding,
    WorkspaceBinding,
)


def _subtask(subtask_id: str, write_scope: list[str], depends_on: list[str] | None = None):
    return ExecutionSubtask(
        id=subtask_id,
        title=subtask_id,
        intent="Implement a bounded slice.",
        depends_on=depends_on or [],
        write_scope=write_scope,
        worker=ExecutionWorker(adapter="local_worktree", role="worker_agent"),
    )


def test_execution_plan_rejects_unknown_dependencies():
    with pytest.raises(ValidationError, match="unknown dependency"):
        ExecutionPlan(
            id="execution-plan-01",
            task_id="agos-01",
            subtasks=[_subtask("subtask-a", ["src/a.py"], depends_on=["subtask-missing"])],
        )


def test_execution_plan_rejects_unserialized_overlapping_write_scopes():
    with pytest.raises(ValidationError, match="overlapping write_scope"):
        ExecutionPlan(
            id="execution-plan-01",
            task_id="agos-01",
            subtasks=[
                _subtask("subtask-a", ["src/shared.py"]),
                _subtask("subtask-b", ["src/shared.py"]),
            ],
        )


def test_execution_plan_allows_serialized_overlapping_write_scopes():
    plan = ExecutionPlan(
        id="execution-plan-01",
        task_id="agos-01",
        subtasks=[
            _subtask("subtask-a", ["src/shared.py"]),
            _subtask("subtask-b", ["src/shared.py"], depends_on=["subtask-a"]),
        ],
    )

    assert [subtask.id for subtask in plan.subtasks] == ["subtask-a", "subtask-b"]


def test_candidate_patch_round_trips_review_and_test_refs():
    candidate = CandidatePatch(
        id="candidate-01",
        task_id="agos-01",
        subtask_id="subtask-a",
        source_agent="local_worktree",
        workspace_ref="execution/workspaces/subtask-a.json",
        patch_ref="evidence/candidate_patches/candidate-01.patch",
        patch_sha256="abc123",
        base_commit="deadbeef",
        summary="Add execution models.",
        test_refs=["execution/tests/candidate-test-01.json"],
        review_refs=[
            ReviewBinding(
                review_id="review-01",
                packet_ref="reviews/review-01/packet.json",
                report_ref="reviews/review-01/findings.json",
                state="completed",
            )
        ],
    )

    data = candidate.model_dump()

    assert data["status"] == "proposed"
    assert data["review_refs"][0]["state"] == "completed"


def test_candidate_test_run_has_candidate_stage_defaults():
    run = CandidateTestRun(
        id="candidate-test-01",
        candidate_id="candidate-01",
        gate_id="patch_applies",
        command="git apply --check",
        state="passed",
        evidence_ref="gates/candidate-01-patch_applies.log",
        workspace_ref="execution/workspaces/subtask-a.json",
    )

    assert run.stage == "candidate"


def test_accepted_decision_requires_evidence_and_direct_patch_strategy():
    with pytest.raises(ValidationError, match="accepted decisions require evidence_refs"):
        ArbiterDecision(
            id="decision-01",
            candidate_id="candidate-01",
            decision="accepted",
            reason="Looks good.",
            evidence_refs=[],
            decided_by="local_user",
        )


def test_workspace_binding_records_git_worktree_base():
    binding = WorkspaceBinding(
        subtask_id="subtask-a",
        path="../.agos-worktrees/agos-01/subtask-a",
        base_ref="main",
        base_commit="deadbeef",
    )

    assert binding.kind == "git_worktree"
