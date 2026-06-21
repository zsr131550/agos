from __future__ import annotations

import json

from typer.testing import CliRunner

from agos.cli.main import app
from tests.cli.test_review import _active_task


runner = CliRunner()


def test_resolve_requires_evidence_for_resolved_findings(monkeypatch, tmp_repo):
    _create_blocking_finding(monkeypatch, tmp_repo)

    result = runner.invoke(
        app,
        ["resolve", "finding-01", "--status", "resolved", "--rationale", "Fixed"],
    )

    assert result.exit_code == 1
    assert "evidence" in result.stderr


def test_resolve_finding_updates_status_and_prints_result(monkeypatch, tmp_repo):
    _create_blocking_finding(monkeypatch, tmp_repo)

    result = runner.invoke(
        app,
        [
            "resolve",
            "finding-01",
            "--status",
            "resolved",
            "--evidence",
            "gates/tests_pass.log",
            "--rationale",
            "Regression test added.",
        ],
    )

    assert result.exit_code == 0
    assert "finding-01 resolved" in result.stdout


def _create_blocking_finding(monkeypatch, tmp_repo) -> None:
    _active_task(tmp_repo)
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
