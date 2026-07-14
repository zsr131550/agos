"""`agos init` command."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import typer

from agos.adapters.multica import resolve_multica_bin
from agos.cli.worker_registry import register_configured_worker_adapters
from agos.core.command import run_command
from agos.core.config import AGOSConfig, ExecutorConfig, ReviewerConfig, WorkerConfig
from agos.core.execution_service import ExecutionService
from agos.core.ledger import append_repo_record
from agos.core.repo import agos_dir, config_path, find_repo_root, repo_ledger_path, repo_paths
from agos.core.task_execution import TaskExecutionConfig


class InitAgentResolutionError(Exception):
    """Raised when `agos init` cannot resolve a valid agent choice."""


@dataclass(frozen=True)
class LocalAgentCandidate:
    """One local AGOS-compatible executor/worker candidate."""

    key: str
    provider: str
    name: str
    display_name: str
    executor_name: str
    executor_agent: str
    command: str | None
    worker_name: str
    worker_config: dict[str, str]

    @property
    def executor_config(self) -> ExecutorConfig:
        return ExecutorConfig(
            name=self.executor_name,
            agent=self.executor_agent,
            command=self.command,
        )


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


def _resolve_cli_command(command: str) -> str | None:
    for candidate in (f"{command}.cmd", f"{command}.exe", command):
        resolved = shutil.which(candidate)
        if resolved:
            return candidate if candidate.endswith((".cmd", ".exe")) else resolved
    return None


def discover_local_agents() -> list[LocalAgentCandidate]:
    """Return locally available executor/worker choices across supported providers."""

    candidates: list[LocalAgentCandidate] = []
    try:
        for agent in discover_multica_agents():
            candidates.append(
                LocalAgentCandidate(
                    key=f"multica:{agent}",
                    provider="multica",
                    name=agent,
                    display_name=f"multica:{agent}",
                    executor_name="multica",
                    executor_agent=agent,
                    command=None,
                    worker_name=f"multica_{_slug(agent)}",
                    worker_config={"type": "multica", "command": "multica", "agent": agent},
                )
            )
    except RuntimeError:
        pass

    codex_command = _resolve_cli_command("codex")
    if codex_command is not None:
        candidates.append(
            LocalAgentCandidate(
                key="codex:codex",
                provider="codex",
                name="codex",
                display_name="codex:codex",
                executor_name="codex_cli",
                executor_agent="codex",
                command=codex_command,
                worker_name="codex",
                worker_config={"type": "codex_cli", "command": codex_command},
            )
        )

    claude_command = _resolve_cli_command("claude")
    if claude_command is not None:
        candidates.append(
            LocalAgentCandidate(
                key="claude:claude",
                provider="claude",
                name="claude",
                display_name="claude:claude",
                executor_name="claude_code",
                executor_agent="claude",
                command=claude_command,
                worker_name="claude",
                worker_config={"type": "claude_code", "command": claude_command},
            )
        )
    return candidates


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return slug or "agent"


def _render_agent_candidates(candidates: list[LocalAgentCandidate]) -> str:
    return "\n".join(f"- {candidate.key}" for candidate in candidates)


def resolve_init_agent(agent: str | None) -> LocalAgentCandidate:
    """Resolve an explicit agent selection for `init`."""

    if agent is not None:
        candidates = discover_local_agents()
        for candidate in candidates:
            if agent in {candidate.key, candidate.name}:
                return candidate
        if ":" not in agent and not any(candidate.provider == "multica" for candidate in candidates):
            return LocalAgentCandidate(
                key=f"multica:{agent}",
                provider="multica",
                name=agent,
                display_name=f"multica:{agent}",
                executor_name="multica",
                executor_agent=agent,
                command=None,
                worker_name=f"multica_{_slug(agent)}",
                worker_config={"type": "multica", "command": "multica", "agent": agent},
            )
        candidate_lines = _render_agent_candidates(candidates) if candidates else "- <none>"
        raise InitAgentResolutionError(
            f'Configured agent "{agent}" was not found in the current workspace.\n\n'
            f"Available local agents:\n{candidate_lines}"
        )

    candidates = discover_local_agents()
    if not candidates:
        raise InitAgentResolutionError(
            "No default agent configured and --agent was not provided.\n\n"
            "No local AGOS-compatible agents were found in the current workspace.\n"
            "Install or enable Multica, Codex CLI, or Claude Code, then re-run:\n"
            '  agos init --agent "<agent-name>"'
        )

    raise InitAgentResolutionError(
        "No default agent configured and --agent was not provided.\n\n"
        "Available local agents:\n"
        f"{_render_agent_candidates(candidates)}\n\n"
        "Re-run with:\n"
        f'  agos init --agent "{candidates[0].key}"'
    )


def _select_interactive_executor(
    candidates: list[LocalAgentCandidate],
) -> LocalAgentCandidate:
    typer.echo("Available local agents:")
    for index, candidate in enumerate(candidates, start=1):
        typer.echo(f"{index}. {candidate.key}")

    selected_index = _prompt_index("Select main executor", candidates, default=1)
    return candidates[selected_index - 1]


def _prompt_task_intent() -> str:
    return str(typer.prompt("Task intent", default="")).strip()


def _prompt_task_title() -> str:
    title = str(typer.prompt("Task title")).strip()
    if not title:
        raise InitAgentResolutionError("task title must be non-empty")
    return title


def plan_workers_for_goal(
    selected_agent: LocalAgentCandidate,
    title: str,
    candidates: list[LocalAgentCandidate],
) -> list[LocalAgentCandidate]:
    """Ask the selected executor for a worker plan, with a deterministic fallback."""

    by_key = {candidate.key: candidate for candidate in candidates}
    planned_keys = _executor_worker_plan_keys(selected_agent, title, candidates)
    planned = [by_key[key] for key in planned_keys if key in by_key]
    if planned:
        return _dedupe_worker_candidates(planned)
    return _dedupe_worker_candidates([selected_agent, *candidates])


def _executor_worker_plan_keys(
    selected_agent: LocalAgentCandidate,
    title: str,
    candidates: list[LocalAgentCandidate],
) -> list[str]:
    args = _executor_planner_args(selected_agent, _worker_plan_prompt(title, candidates))
    if args is None:
        return []
    try:
        proc = run_command(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return _extract_worker_plan_keys(proc.stdout)


def _executor_planner_args(
    selected_agent: LocalAgentCandidate,
    prompt: str,
) -> list[str] | None:
    command = selected_agent.command
    if selected_agent.executor_name == "codex_cli":
        return [command or "codex", "exec", "--json", prompt]
    if selected_agent.executor_name == "claude_code":
        return [command or "claude", "-p", "--output-format", "json", prompt]
    return None


def _worker_plan_prompt(title: str, candidates: list[LocalAgentCandidate]) -> str:
    worker_lines = "\n".join(
        f"- {candidate.key}: executor={candidate.executor_name}, worker={candidate.worker_name}"
        for candidate in candidates
    )
    return (
        "You are the AGOS main executor. Analyze the task title and choose the workers "
        "AGOS should configure before the task starts.\n\n"
        f"Task title: {title}\n\n"
        f"Available workers:\n{worker_lines}\n\n"
        'Return JSON only, exactly like: {"workers":["codex:codex"]}. '
        "Use only keys from the available workers list."
    )


def _extract_worker_plan_keys(stdout: str) -> list[str]:
    direct = _worker_keys_from_json_text(stdout)
    if direct:
        return direct

    for line in reversed(stdout.splitlines()):
        keys = _worker_keys_from_json_text(line)
        if keys:
            return keys
    return []


def _worker_keys_from_json_text(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    payload = _load_json_object(text)
    if payload is None:
        json_text = _json_object_slice(text)
        if json_text is None:
            return []
        payload = _load_json_object(json_text)
    return _worker_keys_from_payload(payload)


def _load_json_object(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _json_object_slice(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _worker_keys_from_payload(payload: object | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    workers = payload.get("workers")
    if isinstance(workers, list):
        return [str(worker) for worker in workers if isinstance(worker, str) and worker.strip()]

    for field in ("result", "content", "message", "text"):
        value = payload.get(field)
        if isinstance(value, str):
            keys = _worker_keys_from_json_text(value)
            if keys:
                return keys

    item = payload.get("item")
    if isinstance(item, dict):
        return _worker_keys_from_payload(item)
    return []


def _dedupe_worker_candidates(candidates: list[LocalAgentCandidate]) -> list[LocalAgentCandidate]:
    planned: list[LocalAgentCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.worker_name in seen:
            continue
        planned.append(candidate)
        seen.add(candidate.worker_name)
    return planned


def _prompt_index(prompt: str, candidates: list[LocalAgentCandidate], *, default: int) -> int:
    raw = typer.prompt(prompt, default=str(default))
    try:
        value = int(str(raw).strip())
    except ValueError as exc:
        raise InitAgentResolutionError(f"invalid selection: {raw}") from exc
    if value < 1 or value > len(candidates):
        raise InitAgentResolutionError(f"selection out of range: {value}")
    return value


def _prompt_indexes(
    prompt: str,
    candidates: list[LocalAgentCandidate],
    *,
    default: list[int],
) -> list[int]:
    raw = typer.prompt(prompt, default=",".join(str(index) for index in default))
    indexes: list[int] = []
    for part in str(raw).split(","):
        text = part.strip()
        if not text:
            continue
        try:
            value = int(text)
        except ValueError as exc:
            raise InitAgentResolutionError(f"invalid worker selection: {text}") from exc
        if value < 1 or value > len(candidates):
            raise InitAgentResolutionError(f"worker selection out of range: {value}")
        if value not in indexes:
            indexes.append(value)
    if not indexes:
        indexes = list(default)
    return indexes


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


def validate_executor_environment(executor: ExecutorConfig) -> list[str]:
    if executor.name == "multica":
        return validate_multica_environment(executor.name)
    if executor.name in {"codex_cli", "claude_code"}:
        command = executor.command or executor.agent
        if _resolve_cli_command(command) is None and shutil.which(command) is None:
            return [f"{executor.name} command not found: {command}"]
        return []
    return [f"Unsupported executor '{executor.name}'"]


def run_init_health_checks(config: AGOSConfig, repo_root: Path) -> list[str]:
    """Return blocking health failures before interactive init auto-runs the task."""

    failures = list(validate_executor_environment(config.executor))
    service = ExecutionService(repo_paths(repo_root))
    register_configured_worker_adapters(service)
    for name, adapter in sorted(service.worker_adapters().items()):
        try:
            health = adapter.health()
        except Exception as exc:
            failures.append(f"worker {name} is not ready: health_check failed: {exc}")
            continue
        for check in health.checks:
            if check.state == "failed":
                detail = f": {check.detail}" if check.detail else ""
                failures.append(
                    f"worker {health.name} is not ready: {check.name} failed{detail}"
                )
    return failures


def _abort_for_health_failures(failures: list[str]) -> None:
    typer.echo("Health check failed:", err=True)
    for failure in failures:
        typer.echo(f"- {failure}", err=True)
    raise typer.Exit(code=1)


def _configure_task_execution(
    config: AGOSConfig,
    selected_agent: LocalAgentCandidate,
) -> tuple[AGOSConfig, str | None]:
    if selected_agent.executor_name in {"codex_cli", "claude_code"}:
        reviewer_name = f"{selected_agent.worker_name}_reviewer"
        return config.model_copy(
            update={
                "task_execution": TaskExecutionConfig(
                    mode="candidate",
                    output_contract="source_code",
                ),
                "reviewers": {
                    reviewer_name: ReviewerConfig(
                        type=selected_agent.executor_name,
                        role="code_review",
                        required=True,
                        command=selected_agent.command,
                        executor=selected_agent.executor_name,
                    )
                },
            }
        ), None

    reason = (
        f"selected executor {selected_agent.executor_name!r} cannot provide an automatic "
        "local reviewer; using compatible legacy execution"
    )
    return config.model_copy(
        update={
            "task_execution": TaskExecutionConfig(
                mode="legacy",
                output_contract="legacy",
            )
        }
    ), reason


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
    executor: str | None = typer.Option(
        None,
        "--executor",
        help="Restrict executor provider to multica, codex_cli, or claude_code.",
    ),
    agent: str | None = typer.Option(None, "--agent", help="Default local agent key or legacy name."),
) -> None:
    """Create `.agos/`, write config, and install git hooks."""

    supported_executors = {"multica", "codex_cli", "claude_code"}
    if executor is not None and executor not in supported_executors:
        raise typer.BadParameter("Executor must be one of: multica, codex_cli, claude_code.")

    auto_run_title: str | None = None
    auto_run_intent: str = ""
    try:
        candidates = discover_local_agents()
        if executor is not None:
            candidates = [candidate for candidate in candidates if candidate.executor_name == executor]
        if agent is None:
            if not candidates:
                if executor is not None:
                    raise InitAgentResolutionError(
                        "No local AGOS-compatible agents were found in the current workspace after "
                        f"applying --executor {executor}.\n\n"
                        "Install or enable Multica, Codex CLI, or Claude Code, then re-run:\n"
                        '  agos init --agent "<agent-name>"'
                    )
                raise InitAgentResolutionError(
                    "No default agent configured and --agent was not provided.\n\n"
                    "No local AGOS-compatible agents were found in the current workspace.\n"
                    "Install or enable Multica, Codex CLI, or Claude Code, then re-run:\n"
                    '  agos init --agent "<agent-name>"'
                )
            selected_agent = _select_interactive_executor(candidates)
            auto_run_title = _prompt_task_title()
            auto_run_intent = _prompt_task_intent()
            selected_workers = plan_workers_for_goal(selected_agent, auto_run_title, candidates)
            if not selected_workers:
                selected_workers = [selected_agent]
        else:
            selected_agent = resolve_init_agent(agent)
            if executor is not None and selected_agent.executor_name != executor:
                raise InitAgentResolutionError(
                    f'Configured agent "{agent}" is not compatible with executor "{executor}".'
                )
            selected_workers = [selected_agent]
    except InitAgentResolutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    repo_root = find_repo_root()
    agos_root = agos_dir(repo_root)
    agos_root.mkdir(parents=True, exist_ok=True)
    (agos_root / "tasks" / "current").mkdir(parents=True, exist_ok=True)
    (agos_root / "hooks").mkdir(parents=True, exist_ok=True)

    workers = {
        candidate.worker_name: WorkerConfig.model_validate(candidate.worker_config)
        for candidate in selected_workers
    }
    config = AGOSConfig.default(
        executor=selected_agent.executor_name,
        agent=selected_agent.executor_agent,
        command=selected_agent.command,
        workers=workers,
    )
    config, execution_fallback_reason = _configure_task_execution(config, selected_agent)
    config.save(config_path(repo_root))

    git_hooks_dir = repo_root / ".git" / "hooks"
    git_hooks_dir.mkdir(parents=True, exist_ok=True)
    for stage in ("pre-commit", "pre-push"):
        _install_hook(git_hooks_dir, stage=stage)

    append_repo_record(
        repo_ledger_path(repo_root),
        "repo_initialized",
        executor=selected_agent.executor_name,
        agent=selected_agent.executor_agent,
        hooks=["pre-commit", "pre-push"],
    )

    typer.echo(f"Initialized AGOS in {agos_root}")
    if execution_fallback_reason is not None:
        typer.echo(f"Warning: {execution_fallback_reason}", err=True)
    if auto_run_title is None:
        for warning in validate_executor_environment(config.executor):
            typer.echo(f"Warning: {warning}", err=True)
        return

    typer.echo("Running health checks...")
    health_failures = run_init_health_checks(config, repo_root)
    if health_failures:
        _abort_for_health_failures(health_failures)

    typer.echo("Health checks passed")
    typer.echo("Starting AGOS task...")
    from agos.cli.cmd_start import start_command

    start_command(
        title=auto_run_title,
        intent=auto_run_intent,
        workflow=None,
        gate=None,
        mode=None,
        json_output=False,
    )
