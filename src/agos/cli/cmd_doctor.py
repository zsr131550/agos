"""Top-level `agos doctor` diagnostics."""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass

import typer
from pydantic import ValidationError

from agos.cli.orchestration_registry import register_configured_orchestration_backends
from agos.cli.reviewer_registry import configured_reviewer_adapters, configured_reviewer_specs
from agos.cli.worker_registry import register_configured_worker_adapters
from agos.core.command import run_command
from agos.core.config import AGOSConfig
from agos.core.execution_service import ExecutionService
from agos.core.repo import find_repo_root, repo_paths
from agos.core.status import load_status
from agos.core.trust_anchor import GitRefTrustAnchorStore, store_from_config, verify_current_anchor


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    state: str
    detail: str = ""

    def payload(self) -> dict[str, str]:
        return {"name": self.name, "state": self.state, "detail": self.detail}


def doctor_command(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Diagnose the current AGOS repository configuration."""

    checks = _run_checks()
    healthy = all(check.state != "failed" for check in checks)
    payload = {"healthy": healthy, "checks": [check.payload() for check in checks]}

    if json_output:
        typer.echo(json.dumps(payload, sort_keys=True))
    else:
        typer.echo(_format_checks(checks))

    if not healthy:
        raise typer.Exit(code=1)


def _run_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    repo_root = None
    config = None

    try:
        repo_root = find_repo_root()
        checks.append(DoctorCheck("git_repo", "passed", str(repo_root)))
    except Exception as exc:
        checks.append(DoctorCheck("git_repo", "failed", str(exc)))
        return checks

    paths = repo_paths(repo_root)
    if paths.agos_yaml.is_file():
        checks.append(DoctorCheck("agos_initialized", "passed", str(paths.agos_yaml)))
        checks.append(_python_version_check())
        checks.append(_cli_entrypoint_check())
        checks.append(_git_hooks_check(repo_root))
    else:
        checks.append(DoctorCheck("agos_initialized", "failed", "missing .agos/agos.yaml"))
        checks.extend(
            [
                _python_version_check(),
                _cli_entrypoint_check(),
                DoctorCheck("config", "skipped", "repository is not initialized"),
                DoctorCheck("git_hooks", "skipped", "repository is not initialized"),
                DoctorCheck("workers", "skipped", "repository is not initialized"),
                DoctorCheck("reviewers", "skipped", "repository is not initialized"),
                DoctorCheck("orchestration", "skipped", "repository is not initialized"),
                DoctorCheck("autonomous_loop", "skipped", "repository is not initialized"),
                DoctorCheck("trust_anchor", "skipped", "repository is not initialized"),
            ]
        )
        return checks

    try:
        config = AGOSConfig.load(paths.agos_yaml)
        checks.append(DoctorCheck("config", "passed", str(paths.agos_yaml)))
    except Exception as exc:
        checks.append(DoctorCheck("config", "failed", _safe_config_error(exc)))
        checks.extend(
            [
                DoctorCheck("workers", "skipped", "config is invalid"),
                DoctorCheck("reviewers", "skipped", "config is invalid"),
                DoctorCheck("orchestration", "skipped", "config is invalid"),
                DoctorCheck("autonomous_loop", "skipped", "config is invalid"),
                DoctorCheck("trust_anchor", "skipped", "config is invalid"),
            ]
        )
        return checks

    service = ExecutionService(paths)
    worker_count = 0
    try:
        register_configured_worker_adapters(service)
        worker_count = len(service.worker_adapters())
        checks.append(_worker_health_check(service))
    except Exception as exc:
        checks.append(DoctorCheck("workers", "failed", str(exc)))

    reviewer_specs = []
    try:
        reviewer_specs = configured_reviewer_specs(repo_root)
        configured_reviewer_adapters(repo_root)
        dev_only_count = sum(1 for reviewer in config.reviewers.values() if reviewer.dev_only)
        if dev_only_count:
            checks.append(
                DoctorCheck(
                    "reviewers",
                    "warning",
                    f"{dev_only_count} dev-only reviewer(s) active; not for production",
                )
            )
        else:
            checks.append(DoctorCheck("reviewers", "passed", f"{len(reviewer_specs)} reviewer(s) configured"))
    except Exception as exc:
        checks.append(DoctorCheck("reviewers", "failed", str(exc)))

    try:
        register_configured_orchestration_backends(service)
        state = "passed" if config.orchestration.backend in service.orchestration_backend_names() else "failed"
        detail = (
            f"backend {config.orchestration.backend!r} configured"
            if state == "passed"
            else f"unknown backend {config.orchestration.backend!r}"
        )
        checks.append(DoctorCheck("orchestration", state, detail))
    except Exception as exc:
        checks.append(DoctorCheck("orchestration", "failed", str(exc)))

    checks.append(_autonomous_loop_readiness_check(config, reviewer_specs=reviewer_specs, worker_count=worker_count))
    checks.append(_trust_anchor_check(paths, config))

    return checks


def _python_version_check() -> DoctorCheck:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info < (3, 11):
        return DoctorCheck("python_version", "failed", f"Python {version}; AGOS requires >= 3.11")
    return DoctorCheck("python_version", "passed", f"Python {version}")


def _cli_entrypoint_check() -> DoctorCheck:
    script = shutil.which("agos")
    if script is None:
        return DoctorCheck(
            "cli_entrypoint",
            "failed",
            "agos console script not found on PATH",
        )

    completed = run_command([script, "--help"], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        return DoctorCheck(
            "cli_entrypoint",
            "failed",
            f"agos console script at {script} could not run: {detail}",
        )

    detail = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "help available"
    return DoctorCheck("cli_entrypoint", "passed", f"agos console script at {script} ran: {detail}")


def _git_hooks_check(repo_root) -> DoctorCheck:
    hooks_dir = _git_hooks_dir(repo_root)
    required = ("pre-commit", "pre-push")
    missing: list[str] = []
    unmanaged: list[str] = []
    for hook_name in required:
        hook_path = hooks_dir / hook_name
        if not hook_path.is_file():
            missing.append(hook_name)
            continue
        try:
            text = hook_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            unmanaged.append(hook_name)
            continue
        if "# Managed by AGOS" not in text:
            unmanaged.append(hook_name)

    if missing or unmanaged:
        details: list[str] = []
        if missing:
            details.append(f"not installed: {', '.join(missing)}")
        if unmanaged:
            details.append(f"not managed by AGOS: {', '.join(unmanaged)}")
        details.append("run: agos init")
        return DoctorCheck("git_hooks", "warning", "; ".join(details))
    return DoctorCheck("git_hooks", "passed", "pre-commit and pre-push installed")


def _git_hooks_dir(repo_root):
    completed = run_command(
        ["git", "rev-parse", "--git-path", "hooks"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        hooks_path = completed.stdout.strip()
        from pathlib import Path

        path = Path(hooks_path)
        return path if path.is_absolute() else repo_root / path
    return repo_root / ".git" / "hooks"


def _worker_health_check(service: ExecutionService) -> DoctorCheck:
    adapters = service.worker_adapters()
    if not adapters:
        return DoctorCheck("workers", "warning", "no workers configured")

    failed: list[str] = []
    warnings: list[str] = []
    for name, adapter in sorted(adapters.items()):
        try:
            health = adapter.health()
        except Exception as exc:
            failed.append(f"{name}: health_check failed: {exc}")
            continue
        for check in health.checks:
            detail = f": {check.detail}" if check.detail else ""
            item = f"{name}/{check.name}{detail}"
            if check.state == "failed":
                failed.append(item)
            elif check.state == "warning":
                warnings.append(item)

    if failed:
        return DoctorCheck("workers", "failed", "; ".join(failed))
    if warnings:
        return DoctorCheck("workers", "warning", "; ".join(warnings))
    return DoctorCheck("workers", "passed", f"{len(adapters)} worker(s) healthy")


def _autonomous_loop_readiness_check(
    config: AGOSConfig,
    *,
    reviewer_specs,
    worker_count: int,
) -> DoctorCheck:
    issues: list[str] = []
    failures: list[str] = []

    if worker_count == 0:
        issues.append("no workers configured; fallback local_worktree will be used only when execution registers it")
    elif config.orchestration.max_parallel > worker_count:
        issues.append(
            f"max_parallel={config.orchestration.max_parallel} exceeds configured worker count={worker_count}; "
            "extra capacity will wait"
        )

    planner = config.orchestration.planner
    if planner.enabled:
        planner_command = planner.command or _default_cli_command(planner.executor)
        if shutil.which(planner_command) is None:
            issues.append(
                f"planner command unavailable: {planner_command}; agos run auto will use deterministic fallback"
            )

    if not reviewer_specs:
        issues.append(
            "no reviewers configured; automatic acceptance is blocked unless --allow-missing-review is used"
        )

    for name, reviewer in sorted(config.reviewers.items()):
        if reviewer.type == "manual" and reviewer.required:
            issues.append(
                f"required manual reviewer {name!r} blocks automatic acceptance until manual findings are ingested"
            )
        elif reviewer.type in {"codex_cli", "claude_code"}:
            command = reviewer.command or _default_cli_command(reviewer.executor or reviewer.type)
            if reviewer.required and shutil.which(command) is None:
                failures.append(f"required reviewer {name!r} command unavailable: {command}")
            elif shutil.which(command) is None:
                issues.append(f"optional reviewer {name!r} command unavailable: {command}")

    if failures:
        return DoctorCheck("autonomous_loop", "failed", "; ".join(failures + issues))
    if issues:
        return DoctorCheck("autonomous_loop", "warning", "; ".join(issues))
    return DoctorCheck("autonomous_loop", "passed", "planner/worker/reviewer loop ready")


def _default_cli_command(executor: str) -> str:
    if executor == "codex_cli":
        return "codex"
    if executor == "claude_code":
        return "claude"
    return executor


def _trust_anchor_check(paths, config: AGOSConfig) -> DoctorCheck:
    if load_status(paths) is None or not paths.task_yaml.is_file():
        return DoctorCheck("trust_anchor", "skipped", "no active AGOS task")

    stores = [(config.trust_anchor.backend, store_from_config(paths, config.trust_anchor))]
    if config.trust_anchor.backend != "git-ref":
        stores.append(("git-ref", GitRefTrustAnchorStore(paths.root)))

    issues: list[str] = []
    for label, store in stores:
        verification = verify_current_anchor(paths, store)
        if verification.passed:
            return DoctorCheck("trust_anchor", "passed", f"{label} anchor verified")
        issues.extend(f"{label}: {issue}" for issue in verification.issues)

    detail = "no valid trust anchor"
    if issues:
        detail = f"{detail}: {'; '.join(issues)}"
    detail = f"{detail}; run: agos anchor publish --backend git-ref --issuer <issuer>"
    return DoctorCheck("trust_anchor", "warning", detail)


def _format_checks(checks: list[DoctorCheck]) -> str:
    return "\n".join(
        f"[{check.state}] {check.name}{': ' + check.detail if check.detail else ''}"
        for check in checks
    )


def _safe_config_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return f"invalid AGOS configuration: {exc}"
    return str(exc)
