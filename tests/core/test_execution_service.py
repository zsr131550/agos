from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agos.adapters.workers import LocalWorktreeWorkerAdapter
from agos.core.adapter import ExecutorRun
from agos.core.execution_service import ExecutionService
from agos.core.execution_store import ExecutionStore
from agos.core.execution_worker import WorkerHealth, WorkerHealthCheck
from agos.core.ledger import Ledger
from agos.core.orchestration.models import OrchestratorRunHandle, OrchestratorRunStatus
from agos.core.repo import repo_paths
from agos.core.review import Finding, FindingResolution
from agos.core.review_service import ReviewService
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


def _active_task(
    tmp_repo: Path,
    *,
    gates: list[str] | None = None,
    orchestration: dict[str, object] | None = None,
):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "default_workflow": "feature",
                "orchestration": orchestration or {"backend": "native_async"},
                "workflows": {
                    "feature": {
                        "gates": [
                            {
                                "id": "tests_pass",
                                "stage": ["candidate", "pre-commit", "pre-push"],
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    (
                                        "from pathlib import Path; "
                                        "assert Path('README.md').read_text().startswith('# changed')"
                                    ),
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
        title="Execution task",
        intent="Exercise execution orchestration.",
        workflow="feature",
        gates=gates or ["tests_pass"],
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
                    "id": "tests_pass",
                    "stage": ["candidate", "pre-commit", "pre-push"],
                    "argv": [
                        sys.executable,
                        "-c",
                        (
                            "from pathlib import Path; "
                            "assert Path('README.md').read_text().startswith('# changed')"
                        ),
                    ],
                    "command": None,
                    "type": None,
                }
            ],
        }
    )
    status = TaskStatus.for_started_task(
        task=task,
        run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
        ledger_head_hash=locked["hash"],
    )
    save_status(status, paths)
    return paths


