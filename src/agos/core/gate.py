"""Gate engine for command and secret-scan gates.

Gates only inspect the governed working tree and the human developer's diff.
`gates_locked` prevents swapping out the configured gate set mid-task.
"""
from __future__ import annotations

import os
import re
import json
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from agos.core.command import run_command
from agos.core.config import GateSpec


BUILTIN_SECRET_PATTERNS: list[str] = [
    r"AKIA[0-9A-Z]{16}",
    r"ghp_[0-9A-Za-z]{36}",
    r"gho_[0-9A-Za-z]{36}",
    r"AIza[0-9A-Za-z_\-]{35}",
    r"sk-[0-9A-Za-z]{20,}",
]

GIT_LOCAL_ENV_KEYS = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_SUPER_PREFIX",
    "GIT_WORK_TREE",
}


@dataclass(frozen=True)
class GateContext:
    repo_root: Path
    stage: str
    diff: str
    evidence_dir: Path


@dataclass(frozen=True)
class GateResult:
    state: Literal["pass", "block"]
    reason: str
    evidence_path: str | None = None


@runtime_checkable
class Gate(Protocol):
    id: str

    def evaluate(self, ctx: GateContext) -> GateResult: ...


def _fsafe_ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


class CommandGate:
    """Run a command gate and persist stdout/stderr evidence."""

    def __init__(self, spec: GateSpec) -> None:
        self.spec = spec
        self.id = spec.id

    def evaluate(self, ctx: GateContext) -> GateResult:
        log_dir = ctx.evidence_dir / "gates"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{self.id}-{_fsafe_ts()}.log"
        command_text = self.spec.command or ""
        argv = self.spec.argv
        command_kwargs = {
            "shell": argv is None,
            "cwd": ctx.repo_root,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": _gate_command_env(),
        }
        if self.spec.timeout_seconds is not None:
            command_kwargs["timeout"] = self.spec.timeout_seconds
        try:
            proc = run_command(
                argv if argv is not None else command_text,
                **command_kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            log_path.write_text(
                f"command: {command_text}\nargv: {argv}\ntimeout: {exc.timeout}\n",
                encoding="utf-8",
            )
            return GateResult(
                state="block",
                reason=f"gate {self.id}: command timed out after {exc.timeout}s",
                evidence_path=str(log_path),
            )
        except OSError as exc:
            log_path.write_text(
                f"command: {command_text}\nargv: {argv}\nstart_error: {exc}\n",
                encoding="utf-8",
            )
            return GateResult(
                state="block",
                reason=f"gate {self.id}: command failed to start ({exc})",
                evidence_path=str(log_path),
            )

        log_path.write_text(
            (
                f"command: {command_text}\n"
                f"argv: {argv}\n"
                f"exit_code: {proc.returncode}\n"
                f"--- stdout ---\n{proc.stdout}\n"
                f"--- stderr ---\n{proc.stderr}\n"
            ),
            encoding="utf-8",
        )
        if proc.returncode == 0:
            return GateResult(
                state="pass",
                reason=f"gate {self.id}: passed",
                evidence_path=str(log_path),
            )
        return GateResult(
            state="block",
            reason=f"gate {self.id}: command failed with exit {proc.returncode}",
            evidence_path=str(log_path),
        )


class SecretScanGate:
    """Scan the human developer diff for known secret-like tokens."""

    def __init__(self, spec: GateSpec) -> None:
        self.spec = spec
        self.id = spec.id
        self._patterns = [re.compile(pattern) for pattern in BUILTIN_SECRET_PATTERNS]

    def evaluate(self, ctx: GateContext) -> GateResult:
        for pattern in self._patterns:
            match = pattern.search(ctx.diff)
            if match:
                return GateResult(
                    state="block",
                    reason=f"gate {self.id}: secret pattern matched {match.group(0)!r}",
                )
        return GateResult(
            state="pass",
            reason=f"gate {self.id}: no secrets found",
        )


class ExternalSecurityGate:
    """Run a typed external security scanner using structured argv."""

    def __init__(self, spec: GateSpec) -> None:
        self.spec = spec
        self.id = spec.id

    def evaluate(self, ctx: GateContext) -> GateResult:
        log_dir = ctx.evidence_dir / "gates"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{self.id}-{_fsafe_ts()}.log"

        try:
            argv = self._argv()
        except ValueError as exc:
            log_path.write_text(f"gate_type: {self.spec.type}\nconfig_error: {exc}\n", encoding="utf-8")
            return GateResult(
                state="block",
                reason=f"gate {self.id}: invalid gate options ({exc})",
                evidence_path=str(log_path),
            )

        executable = argv[0]
        if shutil.which(executable) is None:
            log_path.write_text(
                (
                    f"gate_type: {self.spec.type}\n"
                    f"argv: {argv}\n"
                    f"start_error: missing executable {executable!r}\n"
                ),
                encoding="utf-8",
            )
            return GateResult(
                state="block",
                reason=f"gate {self.id}: missing executable {executable!r}",
                evidence_path=str(log_path),
            )

        command_kwargs = {
            "shell": False,
            "cwd": ctx.repo_root,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": _gate_command_env(),
        }
        if self.spec.timeout_seconds is not None:
            command_kwargs["timeout"] = self.spec.timeout_seconds
        try:
            proc = run_command(
                argv,
                **command_kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            log_path.write_text(
                f"gate_type: {self.spec.type}\nargv: {argv}\ntimeout: {exc.timeout}\n",
                encoding="utf-8",
            )
            return GateResult(
                state="block",
                reason=f"gate {self.id}: command timed out after {exc.timeout}s",
                evidence_path=str(log_path),
            )
        except OSError as exc:
            log_path.write_text(
                f"gate_type: {self.spec.type}\nargv: {argv}\nstart_error: {exc}\n",
                encoding="utf-8",
            )
            return GateResult(
                state="block",
                reason=f"gate {self.id}: command failed to start ({exc})",
                evidence_path=str(log_path),
            )

        log_path.write_text(
            (
                f"gate_type: {self.spec.type}\n"
                f"argv: {argv}\n"
                f"command: {gate_command_text(self.spec)}\n"
                f"exit_code: {proc.returncode}\n"
                f"--- stdout ---\n{proc.stdout}\n"
                f"--- stderr ---\n{proc.stderr}\n"
            ),
            encoding="utf-8",
        )
        if proc.returncode == 0:
            return GateResult(state="pass", reason=f"gate {self.id}: passed", evidence_path=str(log_path))
        return GateResult(
            state="block",
            reason=f"gate {self.id}: command failed with exit {proc.returncode}",
            evidence_path=str(log_path),
        )

    def _argv(self) -> list[str]:
        if self.spec.type is None or self.spec.type == "secret_scan":
            raise ValueError(f"unsupported external gate type: {self.spec.type}")
        options = self.spec.options
        command = _option_str(options, "command", default=self.spec.type)
        extra_args = _option_str_list(options, "args")
        if self.spec.type == "opa":
            argv = [command, "eval", "--format", "json"]
            policy = _option_str(options, "policy")
            input_path = _option_str(options, "input")
            query = _option_str(options, "query")
            if policy is not None:
                argv.extend(["-d", policy])
            if input_path is not None:
                argv.extend(["-i", input_path])
            if query is not None:
                argv.append(query)
            return [*argv, *extra_args]
        if self.spec.type == "semgrep":
            argv = [command, "scan"]
            config = _option_str(options, "config")
            if config is not None:
                argv.extend(["--config", config])
            return [*argv, *extra_args]
        if self.spec.type == "trufflehog":
            target = _option_str(options, "input", default=".")
            return [command, "filesystem", target, *extra_args]
        if self.spec.type == "codeql":
            database = _option_str(options, "database")
            if database is None:
                raise ValueError("codeql gate requires options.database")
            argv = [command, "database", "analyze", database]
            query = _option_str(options, "query")
            config = _option_str(options, "config")
            if query is not None:
                argv.append(query)
            if config is not None:
                argv.extend(["--codescanning-config", config])
            return [*argv, *extra_args]
        raise ValueError(f"unsupported external gate type: {self.spec.type}")


def _option_str(options: dict[str, object], key: str, default: str | None = None) -> str | None:
    value = options.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"options.{key} must be a string")
    return value


def _option_str_list(options: dict[str, object], key: str) -> list[str]:
    value = options.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"options.{key} must be a list of strings")
    return value


