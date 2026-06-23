from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


runner = CliRunner()


def _active_task(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    gate_argv = [
        sys.executable,
        "-c",
        "from pathlib import Path; assert Path('README.md').read_text().startswith('# changed')",
    ]
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "default_workflow": "feature",
                "reviewers": {
                    "tests": {
                        "type": "fake",
                        "role": "test_reviewer",
                        "required": True,
                    }
                },
                "workflows": {
                    "feature": {
                        "gates": [
                            {
                                "id": "tests_pass",
                                "stage": ["candidate", "pre-commit", "pre-push"],
                                "argv": gate_argv,
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
        title="Execution CLI task",
        workflow="feature",
        gates=["tests_pass"],
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
                    "argv": gate_argv,
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
    path = tmp_repo / "execution-plan.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "execution-plan-01",
                "task_id": task_id,
                "subtasks": [
                    {
                        "id": "subtask-readme",
                        "title": "Update README",
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def test_execute_plan_command_creates_workspace(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["execute-plan", "--plan", str(_plan_file(tmp_repo))])

    assert result.exit_code == 0
    assert "execution-plan-01" in result.stdout
    workspace = ExecutionStore(paths).read_workspace("subtask-readme")
    assert Path(workspace.path).exists()


def test_candidate_cli_full_closed_loop(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    runner.invoke(app, ["execute-plan", "--plan", str(_plan_file(tmp_repo))])
    store = ExecutionStore(paths)
    workspace = Path(store.read_workspace("subtask-readme").path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")

    submit = runner.invoke(
        app,
        ["candidate", "submit", "subtask-readme", "--summary", "Update README heading."],
    )
    assert submit.exit_code == 0
    candidate_id = submit.stdout.strip()
    assert candidate_id.startswith("candidate-")

    listing = runner.invoke(app, ["candidate", "list"])
    assert listing.exit_code == 0
    assert candidate_id in listing.stdout
    assert "proposed" in listing.stdout

    test = runner.invoke(app, ["candidate", "test", candidate_id])
    assert test.exit_code == 0
    assert "patch_applies: passed" in test.stdout
    assert "tests_pass: passed" in test.stdout

    packet = runner.invoke(app, ["candidate", "review", candidate_id, "--packet-only"])
    assert packet.exit_code == 0
    packet_ref = packet.stdout.strip()
    review_id = packet_ref.split("/")[1]
    findings_path = tmp_repo / "findings.json"
    findings_path.write_text(json.dumps({"findings": []}), encoding="utf-8")

    ingest = runner.invoke(
        app,
        [
            "candidate",
            "review",
            candidate_id,
            "--ingest",
            str(findings_path),
            "--review-id",
            review_id,
        ],
    )
    assert ingest.exit_code == 0
    assert f"reviews/{review_id}/findings.json" in ingest.stdout
    assert store.read_candidate(candidate_id).review_refs[-1].state == "completed"

    decide = runner.invoke(
        app,
        [
            "candidate",
            "decide",
            candidate_id,
            "--decision",
            "accepted",
            "--reason",
            "Patch applies, tests pass, and candidate review is clean.",
        ],
    )
    assert decide.exit_code == 0
    assert "accepted" in decide.stdout

    apply = runner.invoke(app, ["candidate", "apply", candidate_id])
    assert apply.exit_code == 0
    assert f"{candidate_id} applied" in apply.stdout
    assert (tmp_repo / "README.md").read_text(encoding="utf-8") == "# changed\n"


def test_candidate_review_uses_configured_reviewers(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    runner.invoke(app, ["execute-plan", "--plan", str(_plan_file(tmp_repo))])
    store = ExecutionStore(paths)
    workspace = Path(store.read_workspace("subtask-readme").path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")

    submit = runner.invoke(
        app,
        ["candidate", "submit", "subtask-readme", "--summary", "Update README heading."],
    )
    candidate_id = submit.stdout.strip()
    assert runner.invoke(app, ["candidate", "test", candidate_id]).exit_code == 0

    result = runner.invoke(app, ["candidate", "review", candidate_id])

    assert result.exit_code == 0, result.stderr
    assert "reviews/review-" in result.stdout
    assert "findings.json" in result.stdout
    candidate = store.read_candidate(candidate_id)
    assert candidate.status == "reviewed"
    assert candidate.review_refs[-1].state == "completed"
    assert candidate.review_refs[-1].report_ref is not None


def test_candidate_review_with_configured_manual_reviewer_creates_packet(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "default_workflow": "feature",
                "reviewers": {
                    "security": {
                        "type": "manual",
                        "role": "security_reviewer",
                        "required": True,
                    }
                },
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
    monkeypatch.chdir(tmp_repo)
    runner.invoke(app, ["execute-plan", "--plan", str(_plan_file(tmp_repo))])
    store = ExecutionStore(paths)
    workspace = Path(store.read_workspace("subtask-readme").path)
    (workspace / "README.md").write_text("# changed\n", encoding="utf-8")
    submit = runner.invoke(
        app,
        ["candidate", "submit", "subtask-readme", "--summary", "Update README heading."],
    )
    candidate_id = submit.stdout.strip()
    assert runner.invoke(app, ["candidate", "test", candidate_id]).exit_code == 0

    result = runner.invoke(app, ["candidate", "review", candidate_id])

    assert result.exit_code == 0, result.stderr
    packet_ref = result.stdout.strip()
    assert packet_ref.startswith("reviews/review-")
    assert packet_ref.endswith("/packet.json")
    candidate = store.read_candidate(candidate_id)
    assert candidate.status == "reviewing"
    assert candidate.review_refs[-1].state == "started"
    assert candidate.review_refs[-1].report_ref is None


def test_execute_plan_command_reports_task_mismatch(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["execute-plan", "--plan", str(_plan_file(tmp_repo, task_id="other"))])

    assert result.exit_code == 1
    assert "does not match active task" in result.stderr
