from __future__ import annotations

import yaml
from typer.testing import CliRunner

from agos.cli.main import app

runner = CliRunner()


def test_init_creates_layout_config_and_hooks(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.validate_multica_environment", lambda _executor: [])

    result = runner.invoke(app, ["init", "--executor", "multica", "--agent", "Lambda"])

    assert result.exit_code == 0
    assert (tmp_repo / ".agos" / "agos.yaml").exists()
    assert (tmp_repo / ".agos" / "repo_ledger.jsonl").exists()
    assert (tmp_repo / ".git" / "hooks" / "pre-commit").exists()
    assert (tmp_repo / ".git" / "hooks" / "pre-push").exists()

    config = yaml.safe_load((tmp_repo / ".agos" / "agos.yaml").read_text(encoding="utf-8"))
    assert config["executor"]["name"] == "multica"
    assert config["executor"]["agent"] == "Lambda"
    assert config["default_workflow"] == "feature"


def test_init_does_not_hardfail_when_daemon_down(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.cmd_init.validate_multica_environment",
        lambda _executor: ["multica daemon status failed"],
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    assert "Warning: multica daemon status failed" in result.stderr
    assert (tmp_repo / ".agos" / "agos.yaml").exists()


def test_init_preserves_existing_hooks(monkeypatch, tmp_repo):
    hooks_dir = tmp_repo / ".git" / "hooks"
    pre_commit = hooks_dir / "pre-commit"
    pre_commit.write_text("#!/bin/sh\necho legacy\n", encoding="utf-8")
    pre_commit.chmod(0o755)

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.validate_multica_environment", lambda _executor: [])

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    backup = hooks_dir / "pre-commit.agos.original"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "#!/bin/sh\necho legacy\n"
    managed_hook = pre_commit.read_text(encoding="utf-8")
    assert "agos ci --local --stage pre-commit" in managed_hook
    assert "pre-commit.agos.original" in managed_hook