def gate_command_text(spec: GateSpec) -> str:
    if spec.command is not None:
        return spec.command
    if spec.argv is not None:
        return shlex.join(spec.argv)
    if spec.type == "secret_scan":
        return "secret_scan"
    try:
        return shlex.join(ExternalSecurityGate(spec)._argv())
    except ValueError:
        command = spec.options.get("command")
        if isinstance(command, str):
            return command
        return spec.type or "typed_gate"


def build_gate(spec: GateSpec) -> Gate:
    if spec.command is not None or spec.argv is not None:
        return CommandGate(spec)
    if spec.type == "secret_scan":
        return SecretScanGate(spec)
    if spec.type in {"opa", "semgrep", "trufflehog", "codeql"}:
        return ExternalSecurityGate(spec)
    raise ValueError(f"cannot build gate {spec.id!r}: unsupported gate spec")


def gates_locked_payload(gates: list[GateSpec]) -> list[dict]:
    return [
        {
            "id": gate.id,
            "stage": sorted(gate.stage),
            "command": gate.command,
            "argv": gate.argv,
            "type": gate.type,
            "timeout_seconds": gate.timeout_seconds,
            "options": gate.options,
        }
        for gate in gates
    ]


def gates_match(locked: list[dict], current: list[GateSpec]) -> bool:
    locked_without_timeout = sorted(
        (
            entry["id"],
            tuple(entry["stage"]),
            entry.get("command"),
            tuple(entry.get("argv") or []),
            entry["type"],
            _canonical_options(entry.get("options") or {}),
        )
        for entry in locked
    )
    current_without_timeout = sorted(
        (
            gate.id,
            tuple(sorted(gate.stage)),
            gate.command,
            tuple(gate.argv or []),
            gate.type,
            _canonical_options(gate.options),
        )
        for gate in current
    )
    if locked_without_timeout != current_without_timeout:
        return False

    current_timeout_by_key = {
        (
            gate.id,
            tuple(sorted(gate.stage)),
            gate.command,
            tuple(gate.argv or []),
            gate.type,
            _canonical_options(gate.options),
        ): gate.timeout_seconds
        for gate in current
    }
    for entry in locked:
        if "timeout_seconds" not in entry:
            continue
        key = (
            entry["id"],
            tuple(entry["stage"]),
            entry.get("command"),
            tuple(entry.get("argv") or []),
            entry["type"],
            _canonical_options(entry.get("options") or {}),
        )
        if current_timeout_by_key.get(key) != entry.get("timeout_seconds"):
            return False
    return True


def _canonical_options(options: dict) -> str:
    return json.dumps(options, sort_keys=True, separators=(",", ":"))


def _gate_command_env() -> dict[str, str]:
    """Return an environment safe for nested git commands run from hooks."""

    env = dict(os.environ)
    for key in _git_local_env_keys():
        env.pop(key, None)
    return env


def _git_local_env_keys() -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--local-env-vars"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return GIT_LOCAL_ENV_KEYS
    keys = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return keys or GIT_LOCAL_ENV_KEYS
