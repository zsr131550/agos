from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from agos.cli.main import app


runner = CliRunner()


def test_doctor_json_reports_healthy_initialized_repo(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["healthy"] is True
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["git_repo"]["state"] == "passed"
    assert checks["agos_initialized"]["state"] == "passed"
    assert checks["config"]["state"] == "passed"
    assert checks["workers"]["state"] == "passed"
    assert checks["reviewers"]["state"] == "passed"
    assert checks["orchestration"]["state"] == "passed"


def test_doctor_human_reports_check_lines(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "[passed] git_repo" in result.stdout
    assert "[passed] config" in result.stdout


def test_doctor_json_fails_for_invalid_config(monkeypatch, tmp_repo):
    agos_dir = tmp_repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "agos.yaml").write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workflows": {
                    "feature": {
                        "gates": [
                            {
                                "id": "bad_gate",
                                "stage": ["pre-commit"],
                                "command": "pytest -q",
                                "argv": ["pytest", "-q"],
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

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["healthy"] is False
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["config"]["state"] == "failed"
    assert "invalid AGOS configuration" in checks["config"]["detail"]


def _write_config(tmp_repo) -> None:
    agos_dir = tmp_repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "agos.yaml").write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workers": {"local_worktree": {"type": "local_worktree"}},
                "reviewers": {"manual": {"type": "manual", "role": "security_reviewer"}},
                "orchestration": {"backend": "native_async", "max_parallel": 2},
                "workflows": {"feature": {"gates": []}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
