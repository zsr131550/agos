from __future__ import annotations

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
from agos.core.execution_pipeline import run_auto_execution
from agos.core.execution_runtime import ExecutionRuntimeSnapshot
from agos.core.execution_service import ExecutionService
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.review import Finding
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
    candidate = ExecutionStore(repo_paths(tmp_repo)).read_candidate(result.candidate_ids[0])
    assert candidate.review_refs
    assert candidate.review_refs[-1].state == "completed"


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


def test_run_prepared_plan_stops_when_runtime_state_repeats(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    snapshot = ExecutionRuntimeSnapshot(
        run_id="auto-run-01",
        state="running",
        running_subtasks=("subtask-01",),
    )
    tick_run_ids: list[str] = []

    def fake_tick(_runtime, _plan, *, run_id: str):
        tick_run_ids.append(run_id)
        return snapshot

    monkeypatch.setattr(execution_pipeline.ExecutionRuntime, "tick", fake_tick)

    result = execution_pipeline._run_prepared_plan(
        _service(tmp_repo),
        SimpleNamespace(id="auto-plan-01", subtasks=[]),
    )

    assert result is snapshot
    assert tick_run_ids == ["auto-run-01", "auto-run-01"]


def test_acceptance_reason_records_explicit_missing_review_override():
    assert (
        execution_pipeline._acceptance_reason(reviewed=False)
        == "Automatic pipeline accepted candidate after passing tests with missing review explicitly allowed."
    )


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
