from __future__ import annotations

import json
import subprocess

import yaml
from typer.testing import CliRunner

from agos.cli.main import app

runner = CliRunner()


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


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


def test_discover_multica_agents_filters_named_items(monkeypatch):
    import agos.cli.cmd_init as cmd_init

    payload = [
        {"name": "codex-gpt-5.4 xhigh"},
        {"name": ""},
        {"missing": "name"},
        {"name": "glm-5.2"},
    ]
    monkeypatch.setattr(cmd_init, "resolve_multica_bin", lambda: "multica")
    monkeypatch.setattr(cmd_init, "run_command", lambda *_args, **_kwargs: _Proc(stdout=json.dumps(payload)))

    assert cmd_init.discover_multica_agents() == ["codex-gpt-5.4 xhigh", "glm-5.2"]


def test_discover_local_agents_includes_multica_codex_and_claude(monkeypatch):
    import agos.cli.cmd_init as cmd_init

    monkeypatch.setattr(cmd_init, "discover_multica_agents", lambda: ["Lambda"])
    monkeypatch.setattr(
        cmd_init,
        "_resolve_cli_command",
        lambda command: {"codex": "codex.cmd", "claude": "claude.cmd"}.get(command),
    )

    candidates = cmd_init.discover_local_agents()

    assert [candidate.key for candidate in candidates] == [
        "multica:Lambda",
        "codex:codex",
        "claude:claude",
    ]
    assert [candidate.executor_name for candidate in candidates] == [
        "multica",
        "codex_cli",
        "claude_code",
    ]
    assert candidates[1].worker_config == {"type": "codex_cli", "command": "codex.cmd"}
    assert candidates[2].worker_config == {"type": "claude_code", "command": "claude.cmd"}


def test_discover_multica_agents_rejects_invalid_json(monkeypatch):
    import agos.cli.cmd_init as cmd_init

    monkeypatch.setattr(cmd_init, "resolve_multica_bin", lambda: "multica")
    monkeypatch.setattr(cmd_init, "run_command", lambda *_args, **_kwargs: _Proc(stdout="{not json"))

    try:
        cmd_init.discover_multica_agents()
    except RuntimeError as exc:
        assert "invalid JSON" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_validate_multica_environment_reports_failed_checks(monkeypatch):
    import agos.cli.cmd_init as cmd_init

    monkeypatch.setattr(cmd_init, "resolve_multica_bin", lambda: "multica")
    monkeypatch.setattr(cmd_init, "run_command", lambda *_args, **_kwargs: _Proc(returncode=1, stderr="down"))

    warnings = cmd_init.validate_multica_environment("multica")

    assert len(warnings) == 2
    assert all("down" in warning for warning in warnings)


def test_validate_multica_environment_reports_timeouts(monkeypatch):
    import agos.cli.cmd_init as cmd_init

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="multica daemon status", timeout=30)

    monkeypatch.setattr(cmd_init, "resolve_multica_bin", lambda: "multica")
    monkeypatch.setattr(cmd_init, "run_command", timeout)

    warnings = cmd_init.validate_multica_environment("multica")

    assert len(warnings) == 2
    assert all("timed out" in warning for warning in warnings)


def test_init_rejects_unsupported_executor(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)

    result = runner.invoke(app, ["init", "--executor", "other", "--agent", "Lambda"])

    assert result.exit_code != 0
    assert "Executor must be one of: multica, codex_cli, claude_code." in result.stderr


def test_init_lists_discovered_agents_and_requires_explicit_choice(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", lambda: ["Lambda"])
    monkeypatch.setattr(
        "agos.cli.cmd_init._resolve_cli_command",
        lambda command: {"codex": "codex.cmd", "claude": "claude.cmd"}.get(command),
    )

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "Available local agents:" in result.stdout
    assert "1. multica:Lambda" in result.stdout
    assert "2. codex:codex" in result.stdout
    assert "3. claude:claude" in result.stdout
    assert not (tmp_repo / ".agos" / "agos.yaml").exists()


def test_init_interactively_selects_executor_and_workers(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.validate_executor_environment", lambda _executor: [])
    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", lambda: ["Lambda"])
    monkeypatch.setattr(
        "agos.cli.cmd_init._resolve_cli_command",
        lambda command: {"codex": "codex.cmd", "claude": "claude.cmd"}.get(command),
    )

    result = runner.invoke(app, ["init"], input="2\n2,3\n")

    assert result.exit_code == 0, result.stderr
    config = yaml.safe_load((tmp_repo / ".agos" / "agos.yaml").read_text(encoding="utf-8"))
    assert config["executor"] == {"name": "codex_cli", "agent": "codex", "command": "codex.cmd"}
    assert config["workers"] == {
        "codex": {"type": "codex_cli", "command": "codex.cmd"},
        "claude": {"type": "claude_code", "command": "claude.cmd"},
    }


def test_init_fails_when_agent_discovery_returns_no_candidates(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", lambda: [])
    monkeypatch.setattr("agos.cli.cmd_init._resolve_cli_command", lambda _command: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "No local AGOS-compatible agents were found in the current workspace." in result.stderr
    assert not (tmp_repo / ".agos" / "agos.yaml").exists()


def test_init_fails_when_agent_discovery_errors(monkeypatch, tmp_repo):
    monkeypatch.chdir(tmp_repo)

    def _fail():
        raise RuntimeError("multica agent list failed: daemon unavailable")

    monkeypatch.setattr("agos.cli.cmd_init.discover_multica_agents", _fail)
    monkeypatch.setattr("agos.cli.cmd_init._resolve_cli_command", lambda _command: None)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 1
    assert "No local AGOS-compatible agents were found in the current workspace." in result.stderr
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
    monkeypatch.setattr("agos.cli.cmd_init._resolve_cli_command", lambda _command: None)

    result = runner.invoke(app, ["init", "--agent", "codex-gpt-5.4 xhigh"])

    assert result.exit_code == 1
    assert 'Configured agent "codex-gpt-5.4 xhigh" was not found in the current workspace.' in result.stderr
    assert "- multica:glm-5.2" in result.stderr
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
