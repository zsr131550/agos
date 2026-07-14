from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner
from typer.main import get_command

from agos.cli import cmd_merge_gate
from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.config import AGOSConfig, WorkflowConfig
from agos.core.ledger import Ledger
from agos.core.merge_gate import MergeGateResult
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.core.trust_anchor import (
    FileTrustAnchorStore,
    GitRefTrustAnchorStore,
    SignedFileTrustAnchorStore,
    publish_current_anchor,
)


runner = CliRunner()


def _write_active_task(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    AGOSConfig(
        executor={"name": "multica", "agent": "Lambda"},
        default_workflow="feature",
        workflows={"feature": WorkflowConfig(gates=[])},
    ).save(paths.agos_yaml)
    task = Task(
        id="agos-task-01",
        title="Merge gate CLI task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    record = Ledger(paths.ledger).append(
        {"type": "task_started", "task_id": task.id, "title": task.title}
    )
    Ledger(paths.ledger).append({"type": "gates_locked", "task_id": task.id, "gates": []})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-1"),
            ledger_head_hash=record["hash"],
        ),
        paths,
    )
    return paths


def test_merge_gate_json_output_passes(monkeypatch, tmp_repo: Path):
    _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["merge-gate", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert any(check["name"] == "ledger_chain" for check in payload["checks"])


def test_merge_gate_exits_nonzero_when_blocked(monkeypatch, tmp_repo: Path):
    paths = _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    lines = paths.ledger.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["title"] = "forged"
    lines[0] = json.dumps(record)
    paths.ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = runner.invoke(app, ["merge-gate"])

    assert result.exit_code == 1
    assert "blocked" in result.stderr
    assert "ledger_chain" in result.stderr


def test_merge_gate_require_anchor_file_backend_blocks_on_mismatch(monkeypatch, tmp_repo: Path):
    paths = _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.core.trust_anchor.git_head", lambda _root: "a" * 40)
    anchor_path = tmp_repo / ".agos" / "anchor.json"
    publish_current_anchor(paths, FileTrustAnchorStore(anchor_path), issuer="CI")
    Ledger(paths.ledger).append({"type": "checkpoint", "repo_head": "a" * 40})

    result = runner.invoke(
        app,
        [
            "merge-gate",
            "--json",
            "--require-anchor",
            "--anchor-backend",
            "file",
            "--anchor-path",
            str(anchor_path),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["passed"] is False
    assert any(check["name"] == "trust_anchor" and check["state"] == "block" for check in payload["checks"])


def test_merge_gate_require_anchor_requires_anchor_path(monkeypatch, tmp_repo: Path):
    _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["merge-gate", "--require-anchor", "--anchor-backend", "file"])

    assert result.exit_code == 1
    assert "--anchor-path is required" in result.stderr


def test_merge_gate_requires_base_and_head_together(monkeypatch, tmp_repo: Path):
    _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["merge-gate", "--base", "HEAD", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    check = next(check for check in payload["checks"] if check["name"] == "submitted_diff")
    assert check["state"] == "block"
    assert "both base_ref and head_ref" in "; ".join(check["details"])


def test_merge_gate_help_exposes_submitted_diff_refs():
    command = get_command(app).commands["merge-gate"]
    opts = {
        option
        for param in command.params
        for option in getattr(param, "opts", [])
    }

    assert "--base" in opts
    assert "--head" in opts
    assert "--allow-legacy-decisionless" in opts
    assert "--provenance-policy" in opts
    assert "--trusted-config" in opts


def test_merge_gate_passes_legacy_decisionless_option_to_verifier(monkeypatch, tmp_repo: Path):
    _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    captured = {}

    def fake_verify(_paths, **kwargs):
        captured.update(kwargs)
        return MergeGateResult(passed=True, checks=[])

    monkeypatch.setattr(cmd_merge_gate, "verify_merge_gate", fake_verify)

    result = runner.invoke(app, ["merge-gate", "--allow-legacy-decisionless"])

    assert result.exit_code == 0, result.stderr
    assert captured["allow_legacy_decisionless"] is True


def test_merge_gate_passes_provenance_and_trusted_config_options_to_verifier(
    monkeypatch,
    tmp_repo: Path,
):
    _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    captured = {}
    trusted_config = tmp_repo / "trusted" / "agos.yaml"

    def fake_verify(_paths, **kwargs):
        captured.update(kwargs)
        return MergeGateResult(passed=True, checks=[], provenance_state="disabled")

    monkeypatch.setattr(cmd_merge_gate, "verify_merge_gate", fake_verify)

    result = runner.invoke(
        app,
        [
            "merge-gate",
            "--provenance-policy",
            "disabled",
            "--trusted-config",
            str(trusted_config),
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert captured["provenance_policy"] == "disabled"
    assert captured["trusted_config_path"] == trusted_config


def test_merge_gate_passes_signed_file_store_to_verifier(monkeypatch, tmp_repo: Path):
    _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    captured = {}
    anchor_path = tmp_repo / "signed-anchor.json"

    def fake_verify(_paths, **kwargs):
        captured.update(kwargs)
        return MergeGateResult(passed=True, checks=[])

    monkeypatch.setattr(cmd_merge_gate, "verify_merge_gate", fake_verify)

    result = runner.invoke(
        app,
        [
            "merge-gate",
            "--anchor-backend",
            "signed-file",
            "--anchor-path",
            str(anchor_path),
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert isinstance(captured["signed_anchor_store"], SignedFileTrustAnchorStore)
    assert captured["anchor_store"] is None


def test_merge_gate_exits_nonzero_on_unexpected_error(monkeypatch, tmp_repo: Path):
    monkeypatch.setattr(cmd_merge_gate, "find_initialized_repo_root", lambda: tmp_repo)

    def fail_verify(*_args, **_kwargs):
        raise RuntimeError("merge gate backend unavailable")

    monkeypatch.setattr(cmd_merge_gate, "verify_merge_gate", fail_verify)

    result = runner.invoke(app, ["merge-gate"])

    assert result.exit_code == 1
    assert "merge gate backend unavailable" in result.stderr


def test_merge_gate_store_accepts_git_ref_backend(tmp_repo: Path):
    store = cmd_merge_gate._store("git-ref", tmp_repo, None)

    assert isinstance(store, GitRefTrustAnchorStore)


def test_merge_gate_store_accepts_signed_file_backend(tmp_repo: Path):
    store = cmd_merge_gate._store("signed-file", tmp_repo, tmp_repo / "anchor.json")

    assert isinstance(store, SignedFileTrustAnchorStore)


def test_merge_gate_store_rejects_unknown_backend(tmp_repo: Path):
    with pytest.raises(ValueError, match="unsupported anchor backend"):
        cmd_merge_gate._store("unknown", tmp_repo, None)
