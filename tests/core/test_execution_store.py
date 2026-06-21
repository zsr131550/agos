from __future__ import annotations

from agos.core.execution import (
    ArbiterDecision,
    CandidatePatch,
    CandidateTestRun,
    ExecutionPlan,
    ExecutionSubtask,
    ExecutionWorker,
    WorkspaceBinding,
)
from agos.core.execution_store import ExecutionStore
from agos.core.repo import repo_paths


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        id="execution-plan-01",
        task_id="agos-01",
        subtasks=[
            ExecutionSubtask(
                id="subtask-a",
                title="A",
                write_scope=["src/a.py"],
                worker=ExecutionWorker(adapter="local_worktree", role="worker_agent"),
            )
        ],
    )


def test_execution_store_writes_and_reads_artifacts(tmp_repo):
    paths = repo_paths(tmp_repo)
    store = ExecutionStore(paths)
    plan = _plan()
    subtask = plan.subtasks[0]
    workspace = WorkspaceBinding(
        subtask_id=subtask.id,
        path="../.agos-worktrees/agos-01/subtask-a",
        base_ref="main",
        base_commit="deadbeef",
    )
    candidate = CandidatePatch(
        id="candidate-01",
        task_id="agos-01",
        subtask_id=subtask.id,
        source_agent="local_worktree",
        workspace_ref="execution/workspaces/subtask-a.json",
        patch_ref="evidence/candidate_patches/candidate-01.patch",
        patch_sha256="abc123",
        base_commit="deadbeef",
        summary="Summary.",
    )
    test_run = CandidateTestRun(
        id="candidate-test-01",
        candidate_id=candidate.id,
        gate_id="patch_applies",
        command="git apply --check",
        state="passed",
        evidence_ref="gates/candidate-01-patch_applies.log",
        workspace_ref=workspace.ref,
    )
    decision = ArbiterDecision(
        id="decision-01",
        candidate_id=candidate.id,
        decision="rejected",
        reason="Needs changes.",
        decided_by="local_user",
    )

    assert store.write_plan(plan) == "execution/plan.json"
    assert store.write_subtask(subtask) == "execution/subtasks/subtask-a.json"
    assert store.write_workspace(workspace) == "execution/workspaces/subtask-a.json"
    assert store.write_candidate(candidate) == "execution/candidates/candidate-01.json"
    assert store.write_test_run(test_run) == "execution/tests/candidate-test-01.json"
    assert store.write_decision(decision) == "execution/decisions/decision-01.json"

    assert store.read_plan().id == "execution-plan-01"
    assert store.read_subtask("subtask-a").write_scope == ["src/a.py"]
    assert store.read_workspace("subtask-a").base_commit == "deadbeef"
    assert store.read_candidate("candidate-01").patch_sha256 == "abc123"
    assert store.read_test_runs("candidate-01")[0].gate_id == "patch_applies"
    assert store.read_decisions("candidate-01")[0].decision == "rejected"


def test_execution_store_writes_patch_bytes(tmp_repo):
    store = ExecutionStore(repo_paths(tmp_repo))

    patch_ref, patch_sha = store.write_candidate_patch("candidate-01", b"diff --git a/a b/a\n")

    assert patch_ref == "evidence/candidate_patches/candidate-01.patch"
    assert len(patch_sha) == 64
