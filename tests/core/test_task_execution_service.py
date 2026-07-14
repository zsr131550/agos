from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from agos.core.adapter import Event, ExecutorRun, RunStatus
from agos.core.execution_pipeline import AutoExecutionResult
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import load_status
from agos.core.task import load_task
from agos.core.task_execution import TaskExecutionRequest
from agos.core.task_execution_service import (
    ResolvedLegacyExecutor,
    TaskExecutionError,
    TaskExecutionService,
)


class _LegacyExecutor:
    name = "fake_legacy"

    def __init__(self, state: str = "completed") -> None:
        self.state = state

    def start(self, task) -> ExecutorRun:
        return ExecutorRun(adapter=self.name, run_id="legacy-run-1", issue_id="AGO-1")

    def stream_events(self, run_id: str, since: int | None = None) -> Iterator[Event]:
        del run_id, since
        return iter(())

    def status(self, run_id: str, issue_id: str | None = None) -> RunStatus:
        del run_id, issue_id
        return RunStatus(state=self.state, detail="legacy finished")


def _write_config(
    tmp_repo: Path,
    *,
    mode: str | None = "legacy",
    candidate_ready: bool = False,
) -> None:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "executor": {"name": "multica", "agent": "Lambda"},
        "default_workflow": "feature",
        "workflows": {"feature": {"gates": []}},
    }
    if mode is not None:
        payload["task_execution"] = {
            "mode": mode,
            "output_contract": "source_code" if mode == "candidate" else "legacy",
        }
    if candidate_ready:
        payload.update(
            {
                "workers": {
                    "offline": {
                        "type": "command",
                        "argv": ["python", "-c", "print('offline')"],
                    }
                },
                "reviewers": {
                    "clean": {"type": "fake", "role": "reviewer", "required": True}
                },
                "allow_fake_reviewer": True,
            }
        )
    paths.agos_yaml.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _auto_result(**updates) -> AutoExecutionResult:
    values = {
        "plan_id": "auto-plan-agos-01",
        "task_id": "agos-01",
        "run_id": "auto-run-01",
        "run_state": "completed",
        "candidate_ids": ["candidate-01"],
        "accepted_candidate_ids": ["candidate-01"],
        "applied_candidate_ids": ["candidate-01"],
        "dry_run": False,
    }
    values.update(updates)
    return AutoExecutionResult.model_validate(values)


def _service(
    tmp_repo: Path,
    *,
    candidate_runner=None,
    readiness=None,
    legacy_state: str = "completed",
) -> TaskExecutionService:
    def legacy_factory(_paths, _selection):
        return ResolvedLegacyExecutor(
            adapter=_LegacyExecutor(legacy_state),
            synchronous=True,
        )

    return TaskExecutionService(
        repo_paths(tmp_repo),
        legacy_executor_factory=legacy_factory,
        candidate_runner=candidate_runner or (lambda _paths, _resume: _auto_result()),
        candidate_readiness=readiness or (lambda _config: []),
        task_id_factory=lambda: "01",
    )


def _event_types(tmp_repo: Path) -> list[str]:
    return [str(record["type"]) for record in Ledger(repo_paths(tmp_repo).ledger).read_all()]


def test_candidate_readiness_failure_publishes_no_task(tmp_repo: Path) -> None:
    _write_config(tmp_repo, mode="candidate", candidate_ready=True)
    service = _service(
        tmp_repo,
        readiness=lambda _config: ["automatic reviewer missing"],
    )

    with pytest.raises(TaskExecutionError, match="automatic reviewer missing"):
        service.start(TaskExecutionRequest(title="Change", mode="candidate"))

    paths = repo_paths(tmp_repo)
    assert not paths.task_yaml.exists()
    assert not paths.ledger.exists()


def test_legacy_start_returns_normalized_result_and_compatible_events(tmp_repo: Path) -> None:
    _write_config(tmp_repo, mode="legacy")

    result = _service(tmp_repo).start(TaskExecutionRequest(title="Legacy task"))

    assert result.mode == "legacy"
    assert result.run_id == "legacy-run-1"
    assert result.issue_id == "AGO-1"
    assert result.state == "completed"
    assert _event_types(tmp_repo) == [
        "task_started",
        "gates_locked",
        "task_execution_started",
        "executor_dispatched",
        "executor_completed",
        "task_execution_completed",
    ]
    status = load_status(repo_paths(tmp_repo))
    assert status is not None
    assert status.phase == "done"
    assert status.executor_run is not None
    assert status.executor_run.run_id == "legacy-run-1"