def _plan_file(tmp_repo: Path, *, task_id: str = "agos-01") -> Path:
    plan_path = tmp_repo / "execution-plan.yaml"
    plan_path.write_text(
        yaml.safe_dump(
            {
                "id": "execution-plan-01",
                "task_id": task_id,
                "max_parallel": 1,
                "requires_candidate_review": True,
                "subtasks": [
                    {
                        "id": "subtask-readme",
                        "title": "Update README",
                        "intent": "Change the README heading.",
                        "depends_on": [],
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return plan_path


def _ledger_types(paths) -> list[str]:
    return [json.loads(line)["type"] for line in paths.ledger.read_text(encoding="utf-8").splitlines()]


def _ledger_records(paths) -> list[dict]:
    return [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]


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


def _service(tmp_repo: Path, *, worker_adapters=None, orchestration_backends=None) -> ExecutionService:
    service = ExecutionService(
        repo_paths(tmp_repo),
        worktree_root=tmp_repo.parent / ".agos-worktrees" / "agos-01",
        worker_adapters=worker_adapters,
        orchestration_backends=orchestration_backends,
    )
    if worker_adapters is None:
        service.register_worker_adapter(LocalWorktreeWorkerAdapter(service.workspace_manager))
    return service


class _FlakyPollBackend:
    name = "flaky"

    def __init__(self) -> None:
        self.specs = []

    def run(self, spec):
        self.specs.append(spec)
        return OrchestratorRunHandle(backend=self.name, run_id=spec.run_id)

    def poll(self, handle):
        raise ValueError(f"remote run not ready: {handle.run_id}")

    def cancel(self, handle):  # pragma: no cover - not used in this test
        raise AssertionError

    def collect(self, handle):  # pragma: no cover - protocol compatibility
        raise AssertionError


class _PollThenLoseBackend:
    name = "unstable"

    def __init__(self) -> None:
        self.polls = 0

    def run(self, spec):
        return OrchestratorRunHandle(backend=self.name, run_id=spec.run_id)

    def poll(self, handle):
        self.polls += 1
        if self.polls == 1:
            return OrchestratorRunStatus(
                backend=self.name,
                run_id=handle.run_id,
                state="queued",
            )
        raise ValueError(f"lost remote run: {handle.run_id}")

    def cancel(self, handle):
        raise ValueError(f"lost remote run: {handle.run_id}")

    def collect(self, handle):  # pragma: no cover - protocol compatibility
        raise AssertionError


def _ready_candidate(tmp_repo: Path):
    paths = _active_task(tmp_repo)
    service = _service(tmp_repo)
    service.execute_plan(_plan_file(tmp_repo))
    workspace = Path(ExecutionStore(paths).read_workspace("subtask-readme").path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")
    candidate = service.submit_candidate("subtask-readme", summary="Update README heading.")
    service.test_candidate(candidate.id)
    return paths, service, candidate.id


def test_execute_plan_creates_workspaces_and_ledger_events(tmp_repo):
    paths = _active_task(tmp_repo)
    service = _service(tmp_repo)

    plan = service.execute_plan(_plan_file(tmp_repo))

    workspace = ExecutionStore(paths).read_workspace("subtask-readme")
    assert plan.subtasks[0].status == "workspace_ready"
    assert Path(workspace.path).resolve().is_relative_to((tmp_repo.parent / ".agos-worktrees").resolve())
    assert _ledger_types(paths)[-2:] == ["execution_plan_created", "subtask_workspace_created"]


def test_start_execution_run_preflights_native_worker_readiness(tmp_repo):
    _active_task(tmp_repo)
    service = _service(tmp_repo)
    start_calls: list[str] = []

    class _Adapter:
        name = "local_worktree"

        def __init__(self, manager):
            self.manager = manager

        def prepare(self, assignment):
            return self.manager.create_workspace(assignment.subtask)

        def health(self):
            return WorkerHealth(
                name=self.name,
                adapter="local_worktree",
                checks=[WorkerHealthCheck(name="local_workspace", state="failed", detail="disk full")],
            )

        def start(self, request):  # pragma: no cover - should not be called
            start_calls.append(request.subtask_id)
            raise AssertionError("start should not be reached")

        def export_candidate(self, handle):  # pragma: no cover - not used here
            raise AssertionError

    service.register_worker_adapter(_Adapter(service.workspace_manager))

    with pytest.raises(Exception, match="disk full"):
        service.start_execution_run(_plan_file(tmp_repo), run_id="execution-run-unready")

    assert start_calls == []


def test_orchestration_backend_start_persists_snapshot_before_first_poll_failure(tmp_repo):
    paths = _active_task(tmp_repo, orchestration={"backend": "flaky"})
    backend = _FlakyPollBackend()
    service = _service(tmp_repo, orchestration_backends={backend.name: backend})

    with pytest.raises(ValueError, match="remote run not ready"):
        service.start_execution_run(_plan_file(tmp_repo), run_id="execution-run-flaky")

    status_path = paths.current_task / "execution" / "runs" / "execution-run-flaky" / "status.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["backend"] == "flaky"
    assert payload["state"] == "queued"


def test_orchestration_backend_errors_are_not_reported_as_stale_or_cancelled(tmp_repo):
    _active_task(tmp_repo, orchestration={"backend": "unstable"})
    backend = _PollThenLoseBackend()
    service = _service(tmp_repo, orchestration_backends={backend.name: backend})
    service.start_execution_run(_plan_file(tmp_repo), run_id="execution-run-unstable")

    with pytest.raises(ValueError, match="lost remote run"):
        service.status_execution_run("execution-run-unstable")
    with pytest.raises(ValueError, match="lost remote run"):
        service.cancel_execution_run("execution-run-unstable")


def test_non_native_execution_run_writes_only_the_selected_backend_spec(tmp_repo):
    paths = _active_task(tmp_repo, orchestration={"backend": "unstable"})
    backend = _PollThenLoseBackend()
    service = _service(tmp_repo, orchestration_backends={backend.name: backend})

    service.start_execution_run(_plan_file(tmp_repo), run_id="execution-run-unstable")

    spec_paths = sorted(paths.orchestration_runs.glob("*.json"))
    assert [path.name for path in spec_paths] == ["execution-run-unstable.json"]
    payload = json.loads(spec_paths[0].read_text(encoding="utf-8"))
    assert payload["backend"] == "unstable"
    assert {node["backend"] for node in payload["nodes"]} == {"unstable"}


def test_execute_plan_routes_workspace_creation_through_worker_adapter(tmp_repo, monkeypatch):
    _active_task(tmp_repo)
    service = _service(tmp_repo)
    called: list[str] = []

    class _Adapter:
        name = "local_worktree"

        def __init__(self, manager):
            self.manager = manager

        def prepare(self, assignment):
            called.append(assignment.subtask.id)
            return self.manager.create_workspace(assignment.subtask)

        def export_candidate(self, handle):  # pragma: no cover - not used here
            raise AssertionError

    service.register_worker_adapter(_Adapter(service.workspace_manager))

    service.execute_plan(_plan_file(tmp_repo))

    assert called == ["subtask-readme"]


def test_submit_candidate_captures_scoped_patch_and_hash(tmp_repo):
    paths = _active_task(tmp_repo)
    service = _service(tmp_repo)
    service.execute_plan(_plan_file(tmp_repo))
    workspace = Path(ExecutionStore(paths).read_workspace("subtask-readme").path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")

    candidate = service.submit_candidate("subtask-readme", summary="Update README heading.")

    patch_bytes = ExecutionStore(paths).patch_path(candidate.patch_ref).read_bytes()
    assert candidate.patch_sha256
    assert b"# changed" in patch_bytes
    assert candidate.status == "proposed"
    assert _ledger_types(paths)[-1] == "candidate_patch_created"


def test_submit_candidate_routes_patch_export_through_worker_adapter(tmp_repo, monkeypatch):
    paths = _active_task(tmp_repo)
    service = _service(tmp_repo)
    service.execute_plan(_plan_file(tmp_repo))
    workspace = Path(ExecutionStore(paths).read_workspace("subtask-readme").path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")
    called: list[str] = []

    class _Adapter:
        name = "local_worktree"

        def __init__(self, manager):
            self.manager = manager

        def prepare(self, assignment):  # pragma: no cover - not used here
            raise AssertionError

        def export_candidate(self, handle):
            called.append(handle.metadata["workspace_path"])
            return {"patch_bytes": self.manager.capture_patch(Path(handle.metadata["workspace_path"]))}

    service.register_worker_adapter(_Adapter(service.workspace_manager))

    service.submit_candidate("subtask-readme", summary="Update README heading.")

    assert called == [str(workspace)]


def test_submit_candidate_reuses_prepared_worker_handle(tmp_repo):
    _active_task(tmp_repo)
    service = _service(tmp_repo)
    prepared_handles: list[dict[str, str]] = []
    exported_handles: list[dict[str, str]] = []

    class _Prepared:
        def __init__(self, binding, handle):
            self.binding = binding
            self.handle = handle

    class _Handle:
        def __init__(self, metadata):
            self.metadata = metadata

    class _Adapter:
        name = "local_worktree"

        def __init__(self, manager):
            self.manager = manager

        def prepare(self, assignment):
            prepared = self.manager.create_workspace(assignment.subtask)
            handle = _Handle(
                {
                    "workspace_path": prepared.path,
                    "workspace_ref": prepared.ref,
                    "prepared_marker": "kept",
                }
            )
            prepared_handles.append(dict(handle.metadata))
            return _Prepared(prepared, handle)

        def export_candidate(self, handle):
            exported_handles.append(dict(handle.metadata))
            return {"patch_bytes": self.manager.capture_patch(Path(handle.metadata["workspace_path"]))}

    service = _service(tmp_repo, worker_adapters={"local_worktree": _Adapter(service.workspace_manager)})
    service.execute_plan(_plan_file(tmp_repo))
    workspace = Path(ExecutionStore(service.paths).read_workspace("subtask-readme").path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")

    service.submit_candidate("subtask-readme", summary="Update README heading.")

    assert prepared_handles and exported_handles
    assert ExecutionStore(service.paths).read_workspace("subtask-readme").worker_handle_metadata[
        "prepared_marker"
    ] == "kept"
    assert exported_handles[0]["prepared_marker"] == "kept"


def test_submit_candidate_keeps_canonical_workspace_identity(tmp_repo):
    _active_task(tmp_repo)
    prepared_handles: list[dict[str, str]] = []
    exported_handles: list[dict[str, str]] = []

    class _Prepared:
        def __init__(self, binding, handle):
            self.binding = binding
            self.handle = handle

    class _Handle:
        def __init__(self, metadata):
            self.metadata = metadata

    class _Adapter:
        name = "local_worktree"

        def __init__(self, manager):
            self.manager = manager

        def prepare(self, assignment):
            prepared = self.manager.create_workspace(assignment.subtask)
            handle = _Handle(
                {
                    "workspace_path": "override/path",
                    "workspace_ref": "override/ref",
                    "prepared_marker": "kept",
                }
            )
            prepared_handles.append(dict(handle.metadata))
            return _Prepared(prepared, handle)

        def export_candidate(self, handle):
            exported_handles.append(dict(handle.metadata))
            return {"patch_bytes": self.manager.capture_patch(Path(handle.metadata["workspace_path"]))}

    manager_service = _service(tmp_repo)
    service = _service(
        tmp_repo,
        worker_adapters={"local_worktree": _Adapter(manager_service.workspace_manager)},
    )
    service.execute_plan(_plan_file(tmp_repo))
    workspace = ExecutionStore(service.paths).read_workspace("subtask-readme")
    (Path(workspace.path) / "README.md").write_text("# changed\n", encoding="utf-8")
    service.submit_candidate("subtask-readme", summary="Update README heading.")

    assert prepared_handles and exported_handles
    assert exported_handles[0]["workspace_path"] == workspace.path
    assert exported_handles[0]["workspace_ref"] == workspace.ref
    assert exported_handles[0]["prepared_marker"] == "kept"


def test_accepted_decision_rejects_stale_completed_review_binding(tmp_repo):
    paths, service, candidate_id = _ready_candidate(tmp_repo)
    _packet_ref, packet = service.review_candidate(candidate_id)
    service.ingest_candidate_review(candidate_id, packet.review_id, findings=[])

    candidate = ExecutionStore(paths).read_candidate(candidate_id)
    stale_binding = candidate.review_refs[-1].model_copy(update={"test_refs": ["execution/tests/stale.json"]})
    ExecutionStore(paths).write_candidate(candidate.model_copy(update={"review_refs": [stale_binding]}))

    with pytest.raises(ValueError, match="stale"):
        service.decide_candidate(
            candidate_id,
            decision="accepted",
            reason="Stale review should not be accepted.",
        )


def test_rejected_decision_allows_blocking_candidate_review(tmp_repo):
    _paths, service, candidate_id = _ready_candidate(tmp_repo)
    _packet_ref, packet = service.review_candidate(candidate_id)
    blocker = Finding(
        id="finding-blocker",
        review_id=packet.review_id,
        source_agent="reviewer-a",
        category="correctness",
        severity="high",
        blocking=True,
        title="Blocking issue",
        body="This review has an open blocking finding.",
    )
    service.ingest_candidate_review(candidate_id, packet.review_id, findings=[blocker])

    decision = service.decide_candidate(
        candidate_id,
        decision="rejected",
        reason="The blocking review should still allow an explicit rejection.",
    )

    assert decision.decision == "rejected"
    assert ExecutionStore(service.paths).read_candidate(candidate_id).status == "rejected"


def test_candidate_happy_path_reviews_decides_and_applies(tmp_repo):
    paths, service, candidate_id = _ready_candidate(tmp_repo)
    for record in _ledger_records(paths):
        if record["type"] in {"candidate_test_started", "candidate_test_completed"}:
            assert record["task_id"] == "agos-01"

    packet_ref, packet = service.review_candidate(candidate_id)
    assert packet_ref.startswith("reviews/")
    assert packet.subject["type"] == "candidate"
    assert packet.subject["candidate_id"] == candidate_id
    report_ref, report = service.ingest_candidate_review(candidate_id, packet.review_id, findings=[])
    decision = service.decide_candidate(
        candidate_id,
        decision="accepted",
        reason="Patch applies, tests pass, and candidate review is clean.",
    )

    applied = service.apply_candidate(candidate_id)

    assert report_ref == f"reviews/{packet.review_id}/findings.json"
    assert report.open_blocking_findings() == []
    assert decision.decision == "accepted"
    assert applied.status == "applied"
    assert (tmp_repo / "README.md").read_text(encoding="utf-8") == "# changed\n"
    assert "candidate_review_completed" in _ledger_types(paths)
    assert _ledger_types(paths)[-1] == "candidate_applied"


def test_review_candidate_routes_packet_through_review_arbiter(tmp_repo, monkeypatch):
    paths, service, candidate_id = _ready_candidate(tmp_repo)
    ordered: list[str] = []

    class _ReviewArbiter:
        name = "deterministic_review"

        def decide(self, snapshot):
            ordered.extend(finding.id for finding in snapshot.findings)
            return tuple(reversed(snapshot.findings))

    monkeypatch.setattr(service, "review_arbiter", _ReviewArbiter())

    packet_ref, packet = service.review_candidate(candidate_id)
    findings = [
        Finding(
            id="finding-a",
            review_id=packet.review_id,
            source_agent="reviewer-a",
            category="test",
            severity="low",
            blocking=False,
            title="First",
            body="First body",
        ),
        Finding(
            id="finding-b",
            review_id=packet.review_id,
            source_agent="reviewer-b",
            category="test",
            severity="high",
            blocking=True,
            title="Second",
            body="Second body",
        ),
    ]
    report_ref, report = service.ingest_candidate_review(candidate_id, packet.review_id, findings=findings)

    assert packet_ref.startswith("reviews/")
    assert report_ref == f"reviews/{packet.review_id}/findings.json"
    assert ordered == ["finding-a", "finding-b"]
    assert [finding.id for finding in report.findings] == ["finding-b", "finding-a"]


def test_task_level_review_does_not_satisfy_candidate_review_guard(tmp_repo):
    paths, service, candidate_id = _ready_candidate(tmp_repo)
    review_service = ReviewService(paths)
    _packet_ref, packet = review_service.create_packet(diff_kind="governed_repo_diff")
    review_service.ingest_findings(packet.review_id, [])

    with pytest.raises(ValueError, match="candidate-bound review"):
        service.decide_candidate(
            candidate_id,
            decision="accepted",
            reason="Global review should not count.",
        )


def test_latest_completed_candidate_review_uses_ledger_order(tmp_repo):
    _paths, service, candidate_id = _ready_candidate(tmp_repo)
    _first_packet_ref, first_packet = service.review_candidate(candidate_id)
    _second_packet_ref, second_packet = service.review_candidate(candidate_id)
    service.ingest_candidate_review(candidate_id, second_packet.review_id, findings=[])
    blocker = Finding(
        id="finding-01",
        review_id=first_packet.review_id,
        source_agent="security_reviewer",
        category="security",
        severity="high",
        blocking=True,
        title="Blocking risk",
        body="The later-completed review found a blocking issue.",
    )
    service.ingest_candidate_review(candidate_id, first_packet.review_id, findings=[blocker])

    with pytest.raises(ValueError, match="open blocking findings"):
        service.decide_candidate(
            candidate_id,
            decision="accepted",
            reason="Clean earlier completion should not override later blocker.",
        )


def test_failed_candidate_review_ingest_marks_binding_failed(tmp_repo):
    paths, service, candidate_id = _ready_candidate(tmp_repo)
    _packet_ref, packet = service.review_candidate(candidate_id)
    closed_finding = Finding(
        id="finding-01",
        review_id=packet.review_id,
        source_agent="test_reviewer",
        category="test",
        severity="medium",
        blocking=True,
        title="Closed finding",
        body="This should not be accepted during ingest.",
        status="false_positive",
        resolution=FindingResolution(
            status="false_positive",
            rationale="Reviewer closed it before ingest.",
        ),
    )

    with pytest.raises(ValueError, match="ingested findings must be open"):
        service.ingest_candidate_review(candidate_id, packet.review_id, findings=[closed_finding])

    candidate = ExecutionStore(service.paths).read_candidate(candidate_id)
    assert candidate.review_refs[-1].state == "failed"
    assert _ledger_types(paths)[-1] == "candidate_review_failed"
    assert _ledger_records(paths)[-1]["task_id"] == "agos-01"


def test_accepted_decision_rejects_missing_candidate_review_report(tmp_repo):
    paths, service, candidate_id = _ready_candidate(tmp_repo)
    _packet_ref, packet = service.review_candidate(candidate_id)
    service.ingest_candidate_review(candidate_id, packet.review_id, findings=[])
    (paths.reviews / packet.review_id / "findings.json").unlink()

    with pytest.raises(ValueError, match="candidate-bound review report not found"):
        service.decide_candidate(
            candidate_id,
            decision="accepted",
            reason="Missing report should not count.",
        )


def test_apply_records_blocked_when_patch_no_longer_applies(tmp_repo):
    paths, service, candidate_id = _ready_candidate(tmp_repo)
    packet_ref, packet = service.review_candidate(candidate_id)
    assert packet_ref
    service.ingest_candidate_review(candidate_id, packet.review_id, findings=[])
    service.decide_candidate(
        candidate_id,
        decision="accepted",
        reason="Patch applies, tests pass, and candidate review is clean.",
    )
    (tmp_repo / "README.md").write_text("# other\n", encoding="utf-8")
    _commit(tmp_repo, "conflict")

    with pytest.raises(ValueError, match="does not apply"):
        service.apply_candidate(candidate_id)

    assert _ledger_types(paths)[-1] == "candidate_apply_blocked"
