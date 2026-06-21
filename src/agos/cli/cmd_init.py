"""`agos init` command."""
from __future__ import annotations

import json
import subprocess
from importlib import resources
from pathlib import Path

import typer

from agos.adapters.multica import resolve_multica_bin
from agos.core.command import run_command
from agos.core.config import AGOSConfig
from agos.core.ledger import append_repo_record
from agos.core.repo import agos_dir, config_path, find_repo_root, repo_ledger_path


class InitAgentResolutionError(Exception):
    """Raised when `agos init` cannot resolve a valid agent choice."""


def discover_multica_agents() -> list[str]:
    """Return visible Multica agent names for the current workspace."""

    multica_bin = resolve_multica_bin()
    try:
        completed = run_command(
            [multica_bin, "agent", "list", "--output", "json"],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"multica agent list failed: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise RuntimeError(f"multica agent list failed: {detail}")

    try:
        payload = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"multica agent list returned invalid JSON: {exc.msg}") from exc

    if not isinstance(payload, list):
        raise RuntimeError("multica agent list returned an unexpected payload")

    return [
        item["name"]
        for item in payload
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"].strip()
    ]


def _render_agent_candidates(candidates: list[str]) -> str:
    return "\n".join(f"- {candidate}" for candidate in candidates)


def resolve_init_agent(agent: str | None) -> str:
    """Resolve the agent to write into AGOS config for `init`."""

    if agent is not None:
        try:
            candidates = discover_multica_agents()
        except RuntimeError:
            return agent
        if agent in candidates:
            return agent
        candidate_lines = _render_agent_candidates(candidates) if candidates else "- <none>"
        raise InitAgentResolutionError(
            f'Configured agent "{agent}" was not found in the current workspace.\n\n'
            f"Available Multica agents:\n{candidate_lines}"
        )

    try:
        candidates = discover_multica_agents()
    except RuntimeError as exc:
        raise InitAgentResolutionError(
            "No default agent configured and --agent was not provided.\n\n"
            "Could not discover Multica agents for the current workspace:\n"
            f"  {exc}\n\n"
            "Re-run with:\n"
            '  agos init --agent "<agent-name>"'
        ) from exc

    if not candidates:
        raise InitAgentResolutionError(
            "No default agent configured and --agent was not provided.\n\n"
            "No available Multica agents were found in the current workspace.\n"
            "Create or enable an agent in Multica, then re-run:\n"
            '  agos init --agent "<agent-name>"'
        )

    raise InitAgentResolutionError(
        "No default agent configured and --agent was not provided.\n\n"
        "Available Multica agents:\n"
        f"{_render_agent_candidates(candidates)}\n\n"
        "Re-run with:\n"
        f'  agos init --agent "{candidates[0]}"'
    )


def validate_multica_environment(executor: str) -> list[str]:
    """Return non-fatal warnings for missing multica setup."""

    if executor != "multica":
        return [f"Unsupported executor '{executor}'"]

    warnings: list[str] = []
    multica_bin = resolve_multica_bin()
    commands = [
        [multica_bin, "daemon", "status"],
        [multica_bin, "workspace", "list", "--output", "json"],
    ]
    for command in commands:
        try:
            completed = run_command(command, capture_output=True, text=True, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            completed = subprocess.CompletedProcess(command, 1, stdout="", stderr=str(exc))
        if completed.returncode != 0:
            display_command = "multica " + " ".join(command[1:3])
            detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
            warnings.append(f"{display_command} failed: {detail}")
    return warnings


def _render_template(template_name: str, *, stage: str, legacy_hook: str) -> str:
    template = resources.files("agos.hooks.templates").joinpath(template_name).read_text(encoding="utf-8")
    return template.replace("__STAGE__", stage).replace("__LEGACY_HOOK__", legacy_hook)


def _install_hook(git_hooks_dir: Path, *, stage: str) -> None:
    hook_path = git_hooks_dir / stage
    backup_name = f"{stage}.agos.original"
    backup_path = git_hooks_dir / backup_name

    if hook_path.exists():
        current_text = hook_path.read_text(encoding="utf-8")
        if "# Managed by AGOS" not in current_text and not backup_path.exists():
            hook_path.replace(backup_path)

    rendered = _render_template(f"{stage}.sh", stage=stage, legacy_hook=backup_name)
    hook_path.write_text(rendered, encoding="utf-8", newline="\n")
    hook_path.chmod(0o755)


def init_command(
    executor: str = typer.Option("multica", "--executor", help="Executor adapter to configure."),
    agent: str | None = typer.Option(None, "--agent", help="Default Multica agent name."),
) -> None:
    """Create `.agos/`, write config, and install git hooks."""

    if executor != "multica":
        raise typer.BadParameter("Only the 'multica' executor is supported in v0.1.")

    try:
        resolved_agent = resolve_init_agent(agent)
    except InitAgentResolutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    repo_root = find_repo_root()
    agos_root = agos_dir(repo_root)
    agos_root.mkdir(parents=True, exist_ok=True)
    (agos_root / "tasks" / "current").mkdir(parents=True, exist_ok=True)
    (agos_root / "hooks").mkdir(parents=True, exist_ok=True)

    config = AGOSConfig.default(executor=executor, agent=resolved_agent)
    config.save(config_path(repo_root))

    git_hooks_dir = repo_root / ".git" / "hooks"
    git_hooks_dir.mkdir(parents=True, exist_ok=True)
    for stage in ("pre-commit", "pre-push"):
        _install_hook(git_hooks_dir, stage=stage)

    append_repo_record(
        repo_ledger_path(repo_root),
        "repo_initialized",
        executor=executor,
        agent=resolved_agent,
        hooks=["pre-commit", "pre-push"],
    )

    for warning in validate_multica_environment(executor):
        typer.echo(f"Warning: {warning}", err=True)

    typer.echo(f"Initialized AGOS in {agos_root}")

