from __future__ import annotations

import yaml
from typer.testing import CliRunner

from agos.cli.main import app

runner = CliRunner()


def test_init_creates_layout_config_and_hooks(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.validate_multica_environment", lambda _executor: [])
    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", lambda: ["Lambda"])

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


def test_init_lists_discovered_agents_and_requires_explicit_choice(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.cmd_init.discover_multica_agents",
        lambda: ["codex-gpt-5.4 xhigh", "glm-5.2"],
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "No default agent configured and --agent was not provided." in result.stderr
    assert "Available Multica agents:" in result.stderr
    assert "- codex-gpt-5.4 xhigh" in result.stderr
    assert "- glm-5.2" in result.stderr
    assert 'agos init --agent "codex-gpt-5.4 xhigh"' in result.stderr
    assert not (tmp_repo / ".agos" / "agos.yaml").exists()


def test_init_fails_when_agent_discovery_returns_no_candidates(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", lambda: [])

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "No available Multica agents were found in the current workspace." in result.stderr
    assert not (tmp_repo / ".agos" / "agos.yaml").exists()


def test_init_fails_when_agent_discovery_errors(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)

    def _fail():
        raise RuntimeError("multica agent list failed: daemon unavailable")

    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", _fail)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "Could not discover Multica agents for the current workspace:" in result.stderr
    assert "multica agent list failed: daemon unavailable" in result.stderr
    assert not (tmp_repo / ".agos" / "agos.yaml").exists()


def test_init_explicit_agent_survives_discovery_failure(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.validate_multica_environment", lambda _executor: [])

    def _fail():
        raise RuntimeError("multica agent list failed: daemon unavailable")

    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", _fail)

    result = runner.invoke(app, ["init", "--agent", "codex-gpt-5.4 xhigh"])

    assert result.exit_code == 0
    config = yaml.safe_load((tmp_repo / ".agos" / "agos.yaml").read_text(encoding="utf-8"))
    assert config["executor"]["agent"] == "codex-gpt-5.4 xhigh"


def test_init_explicit_agent_fails_when_not_in_discovered_candidates(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", lambda: ["glm-5.2"])

    result = runner.invoke(app, ["init", "--agent", "codex-gpt-5.4 xhigh"])

    assert result.exit_code == 1
    assert 'Configured agent "codex-gpt-5.4 xhigh" was not found in the current workspace.' in result.stderr
    assert "- glm-5.2" in result.stderr
    assert not (tmp_repo / ".agos" / "agos.yaml").exists()


def test_init_warns_but_keeps_explicit_agent_when_environment_check_fails(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", lambda: ["codex-gpt-5.4 xhigh"])
    monkeypatch.setattr(
        "agos.cli.cmd_init.validate_multica_environment",
        lambda _executor: ["multica daemon status failed"],
    )

    result = runner.invoke(app, ["init", "--agent", "codex-gpt-5.4 xhigh"])

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
    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", lambda: ["codex-gpt-5.4 xhigh"])

    result = runner.invoke(app, ["init", "--agent", "codex-gpt-5.4 xhigh"])

    assert result.exit_code == 0
    backup = hooks_dir / "pre-commit.agos.original"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "#!/bin/sh\necho legacy\n"
    managed_hook = pre_commit.read_text(encoding="utf-8")
    assert "agos ci --local --stage pre-commit" in managed_hook
    assert "pre-commit.agos.original" in managed_hook


def test_init_installed_pre_push_hook_does_not_forward_git_positional_args(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.validate_multica_environment", lambda _executor: [])
    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", lambda: ["codex-gpt-5.4 xhigh"])

    result = runner.invoke(app, ["init", "--agent", "codex-gpt-5.4 xhigh"])

    assert result.exit_code == 0
    pre_push = (tmp_repo / ".git" / "hooks" / "pre-push").read_text(encoding="utf-8")
    assert 'agos ci --local --stage pre-push "$@"' not in pre_push
    assert "agos ci --local --stage pre-push" in pre_push
