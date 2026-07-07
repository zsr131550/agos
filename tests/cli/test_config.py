from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agos.cli.main import app


runner = CliRunner()


def test_config_show_json_prints_validated_config(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["config", "show", "--json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["path"]) == tmp_repo / ".agos" / "agos.yaml"
    assert payload["config"]["executor"] == {"name": "multica", "agent": "Lambda"}
    assert payload["config"]["default_workflow"] == "feature"


def test_config_validate_reports_success(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["config", "validate"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "AGOS configuration valid"


def test_config_validate_json_reports_success(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["config", "validate", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "ok": True,
        "path": str(tmp_repo / ".agos" / "agos.yaml"),
    }


def test_config_validate_accepts_autonomous_loop_configuration(monkeypatch, tmp_repo):
    agos_dir = tmp_repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "agos.yaml").write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "codex_cli", "agent": "codex", "command": "codex"},
                "default_workflow": "feature",
                "workers": {
                    "codex_impl": {"type": "codex_cli", "command": "codex", "timeout_seconds": 900},
                    "claude_docs": {"type": "claude_code", "command": "claude", "timeout_seconds": 900},
                },
                "reviewers": {
                    "codex_review": {
                        "type": "codex_cli",
                        "executor": "codex_cli",
                        "command": "codex",
                        "role": "security_reviewer",
                        "required": True,
                    }
                },
                "orchestration": {
                    "backend": "native_async",
                    "max_parallel": 2,
                    "max_retries": 1,
                    "fallback_write_scope": ["README.md", "src/agos", "tests", "docs"],
                    "planner": {
                        "enabled": True,
                        "executor": "codex_cli",
                        "command": "codex",
                        "timeout_seconds": 60,
                    },
                },
                "workflows": {"feature": {"gates": []}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["config", "validate", "--json"])

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True


def test_config_validate_reports_invalid_config(monkeypatch, tmp_repo):
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

    result = runner.invoke(app, ["config", "validate"])

    assert result.exit_code == 2
    assert "invalid AGOS configuration" in result.stderr
    assert "bad_gate" in result.stderr


def _write_config(tmp_repo) -> None:
    agos_dir = tmp_repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "agos.yaml").write_text(
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
