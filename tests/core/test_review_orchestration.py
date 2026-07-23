from __future__ import annotations

import json

import pytest

from agos.backends.native_async import BackendRunHandle, NativeAsyncBackend
from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.orchestration.registry import OrchestrationRegistry
from agos.core.repo import repo_paths
from agos.core.review_orchestration import ReviewOrchestrator
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


def _active_task(tmp_repo):
    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-01",
        title="Review orchestration task",
        intent="Compile manual review flows into orchestration runs",
        acceptance=["manual review runs are started through the orchestration seam"],
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    dispatched = ledger.append(
        {
            "type": "executor_dispatched",
            "task_id": task.id,
            "adapter": "multica",
            "run_id": "run-01",
            "issue_id": "AGO-1",
        }
    )
    status = TaskStatus.for_started_task(
        task=task,
        run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
        ledger_head_hash=dispatched["hash"],
    )
    save_status(status, paths)
    return paths


def _ledger_types(paths):
    if not paths.ledger.exists():
        return []
    return [
        json.loads(line)["type"]
        for line in paths.ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_review_orchestrator_builds_manual_review_run(tmp_repo):
    paths = _active_task(tmp_repo)
    registry = OrchestrationRegistry()
    registry.register_orchestration(NativeAsyncBackend())
    orchestrator = ReviewOrchestrator(paths, registry=registry)

    run = orchestrator.start_manual_review(
        diff_kind="governed_repo_diff",
        reviewers=["security_reviewer", "test_reviewer"],
    )

    assert run.backend == "native_async"
    assert run.kind == "review_run"
    assert run.packet_ref == f"reviews/{run.review_id}/packet.json"
    assert run.handle == BackendRunHandle(backend="native_async", run_id=run.run_id)
    assert run.spec.run_id == run.run_id
    assert run.spec.task_id == "agos-01"
    assert [node.id for node in run.spec.nodes] == [
        "reviewer-security_reviewer",
        "reviewer-test_reviewer",
    ]
    assert [node.kind for node in run.spec.nodes] == [
        "wait_for_manual_input",
        "wait_for_manual_input",
    ]
    assert [node.backend for node in run.spec.nodes] == [
        "native_async",
        "native_async",
    ]
    assert run.spec.metadata["kind"] == "review_run"
    assert run.spec.metadata["review_id"] == run.review_id
    assert run.spec.metadata["packet_ref"] == run.packet_ref
    assert run.spec.metadata["diff_kind"] == "governed_repo_diff"
    assert run.spec.metadata["reviewers"] == "security_reviewer,test_reviewer"
    stored = json.loads((paths.orchestration_runs / f"{run.run_id}.json").read_text(encoding="utf-8"))
    assert stored["run_id"] == run.run_id
    assert stored["metadata"]["kind"] == "review_run"
    assert stored["metadata"]["review_id"] == run.review_id


def test_review_orchestrator_resume_manual_review_reuses_persisted_run(tmp_repo):
    paths = _active_task(tmp_repo)
    registry = OrchestrationRegistry()
    backend = NativeAsyncBackend()
    registry.register_orchestration(backend)
    orchestrator = ReviewOrchestrator(paths, registry=registry)
    started = orchestrator.start_manual_review(
        diff_kind="governed_repo_diff",
        reviewers=["security_reviewer"],
    )

    resumed = orchestrator.resume_manual_review(started.run_id)

    assert resumed.backend == "native_async"
    assert resumed.kind == "review_run"
    assert resumed.run_id == started.run_id
    assert resumed.review_id == started.review_id
    assert resumed.packet_ref == started.packet_ref
    assert resumed.handle == BackendRunHandle(backend="native_async", run_id=started.run_id)


@pytest.mark.parametrize(
    ("reviewers", "message"),
    [
        (["security_reviewer", "security_reviewer"], "duplicate reviewer"),
        ([" security_reviewer"], "reviewer names must not contain leading or trailing whitespace"),
        ([""], "reviewer names must be non-empty"),
    ],
)
def test_invalid_reviewers_fail_without_creating_review_side_effects(tmp_repo, reviewers, message):
    paths = _active_task(tmp_repo)
    registry = OrchestrationRegistry()
    registry.register_orchestration(NativeAsyncBackend())
    orchestrator = ReviewOrchestrator(paths, registry=registry)
    ledger_types_before = _ledger_types(paths)

    with pytest.raises(ValueError, match=message):
        orchestrator.start_manual_review(
            diff_kind="governed_repo_diff",
            reviewers=reviewers,
        )

    assert list(paths.reviews.glob("*/packet.json")) == []
    assert list(paths.orchestration_runs.glob("*.json")) == []
    assert _ledger_types(paths) == ledger_types_before
