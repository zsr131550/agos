from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import yaml

import agos.core.execution_pipeline as execution_pipeline
from agos.adapters.reviewers import FakeReviewerAdapter
from agos.adapters.workers import LocalWorktreeWorkerAdapter, WorkerRun
from agos.core.adapter import ExecutorRun
from agos.core.config import AGOSConfig
from agos.core.execution import ExecutionPlan
from agos.core.execution_pipeline import run_auto_execution
from agos.core.execution_runtime import ExecutionRuntimeSnapshot
from agos.core.execution_service import ExecutionService
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.review import Finding
from agos.core.review_store import ReviewStore
from agos.core.review_orchestrator import ReviewerSpec
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


def _active_task(tmp_repo: Path, *, passing_gate: bool = True):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    assertion = "startswith('# changed')" if passing_gate else "startswith('# expected-other')"
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workers": {"editing": {"type": "local_worktree"}},
                "reviewers": {"clean": {"type": "fake", "role": "reviewer"}},
                "orchestration": {"backend": "native_async", "max_parallel": 1},
                "workflows": {
                    "feature": {
                        "gates": [
                            {
                                "id": "readme_changed",
                                "stage": ["candidate"],
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    f"from pathlib import Path; assert Path('README.md').read_text().{assertion}",
                                ],
                            }
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task = Task(
        id="agos-01",
        title="Automatic execution task",
        intent="Update the README heading.",
        workflow="feature",
        gates=["readme_changed"],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    locked = ledger.append(
        {
            "type": "gates_locked",
            "task_id": task.id,
            "gates": [
                {
                    "id": "readme_changed",
                    "stage": ["candidate"],
                    "argv": [
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; assert Path('README.md').read_text().{assertion}",
                    ],
                    "command": None,
                    "type": None,
                }
            ],
        }
    )
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
            ledger_head_hash=locked["hash"],
        ),
        paths,
    )
    return paths


class _EditingWorker(LocalWorktreeWorkerAdapter):
    def __init__(self, manager, *, content: str = "# changed\n") -> None:
        super().__init__(manager, name="editing")
        self.content = content

    def start(self, request):
        Path(request.workspace_path, "README.md").write_text(self.content, encoding="utf-8")
        return WorkerRun(
            backend=self.name,
            run_id=request.run_id,
            subtask_id=request.subtask_id,
            state="completed",
        )


def _service(tmp_repo: Path, *, content: str = "# changed\n") -> ExecutionService:
    paths = repo_paths(tmp_repo)
    service = ExecutionService(
        paths,
        worktree_root=tmp_repo.parent / ".agos-worktrees" / "agos-01",
    )
    service.register_worker_adapter(_EditingWorker(service.workspace_manager, content=content))
    return service


def _reviewers(*, blocking: bool = False):
    findings = []
    if blocking:
        findings.append(
            Finding(
                id="finding-01",
                review_id="pending",
                source_agent="fake",
                category="correctness",
                severity="high",
                blocking=True,
                title="Blocking issue",
                body="The candidate should not be accepted.",
            )
        )
    return (
        {"clean": FakeReviewerAdapter(name="clean", findings=findings)},
        [ReviewerSpec(id="clean", role="reviewer", adapter="clean", required=True)],
    )


class _FileEditingWorker(LocalWorktreeWorkerAdapter):
    def __init__(self, manager, *, name: str, relative_path: str, content: str) -> None:
        super().__init__(manager, name=name)
        self.relative_path = relative_path
        self.content = content

    def start(self, request):
        target = Path(request.workspace_path, self.relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.content, encoding="utf-8")
        return WorkerRun(
            backend=self.name,
            run_id=request.run_id,
            subtask_id=request.subtask_id,
            state="completed",
        )


def _active_task_multi_worker(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workers": {
                    "editing_docs": {"type": "local_worktree"},
                    "editing_readme": {"type": "local_worktree"},
                },
                "reviewers": {"clean": {"type": "fake", "role": "reviewer"}},
                "orchestration": {"backend": "native_async", "max_parallel": 2},
                "workflows": {"feature": {"gates": []}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task = Task(
        id="agos-01",
        title="Automatic multi-worker task",
        intent="Update README and docs.",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    locked = ledger.append({"type": "gates_locked", "task_id": task.id, "gates": []})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
            ledger_head_hash=locked["hash"],
        ),
        paths,
    )
    return paths


def _multi_worker_service(tmp_repo: Path) -> ExecutionService:
    paths = repo_paths(tmp_repo)
    service = ExecutionService(
        paths,
        worktree_root=tmp_repo.parent / ".agos-worktrees" / "agos-01",
    )
    service.register_worker_adapter(
        _FileEditingWorker(
            service.workspace_manager,
            name="editing_readme",
            relative_path="README.md",
            content="# changed by readme worker\n",
        )
    )
    service.register_worker_adapter(
        _FileEditingWorker(
            service.workspace_manager,
            name="editing_docs",
            relative_path="docs/agent-loop.md",
            content="# changed by docs worker\n",
        )
    )
    return service


def test_planner_multi_worker_run_exports_candidates_for_each_assigned_worker(tmp_repo):
    _active_task_multi_worker(tmp_repo)
    reviewer_adapters, reviewer_specs = _reviewers()

    result = run_auto_execution(
        _multi_worker_service(tmp_repo),
        apply=False,
        planner_json=json.dumps(
            {
                "id": "multi-worker-plan",
                "task_id": "wrong-task",
                "max_parallel": 2,
                "requires_candidate_review": True,
                "subtasks": [
                    {
                        "id": "docs-subtask",
                        "title": "Update docs",
                        "intent": "Write autonomous loop docs.",
                        "depends_on": [],
                        "write_scope": ["docs"],
                        "worker": {"adapter": "editing_docs", "role": "docs_agent"},
                    },
                    {
                        "id": "readme-subtask",
                        "title": "Update README",
                        "intent": "Summarize autonomous loop.",
                        "depends_on": [],
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "editing_readme", "role": "impl_agent"},
                    },
                ],
            }
        ),
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert result.planner_source == "planner_json"
    assert result.subtask_worker_assignments == {
        "docs-subtask": "editing_docs",
        "readme-subtask": "editing_readme",
    }
    assert set(result.completed_subtasks) == {"docs-subtask", "readme-subtask"}
    assert len(result.candidate_ids) == 2
    assert result.accepted_candidate_ids == result.candidate_ids
    candidates = [ExecutionStore(repo_paths(tmp_repo)).read_candidate(candidate_id) for candidate_id in result.candidate_ids]
    assert {candidate.source_agent for candidate in candidates} == {"editing_docs", "editing_readme"}


def test_dry_run_accepts_clean_candidate_but_does_not_apply(tmp_repo):
    paths = _active_task(tmp_repo)
    reviewer_adapters, reviewer_specs = _reviewers()

    result = run_auto_execution(
        _service(tmp_repo),
        apply=False,
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert result.dry_run is True
    assert len(result.candidate_ids) == 1
    assert result.accepted_candidate_ids == result.candidate_ids
    assert result.applied_candidate_ids == []
    assert (tmp_repo / "README.md").read_text(encoding="utf-8") == "# t\n"
    candidate = ExecutionStore(paths).read_candidate(result.candidate_ids[0])
    assert candidate.status == "accepted"


def test_missing_reviewers_do_not_create_accepted_candidate_by_default(tmp_repo):
    _active_task(tmp_repo)

    result = run_auto_execution(_service(tmp_repo), apply=False)

    assert len(result.candidate_ids) == 1
    assert result.accepted_candidate_ids == []
    assert any("review" in note for note in result.notes)


def test_allow_missing_review_creates_explicit_clean_candidate_review(tmp_repo):
    _active_task(tmp_repo)

    result = run_auto_execution(
        _service(tmp_repo),
        apply=False,
        allow_missing_review=True,
    )

    assert result.accepted_candidate_ids == result.candidate_ids
    assert result.blocked_stage is None
    assert any("missing review explicitly allowed" in note for note in result.notes)
    assert result.candidate_review_ids[result.candidate_ids[0]].startswith("review-")
    store = ExecutionStore(repo_paths(tmp_repo))
    candidate = store.read_candidate(result.candidate_ids[0])
    assert candidate.review_refs
    assert candidate.review_refs[-1].state == "completed"
    decisions = store.read_decisions(candidate.id)
    assert decisions[-1].reason == (
        "Automatic pipeline accepted candidate after passing tests with missing review explicitly allowed."
    )


def test_failing_candidate_tests_prevent_acceptance(tmp_repo):
    _active_task(tmp_repo, passing_gate=False)
    reviewer_adapters, reviewer_specs = _reviewers()

    result = run_auto_execution(
        _service(tmp_repo),
        apply=True,
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert len(result.candidate_ids) == 1
    assert result.accepted_candidate_ids == []
    assert result.applied_candidate_ids == []
    assert (tmp_repo / "README.md").read_text(encoding="utf-8") == "# t\n"


def test_blocking_review_prevents_acceptance(tmp_repo):
    _active_task(tmp_repo)
    reviewer_adapters, reviewer_specs = _reviewers(blocking=True)

    result = run_auto_execution(
        _service(tmp_repo),
        apply=True,
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert len(result.candidate_ids) == 1
    assert result.accepted_candidate_ids == []
    assert result.applied_candidate_ids == []


def test_apply_requires_explicit_apply_flag(tmp_repo):
    _active_task(tmp_repo)
    reviewer_adapters, reviewer_specs = _reviewers()

    result = run_auto_execution(
        _service(tmp_repo),
        apply=True,
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert result.dry_run is False
    assert result.accepted_candidate_ids == result.candidate_ids
    assert result.applied_candidate_ids == result.candidate_ids
    assert (tmp_repo / "README.md").read_text(encoding="utf-8") == "# changed\n"


def test_submit_candidate_failure_is_reported_without_acceptance(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    service = _service(tmp_repo)

    def fail_submit(*_args, **_kwargs):
        raise RuntimeError("candidate export unavailable")

    monkeypatch.setattr(service, "submit_candidate", fail_submit)

    result = run_auto_execution(service, apply=False)

    assert result.candidate_ids == []
    assert result.accepted_candidate_ids == []
    assert any("candidate skipped" in note for note in result.notes)


def test_auto_execution_result_carries_reviewer_raw_refs(tmp_repo):
    paths = _active_task(tmp_repo)
    reviewer_adapters = {"clean": FakeReviewerAdapter(name="clean", review_store=ReviewStore(paths))}
    reviewer_specs = [ReviewerSpec(id="clean", role="reviewer", adapter="clean", required=True)]

    result = run_auto_execution(
        _service(tmp_repo),
        apply=False,
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    candidate_id = result.candidate_ids[0]
    assert result.reviewer_ids == ["clean"]
    assert result.candidate_review_raw_refs[candidate_id]
    candidate = ExecutionStore(paths).read_candidate(candidate_id)
    assert candidate.review_refs[-1].raw_refs == result.candidate_review_raw_refs[candidate_id]


def test_required_reviewer_failure_marks_failed_binding_and_reports_review_mapping(tmp_repo):
    paths = _active_task(tmp_repo)
    reviewer_adapters = {
        "clean": FakeReviewerAdapter(name="clean", state="failed", review_store=ReviewStore(paths))
    }
    reviewer_specs = [ReviewerSpec(id="clean", role="reviewer", adapter="clean", required=True)]

    result = run_auto_execution(
        _service(tmp_repo),
        apply=False,
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    candidate_id = result.candidate_ids[0]
    assert result.accepted_candidate_ids == []
    assert result.blocked_stage == "review"
    assert result.reviewer_ids == ["clean"]
    assert result.candidate_review_ids[candidate_id].startswith("review-")
    assert result.candidate_review_raw_refs[candidate_id]
    candidate = ExecutionStore(paths).read_candidate(candidate_id)
    assert candidate.review_refs[-1].state == "failed"
    assert candidate.review_refs[-1].raw_refs == result.candidate_review_raw_refs[candidate_id]


def test_review_failure_is_reported_without_acceptance(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    service = _service(tmp_repo)
    reviewer_adapters, reviewer_specs = _reviewers()

    def fail_review(*_args, **_kwargs):
        raise RuntimeError("review backend unavailable")

    monkeypatch.setattr(service, "run_candidate_review", fail_review)

    result = run_auto_execution(
        service,
        apply=False,
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert len(result.candidate_ids) == 1
    assert result.accepted_candidate_ids == []
    assert any("review failed" in note for note in result.notes)


def test_decision_failure_is_reported_without_acceptance(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    service = _service(tmp_repo)
    reviewer_adapters, reviewer_specs = _reviewers()

    def fail_decide(*_args, **_kwargs):
        raise RuntimeError("decision store unavailable")

    monkeypatch.setattr(service, "decide_candidate", fail_decide)

    result = run_auto_execution(
        service,
        apply=False,
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert len(result.candidate_ids) == 1
    assert result.accepted_candidate_ids == []
    assert any("was not accepted" in note for note in result.notes)


def test_apply_failure_is_reported_after_acceptance(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    service = _service(tmp_repo)
    reviewer_adapters, reviewer_specs = _reviewers()

    def fail_apply(*_args, **_kwargs):
        raise RuntimeError("apply backend unavailable")

    monkeypatch.setattr(service, "apply_candidate", fail_apply)

    result = run_auto_execution(
        service,
        apply=True,
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert result.accepted_candidate_ids == result.candidate_ids
    assert result.applied_candidate_ids == []
    assert any("apply failed" in note for note in result.notes)


def test_run_auto_execution_passes_planner_json_to_plan_creation(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    reviewer_adapters, reviewer_specs = _reviewers()
    captured = {}

    def fake_create_execution_plan(task, config, workers, *, planner_json=None, planner=None):
        captured["planner_json"] = planner_json
        plan = ExecutionPlan.model_validate(
            {
                "id": "auto-plan-01",
                "task_id": task.id,
                "max_parallel": 1,
                "requires_candidate_review": True,
                "subtasks": [
                    {
                        "id": "subtask-01",
                        "title": "Update README",
                        "intent": "Update the README heading.",
                        "depends_on": [],
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "editing", "role": "worker_agent"},
                    }
                ],
            }
        )
        return SimpleNamespace(plan=plan, source="planner_json")

    monkeypatch.setattr(
        execution_pipeline,
        "create_execution_plan_with_provenance",
        fake_create_execution_plan,
    )
    monkeypatch.setattr(
        execution_pipeline,
        "_run_prepared_plan",
        lambda _service, plan: ExecutionRuntimeSnapshot(
            run_id="auto-run-01",
            state="completed",
            completed_subtasks=(),
            failed_subtasks=(),
        ),
    )

    execution_pipeline.run_auto_execution(
        _service(tmp_repo),
        apply=False,
        planner_json='{"plan":"from-planner"}',
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert captured["planner_json"] == '{"plan":"from-planner"}'


def test_run_prepared_plan_waits_for_repeated_running_state_until_budget_exhausted(
    monkeypatch, tmp_repo
):
    _active_task(tmp_repo)
    tick_run_ids: list[str] = []
    sleeps: list[float] = []

    def fake_tick(_runtime, _plan, *, run_id: str):
        tick_run_ids.append(run_id)
        return ExecutionRuntimeSnapshot(
            run_id="auto-run-01",
            state="running",
            running_subtasks=("subtask-01",),
        )

    monkeypatch.setattr(execution_pipeline.ExecutionRuntime, "tick", fake_tick)
    capped_config = AGOSConfig.model_validate(
        {
            "executor": {"name": "multica", "agent": "Lambda"},
            "workers": {"editing": {"type": "local_worktree", "poll_interval_seconds": 2}},
            "orchestration": {"max_parallel": 1, "max_tick_iterations": 2},
            "workflows": {"feature": {"gates": []}},
        }
    )
    monkeypatch.setattr(execution_pipeline, "load_config", lambda _root: capped_config)

    result = execution_pipeline._run_prepared_plan(
        _service(tmp_repo),
        SimpleNamespace(
            id="auto-plan-01",
            subtasks=[
                SimpleNamespace(
                    id="subtask-01",
                    worker=SimpleNamespace(adapter="editing"),
                    workspace_ref=None,
                )
            ],
        ),
        sleeper=sleeps.append,
    )

    assert result.state == "stuck"
    assert tick_run_ids == ["auto-run-01", "auto-run-01", "auto-run-01"]
    assert sleeps == [2, 2]


def test_run_prepared_plan_completes_after_repeated_running_state(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    tick_run_ids: list[str] = []
    sleeps: list[float] = []

    def fake_tick(_runtime, _plan, *, run_id: str):
        tick_run_ids.append(run_id)
        if len(tick_run_ids) == 3:
            return ExecutionRuntimeSnapshot(run_id="auto-run-01", state="completed")
        return ExecutionRuntimeSnapshot(
            run_id="auto-run-01",
            state="running",
            running_subtasks=("subtask-01",),
        )

    monkeypatch.setattr(execution_pipeline.ExecutionRuntime, "tick", fake_tick)

    capped_config = AGOSConfig.model_validate(
        {
            "executor": {"name": "multica", "agent": "Lambda"},
            "workers": {"editing": {"type": "local_worktree", "poll_interval_seconds": 2}},
            "orchestration": {"max_parallel": 1, "max_tick_iterations": 2},
            "workflows": {"feature": {"gates": []}},
        }
    )
    monkeypatch.setattr(execution_pipeline, "load_config", lambda _root: capped_config)

    result = execution_pipeline._run_prepared_plan(
        _service(tmp_repo),
        SimpleNamespace(
            id="auto-plan-01",
            subtasks=[
                SimpleNamespace(
                    id="subtask-01",
                    worker=SimpleNamespace(adapter="editing"),
                    workspace_ref=None,
                )
            ],
        ),
        sleeper=sleeps.append,
    )

    assert result.state == "completed"
    assert tick_run_ids == ["auto-run-01", "auto-run-01", "auto-run-01"]
    assert sleeps == [2, 2]


def test_acceptance_reason_records_explicit_missing_review_override():
    assert (
        execution_pipeline._acceptance_reason(reviewed=False)
        == "Automatic pipeline accepted candidate after passing tests with missing review explicitly allowed."
    )


def test_auto_execution_result_reports_planner_workers_reviewers_and_review_bindings(tmp_repo):
    _active_task(tmp_repo)
    reviewer_adapters, reviewer_specs = _reviewers()

    result = run_auto_execution(
        _service(tmp_repo),
        apply=False,
        planner_json=json.dumps(
            {
                "id": "planner-plan",
                "task_id": "wrong-task",
                "max_parallel": 1,
                "requires_candidate_review": True,
                "subtasks": [
                    {
                        "id": "planned-subtask",
                        "title": "Update README",
                        "intent": "Update the README heading.",
                        "depends_on": [],
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "editing", "role": "worker_agent"},
                    }
                ],
            }
        ),
        reviewer_adapters=reviewer_adapters,
        reviewer_specs=reviewer_specs,
    )

    assert result.planner_source == "planner_json"
    assert result.subtask_worker_assignments == {"planned-subtask": "editing"}
    assert result.reviewer_ids == ["clean"]
    assert result.accepted_candidate_ids == result.candidate_ids
    assert result.blocked_stage is None
    assert result.candidate_review_ids[result.candidate_ids[0]].startswith("review-")


def test_missing_review_sets_blocked_stage(tmp_repo):
    _active_task(tmp_repo)

    blocked = run_auto_execution(_service(tmp_repo), apply=False)

    assert blocked.accepted_candidate_ids == []
    assert blocked.blocked_stage == "review"
    assert "review" in (blocked.blocked_reason or "")


def _commit(repo: Path, message: str) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True, env=env)