def test_old_config_uses_legacy_mode_and_reports_compatibility_warning(tmp_repo: Path) -> None:
    _write_config(tmp_repo, mode=None)

    result = _service(tmp_repo).start(TaskExecutionRequest(title="Old config"))

    assert result.mode == "legacy"
    assert any("task_execution" in warning for warning in result.compatibility_warnings)
    task = load_task(repo_paths(tmp_repo).task_yaml)
    assert task.execution_mode == "legacy"
    assert task.output_contract == "legacy"


def test_candidate_start_normalizes_and_persists_pipeline_result(tmp_repo: Path) -> None:
    _write_config(tmp_repo, mode="candidate", candidate_ready=True)
    observed: dict[str, object] = {}

    def candidate_runner(paths, resume_run_id):
        observed["paths"] = paths
        observed["resume_run_id"] = resume_run_id
        task_id = load_task(paths.task_yaml).id
        return _auto_result(task_id=task_id, plan_id=f"auto-plan-{task_id}")

    result = _service(tmp_repo, candidate_runner=candidate_runner).start(
        TaskExecutionRequest(title="Candidate task")
    )

    paths = repo_paths(tmp_repo)
    assert observed == {"paths": paths, "resume_run_id": None}
    assert result.mode == "candidate"
    assert result.state == "completed"
    assert result.candidate_ids == result.applied_candidate_ids == ["candidate-01"]
    assert _event_types(tmp_repo)[-1] == "task_execution_completed"
    persisted = json.loads(
        (paths.current_task / "execution" / "task-execution.json").read_text(encoding="utf-8")
    )
    assert persisted == result.model_dump(mode="json")
    status = load_status(paths)
    assert status is not None
    assert status.phase == "done"
    assert status.executor_run is not None
    assert status.executor_run.adapter == "candidate_pipeline"


def test_candidate_block_is_returned_with_resumable_state(tmp_repo: Path) -> None:
    _write_config(tmp_repo, mode="candidate", candidate_ready=True)
    blocked = _auto_result(
        run_state="stuck",
        candidate_ids=[],
        accepted_candidate_ids=[],
        applied_candidate_ids=[],
        blocked_stage="execution",
        blocked_reason="polling budget exhausted",
    )

    result = _service(tmp_repo, candidate_runner=lambda _paths, _resume: blocked).start(
        TaskExecutionRequest(title="Stuck candidate")
    )

    assert result.state == "stuck"
    assert result.blocked_stage == "execution"
    assert result.blocked_reason == "polling budget exhausted"
    assert repo_paths(tmp_repo).task_yaml.is_file()
    assert _event_types(tmp_repo)[-1] == "task_execution_blocked"


def test_candidate_runner_exception_persists_block_before_raising(tmp_repo: Path) -> None:
    _write_config(tmp_repo, mode="candidate", candidate_ready=True)

    def explode(_paths, _resume):
        raise RuntimeError("pipeline exploded")

    with pytest.raises(TaskExecutionError, match="pipeline exploded"):
        _service(tmp_repo, candidate_runner=explode).start(
            TaskExecutionRequest(title="Broken candidate")
        )

    paths = repo_paths(tmp_repo)
    assert paths.task_yaml.is_file()
    assert _event_types(tmp_repo)[-1] == "task_execution_blocked"
    persisted = json.loads(
        (paths.current_task / "execution" / "task-execution.json").read_text(encoding="utf-8")
    )
    assert persisted["state"] == "failed"
    assert persisted["blocked_reason"] == "pipeline exploded"


def test_candidate_resume_passes_persisted_run_id_to_runner(tmp_repo: Path) -> None:
    _write_config(tmp_repo, mode="candidate", candidate_ready=True)
    calls: list[str | None] = []
    results = [
        _auto_result(
            run_state="stuck",
            candidate_ids=[],
            accepted_candidate_ids=[],
            applied_candidate_ids=[],
            blocked_stage="execution",
            blocked_reason="polling budget exhausted",
        ),
        _auto_result(),
    ]

    def candidate_runner(_paths, resume_run_id):
        calls.append(resume_run_id)
        return results.pop(0)

    service = _service(tmp_repo, candidate_runner=candidate_runner)
    started = service.start(TaskExecutionRequest(title="Resume candidate"))
    resumed = service.resume_candidate()

    assert calls == [None, started.run_id]
    assert resumed.state == "completed"
    assert resumed.applied_candidate_ids == ["candidate-01"]
