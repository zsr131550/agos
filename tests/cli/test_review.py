from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task


runner = CliRunner()


def _active_task(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "reviewers": {
                    "tests": {
                        "type": "fake",
                        "role": "test_reviewer",
                        "required": True,
                    }
                },
                "allow_fake_reviewer": True,
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    task = Task(
        id="agos-01",
        title="Review CLI task",
        intent="Expose review orchestration from the CLI",
        acceptance=["review packet refs are printed"],
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


def test_review_packet_only_writes_packet_and_prints_relative_ref(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["review", "--packet-only"])

    assert result.exit_code == 0
    packet_ref = result.stdout.strip()
    assert packet_ref.startswith("reviews/review-")
    assert packet_ref.endswith("/packet.json")
    assert not packet_ref.startswith(str(tmp_repo))
    assert (paths.current_task / packet_ref).exists()


def test_review_ingest_writes_report_and_prints_findings(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    packet_result = runner.invoke(app, ["review", "--packet-only"])
    review_id = packet_result.stdout.strip().split("/")[1]
    ingest_path = tmp_repo / "findings.json"
    ingest_path.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "id": "finding-01",
                        "review_id": "external-review-id",
                        "source_agent": "security_reviewer",
                        "category": "security",
                        "severity": "high",
                        "blocking": True,
                        "title": "Risk",
                        "body": "Risk body.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["review", "--ingest", str(ingest_path), "--review-id", review_id])

    assert result.exit_code == 0
    assert f"reviews/{review_id}/findings.json" in result.stdout
    assert "finding-01: Risk" in result.stdout
    assert (paths.reviews / review_id / "findings.json").exists()


def test_review_rejects_packet_only_with_ingest(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    ingest_path = tmp_repo / "findings.json"
    ingest_path.write_text(json.dumps({"findings": []}), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "review",
            "--packet-only",
            "--ingest",
            str(ingest_path),
            "--review-id",
            "review-conflicting",
        ],
    )

    assert result.exit_code == 2
    assert "--packet-only and --ingest cannot be used together" in result.stderr
    assert not (paths.reviews / "review-conflicting" / "findings.json").exists()


def test_review_run_uses_configured_reviewers_and_writes_report(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["review", "run"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "completed"
    assert payload["reviewers"] == ["tests"]
    assert payload["report_ref"] == f"reviews/{payload['review_id']}/findings.json"
    assert (paths.reviews / payload["review_id"] / "findings.json").exists()


def test_review_run_preserves_configured_manual_review_orchestration(monkeypatch, tmp_repo):
    paths = _active_task(tmp_repo)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "reviewers": {
                    "security": {
                        "type": "manual",
                        "role": "security_reviewer",
                        "required": True,
                    }
                },
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["review", "run"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["backend"] == "native_async"
    assert payload["kind"] == "review_run"
    assert payload["reviewers"] == ["security"]
    assert (paths.orchestration_runs / f"{payload['run_id']}.json").exists()
