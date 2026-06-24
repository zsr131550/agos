from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from agos.cli import cmd_anchor
from agos.cli.main import app
from agos.core.adapter import ExecutorRun
from agos.core.config import default_config
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.core.trust_anchor import GitRefTrustAnchorStore


runner = CliRunner()


def _write_active_task(tmp_repo: Path) -> object:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(default_config(agent="Lambda").model_dump(mode="python"), sort_keys=False),
        encoding="utf-8",
    )
    task = Task(
        id="agos-task-01",
        title="Trust anchor CLI task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    record = Ledger(paths.ledger).append(
        {"type": "task_started", "task_id": task.id, "title": task.title}
    )
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-1"),
            ledger_head_hash=record["hash"],
        ),
        paths,
    )
    return paths


def test_anchor_publish_and_verify_file_backend_json(monkeypatch, tmp_repo: Path):
    _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    anchor_path = tmp_repo / ".agos" / "anchor.json"

    publish = runner.invoke(
        app,
        ["anchor", "publish", "--backend", "file", "--path", str(anchor_path), "--issuer", "CI"],
    )
    assert publish.exit_code == 0, publish.stderr
    assert "published trust anchor" in publish.stdout

    verify = runner.invoke(
        app,
        ["anchor", "verify", "--backend", "file", "--path", str(anchor_path), "--json"],
    )
    assert verify.exit_code == 0, verify.stderr
    payload = json.loads(verify.stdout)
    assert payload["passed"] is True
    assert payload["anchor"]["task_id"] == "agos-task-01"

    human = runner.invoke(
        app,
        ["anchor", "verify", "--backend", "file", "--path", str(anchor_path)],
    )
    assert human.exit_code == 0
    assert "trust anchor verified" in human.stdout


def test_anchor_verify_missing_file_exits_nonzero(monkeypatch, tmp_repo: Path):
    _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(
        app,
        ["anchor", "verify", "--backend", "file", "--path", str(tmp_repo / "missing.json")],
    )

    assert result.exit_code == 1
    assert "anchor not found" in result.stderr


def test_anchor_publish_requires_file_path(monkeypatch, tmp_repo: Path):
    _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["anchor", "publish", "--backend", "file", "--issuer", "CI"])

    assert result.exit_code == 1
    assert "--path is required" in result.stderr


def test_anchor_verify_mismatch_human_output(monkeypatch, tmp_repo: Path):
    paths = _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    anchor_path = tmp_repo / ".agos" / "anchor.json"
    runner.invoke(app, ["anchor", "publish", "--backend", "file", "--path", str(anchor_path), "--issuer", "CI"])
    Ledger(paths.ledger).append({"type": "checkpoint", "repo_head": "a" * 40})

    result = runner.invoke(app, ["anchor", "verify", "--backend", "file", "--path", str(anchor_path)])

    assert result.exit_code == 1
    assert "ledger head mismatch" in result.stderr


def test_anchor_verify_exits_nonzero_on_unexpected_error(monkeypatch, tmp_repo: Path):
    monkeypatch.setattr(cmd_anchor, "find_initialized_repo_root", lambda: tmp_repo)

    def fail_verify(*_args, **_kwargs):
        raise RuntimeError("anchor backend unavailable")

    monkeypatch.setattr(cmd_anchor, "verify_current_anchor", fail_verify)

    result = runner.invoke(app, ["anchor", "verify", "--backend", "git-ref"])

    assert result.exit_code == 1
    assert "anchor backend unavailable" in result.stderr


def test_anchor_store_accepts_git_ref_backend(tmp_repo: Path):
    store = cmd_anchor._store("git-ref", tmp_repo, None)

    assert isinstance(store, GitRefTrustAnchorStore)


def test_anchor_store_rejects_unknown_backend(tmp_repo: Path):
    with pytest.raises(ValueError, match="unsupported anchor backend"):
        cmd_anchor._store("unknown", tmp_repo, None)
