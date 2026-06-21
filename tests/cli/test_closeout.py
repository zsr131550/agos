from __future__ import annotations

import json

from typer.testing import CliRunner

from agos.cli.main import app
from tests.cli.test_review import _active_task
from tests.cli.test_resolve import _create_blocking_finding


runner = CliRunner()


def _ledger_records(tmp_repo):
    ledger_path = tmp_repo / ".agos" / "tasks" / "current" / "ledger.jsonl"
    return [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


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


def test_closeout_requires_completed_review_report(monkeypatch, tmp_repo):
    _active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["closeout"])

    assert result.exit_code == 1
    assert "review report" in result.stderr
    assert not (tmp_repo / ".agos" / "tasks" / "current" / "proof.json").exists()
    assert not (tmp_repo / ".agos" / "tasks" / "current" / "proof.md").exists()


def test_closeout_proof_includes_gate_refs(monkeypatch, tmp_repo):
    _create_blocking_finding(monkeypatch, tmp_repo)
    gate_dir = tmp_repo / ".agos" / "tasks" / "current" / "evidence" / "gates"
    gate_dir.mkdir(parents=True)
    (gate_dir / "tests_pass-20260622.log").write_text("ok\n", encoding="utf-8")
    resolve_result = runner.invoke(
        app,
        [
            "resolve",
            "finding-01",
            "--status",
            "resolved",
            "--evidence",
            "gates/tests_pass-20260622.log",
            "--rationale",
            "Regression test added.",
        ],
    )
    assert resolve_result.exit_code == 0

    result = runner.invoke(app, ["closeout"])

    assert result.exit_code == 0
    proof = json.loads(
        (tmp_repo / ".agos" / "tasks" / "current" / "proof.json").read_text(encoding="utf-8")
    )
    assert proof["gate_refs"] == {"tests_pass": "gates/tests_pass-20260622.log"}


def test_second_closeout_rejects_done_task_without_second_ledger_event(monkeypatch, tmp_repo):
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
    first_result = runner.invoke(app, ["closeout"])
    assert first_result.exit_code == 0
    ledger_records = _ledger_records(tmp_repo)
    assert [record["type"] for record in ledger_records].count("closeout_completed") == 1
    proof_json = tmp_repo / ".agos" / "tasks" / "current" / "proof.json"
    proof_before = proof_json.read_text(encoding="utf-8")

    second_result = runner.invoke(app, ["closeout"])

    assert second_result.exit_code == 1
    assert "task is already done" in second_result.stderr
    assert proof_json.read_text(encoding="utf-8") == proof_before
    ledger_records_after = _ledger_records(tmp_repo)
    assert ledger_records_after == ledger_records
