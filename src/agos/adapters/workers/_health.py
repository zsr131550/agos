"""Shared health-check helpers for CLI-backed worker adapters.

The checks are tiered so that a missing binary blocks readiness while a
temporarily unresponsive CLI (no credentials, quota, network) only degrades to
a warning. This keeps `fail-closed` semantics — an unavailable CLI never lets a
merge through — without making `run auto` unstartable in CI where API
credentials are absent.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from agos.core.command import run_command
from agos.core.execution_worker import WorkerHealthCheck


def command_available_check(command: str) -> WorkerHealthCheck:
    """Level 1: the CLI binary is on PATH. Missing -> failed (blocks readiness)."""

    resolved = shutil.which(command)
    if resolved is None:
        return WorkerHealthCheck(
            name="command_available",
            state="failed",
            detail=f"command not found: {command}",
        )
    return WorkerHealthCheck(name="command_available", state="passed", detail=resolved)


def version_check(
    command: str,
    *,
    timeout: int = 5,
    env: dict[str, str] | None = None,
) -> WorkerHealthCheck:
    """Level 2: `--version` responds. Timeout/nonzero -> warning (degraded)."""

    return _run_probe_check(
        name="version_responds",
        args=[command, "--version"],
        timeout=timeout,
        env=env,
    )


def probe_check(
    command: str,
    args: list[str],
    *,
    timeout: int = 15,
    env: dict[str, str] | None = None,
) -> WorkerHealthCheck:
    """Level 3: a lightweight CLI invocation. Timeout/nonzero -> warning.

    Probe failure is intentionally non-fatal: it usually reflects missing
    credentials, quota, or network — not a broken install — so it must not block
    `ensure_worker_ready`.
    """

    return _run_probe_check(
        name="cli_executes",
        args=[command, *args],
        timeout=timeout,
        env=env,
    )


def _run_probe_check(
    *,
    name: str,
    args: list[str],
    timeout: int,
    env: dict[str, str] | None,
) -> WorkerHealthCheck:
    try:
        proc = run_command(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            env={**os.environ, **(env or {})},
        )
    except subprocess.TimeoutExpired:
        return WorkerHealthCheck(
            name=name,
            state="warning",
            detail=f"timed out after {timeout:g} seconds",
        )
    except OSError as exc:
        return WorkerHealthCheck(name=name, state="warning", detail=str(exc))
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        return WorkerHealthCheck(name=name, state="warning", detail=detail)
    detail = (proc.stdout or proc.stderr or "ok").strip() or "ok"
    return WorkerHealthCheck(name=name, state="passed", detail=detail)
