from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.repo import repo_paths


runner = CliRunner()


def test_worker_doctor_json_reports_configured_workers(monkeypatch, tmp_repo):
    _write_config(
        tmp_repo,
        {
            "local_worktree": {"type": "local_worktree"},
        },
    )
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["worker", "doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["healthy"] is True
    assert payload["workers"][0]["name"] == "local_worktree"
    assert payload["workers"][0]["state"] == "healthy"


def test_worker_doctor_filters_single_worker(monkeypatch, tmp_repo):
    _write_config(
        tmp_repo,
        {
            "local_worktree": {"type": "local_worktree"},
            "codex-prod": {"type": "codex_cli", "command": "missing-codex"},
        },
    )
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["worker", "doctor", "--worker", "local_worktree", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [worker["name"] for worker in payload["workers"]] == ["local_worktree"]


def test_worker_doctor_uses_configured_local_worker_name(monkeypatch, tmp_repo):
    _write_config(
        tmp_repo,
        {
            "local-prod": {"type": "local_worktree"},
        },
    )
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["worker", "doctor", "--worker", "local-prod", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [worker["name"] for worker in payload["workers"]] == ["local-prod"]


def test_worker_doctor_returns_nonzero_for_unhealthy_worker(monkeypatch, tmp_repo):
    _write_config(
        tmp_repo,
        {
            "codex-prod": {"type": "codex_cli", "command": "missing-codex"},
        },
    )
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.adapters.workers._health.shutil.which", lambda _command: None)

    result = runner.invoke(app, ["worker", "doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["healthy"] is False
    assert payload["workers"][0]["name"] == "codex-prod"
    assert payload["workers"][0]["checks"][0]["state"] == "failed"


def test_worker_doctor_json_redacts_invalid_config_values(monkeypatch, tmp_repo):
    _write_raw_config(
        tmp_repo,
        {
            "executor": {"name": "multica", "agent": "Lambda"},
            "workers": {
                "codex-prod": {
                    "type": "codex_cli",
                    "token": ["top-secret-token"],
                    "env": {"SECRET_KEY": ["env-secret"]},
                },
            },
            "workflows": {},
        },
    )
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["worker", "doctor", "--json"])

    assert result.exit_code == 1
    assert "top-secret-token" not in result.stdout
    assert "top-secret-token" not in result.stderr
    assert "env-secret" not in result.stdout
    assert "env-secret" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["healthy"] is False
    assert payload["workers"] == []


def test_worker_doctor_reports_unknown_worker(monkeypatch, tmp_repo):
    _write_config(
        tmp_repo,
        {
            "local_worktree": {"type": "local_worktree"},
        },
    )
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["worker", "doctor", "--worker", "missing"])

    assert result.exit_code == 1
    assert "unknown worker: missing" in result.stderr


def _write_config(tmp_repo, workers: dict[str, dict[str, object]]) -> None:
    _write_raw_config(
        tmp_repo,
        {
            "executor": {"name": "multica", "agent": "Lambda"},
            "workers": workers,
            "workflows": {},
        },
    )


def _write_raw_config(tmp_repo, payload: dict[str, object]) -> None:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
