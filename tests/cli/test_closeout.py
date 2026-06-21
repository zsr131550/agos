from __future__ import annotations

import json

from typer.testing import CliRunner

from agos.cli.main import app
from tests.cli.test_resolve import _create_blocking_finding


runner = CliRunner()


def test_closeout_rejects_open_blocking_findings(monkeypatch, tmp_repo):
    _create_blocking_finding(monkeypatch, tmp_repo)

    result = runner.invoke(app, ["closeout"])

    assert result.exit_code == 1
    assert "open blocking findings" in result.stderr


def test_closeout_writes_proof_after_blocking_finding_resolved(monkeypatch, tmp_repo):
    _create_blocking_finding(monkeypatch, tmp_repo)

    resolve_result = runner.invoke(
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
    assert resolve_result.exit_code == 0

    result = runner.invoke(app, ["closeout"])

    assert result.exit_code == 0
    assert "proof.json written for agos-01" in result.stdout
    proof_json = tmp_repo / ".agos" / "tasks" / "current" / "proof.json"
    proof_md = tmp_repo / ".agos" / "tasks" / "current" / "proof.md"
    assert proof_json.exists()
    assert proof_md.exists()
    proof = json.loads(proof_json.read_text(encoding="utf-8"))
    assert proof["blocking_open_count"] == 0
