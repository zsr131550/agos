from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agos.core.adapter import ExecutorRun
from agos.core.execution_service import ExecutionService
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.review import Finding, FindingResolution
from agos.core.review_service import ReviewService
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


def _active_task(tmp_repo: Path, *, gates: list[str] | None = None):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "default_workflow": "feature",
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


def _service(tmp_repo: Path) -> ExecutionService:
    return ExecutionService(
        repo_paths(tmp_repo),
        worktree_root=tmp_repo.parent / ".agos-worktrees" / "agos-01",
    )


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


def test_candidate_happy_path_reviews_decides_and_applies(tmp_repo):
    paths, service, candidate_id = _ready_candidate(tmp_repo)

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


def test_failed_candidate_review_ingest_marks_binding_failed(tmp_repo):
    _paths, service, candidate_id = _ready_candidate(tmp_repo)
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
