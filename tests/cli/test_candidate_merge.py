from __future__ import annotations

import json
import subprocess
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


def test_candidate_merge_decide_and_apply_non_overlapping_bundle(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    plan = _plan_file(tmp_repo)
    assert runner.invoke(app, ["execute-plan", "--plan", str(plan)]).exit_code == 0

    store = ExecutionStore(paths)
    readme_workspace = Path(store.read_workspace("subtask-readme").path)
    docs_workspace = Path(store.read_workspace("subtask-docs").path)
    (readme_workspace / "README.md").write_text("# changed\n", encoding="utf-8")
    (docs_workspace / "docs").mkdir(exist_ok=True)
    (docs_workspace / "docs" / "guide.md").write_text("# guide\n", encoding="utf-8")

    first = _accepted_candidate("subtask-readme", "Update README", store)
    second = _accepted_candidate("subtask-docs", "Add docs guide", store)

    decide = runner.invoke(app, ["candidate", "merge", "decide", first, second])

    assert decide.exit_code == 0
    assert "non_overlapping_bundle" in decide.stdout
    bundle_id = decide.stdout.split()[0]

    apply = runner.invoke(app, ["candidate", "merge", "apply", bundle_id])

    assert apply.exit_code == 0
    assert first in apply.stdout
    assert second in apply.stdout
    assert (tmp_repo / "README.md").read_text(encoding="utf-8") == "# changed\n"
    assert (tmp_repo / "docs" / "guide.md").read_text(encoding="utf-8") == "# guide\n"
    assert store.read_candidate(first).status == "applied"
    assert store.read_candidate(second).status == "applied"


def _accepted_candidate(subtask_id: str, summary: str, store: ExecutionStore) -> str:
    submit = runner.invoke(app, ["candidate", "submit", subtask_id, "--summary", summary])
    assert submit.exit_code == 0
    candidate_id = submit.stdout.strip()

    test = runner.invoke(app, ["candidate", "test", candidate_id])
    assert test.exit_code == 0
    assert "patch_applies: passed" in test.stdout

    packet = runner.invoke(app, ["candidate", "review", candidate_id, "--packet-only"])
    assert packet.exit_code == 0
    review_id = packet.stdout.strip().split("/")[1]
    findings_path = store.paths.root / f"{candidate_id}-findings.json"
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

    decide = runner.invoke(
        app,
        [
            "candidate",
            "decide",
            candidate_id,
            "--decision",
            "accepted",
            "--reason",
            "Patch applies and candidate review is clean.",
        ],
    )
    assert decide.exit_code == 0
    return candidate_id


def _active_task(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "default_workflow": "feature",
                "workflows": {"feature": {"gates": []}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task = Task(
        id="agos-01",
        title="Execution CLI task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    status = TaskStatus.for_started_task(
        task=task,
        run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
        ledger_head_hash=started["hash"],
    )
    save_status(status, paths)
    return paths


def _plan_file(tmp_repo: Path) -> Path:
    path = tmp_repo / "execution-plan.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "execution-plan-01",
                "task_id": "agos-01",
                "subtasks": [
                    {
                        "id": "subtask-readme",
                        "title": "Update README",
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    },
                    {
                        "id": "subtask-docs",
                        "title": "Add docs guide",
                        "write_scope": ["docs/guide.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path



def test_candidate_merge_apply_ordered_patch_stack(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    (tmp_repo / "README.md").write_text(
        "# t\nalpha\none\ntwo\nthree\nfour\nbeta\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "README.md"], cwd=tmp_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "expand readme"], cwd=tmp_repo, check=True)

    plan = tmp_repo / "execution-plan-overlap.yaml"
    plan.write_text(
        yaml.safe_dump(
            {
                "id": "execution-plan-overlap",
                "task_id": "agos-01",
                "subtasks": [
                    {
                        "id": "subtask-readme-a",
                        "title": "Update README A",
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    },
                    {
                        "id": "subtask-readme-b",
                        "title": "Update README B",
                        "depends_on": ["subtask-readme-a"],
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert runner.invoke(app, ["execute-plan", "--plan", str(plan)]).exit_code == 0

    store = ExecutionStore(paths)
    first_workspace = Path(store.read_workspace("subtask-readme-a").path)
    second_workspace = Path(store.read_workspace("subtask-readme-b").path)
    (first_workspace / "README.md").write_text("# t\nfirst\none\ntwo\nthree\nfour\nbeta\n", encoding="utf-8")
    (second_workspace / "README.md").write_text("# t\nalpha\none\ntwo\nthree\nfour\nsecond\n", encoding="utf-8")

    first = _accepted_candidate("subtask-readme-a", "Update README first", store)
    second = _accepted_candidate("subtask-readme-b", "Update README second", store)

    decide = runner.invoke(app, ["candidate", "merge", "decide", "--ordered", first, second])

    assert decide.exit_code == 0
    assert "ordered_patch_stack" in decide.stdout
    bundle_id = decide.stdout.split()[0]

    apply = runner.invoke(app, ["candidate", "merge", "apply", bundle_id])

    assert apply.exit_code == 0
    readme = (tmp_repo / "README.md").read_text(encoding="utf-8")
    assert "first" in readme
    assert "second" in readme
    assert store.read_candidate(first).status == "applied"
    assert store.read_candidate(second).status == "applied"
