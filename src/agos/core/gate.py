"""Gate engine for command and secret-scan gates.

Gates only inspect the governed working tree and the human developer's diff.
`gates_locked` prevents swapping out the configured gate set mid-task.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from agos.core.config import GateSpec


BUILTIN_SECRET_PATTERNS: list[str] = [
    r"AKIA[0-9A-Z]{16}",
    r"ghp_[0-9A-Za-z]{36}",
    r"gho_[0-9A-Za-z]{36}",
    r"AIza[0-9A-Za-z_\-]{35}",
    r"sk-[0-9A-Za-z]{20,}",
]


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
        try:
            proc = subprocess.run(
                self.spec.command,
                shell=True,
                cwd=ctx.repo_root,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            log_path.write_text(
                f"command: {self.spec.command}\nstart_error: {exc}\n",
                encoding="utf-8",
            )
            return GateResult(
                state="block",
                reason=f"gate {self.id}: command failed to start ({exc})",
                evidence_path=str(log_path),
            )

        log_path.write_text(
            (
                f"command: {self.spec.command}\n"
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


def build_gate(spec: GateSpec) -> Gate:
    if spec.command is not None:
        return CommandGate(spec)
    if spec.type == "secret_scan":
        return SecretScanGate(spec)
    raise ValueError(f"cannot build gate {spec.id!r}: unsupported gate spec")


def gates_locked_payload(gates: list[GateSpec]) -> list[dict]:
    return [
        {
            "id": gate.id,
            "stage": sorted(gate.stage),
            "command": gate.command,
            "type": gate.type,
        }
        for gate in gates
    ]


def gates_match(locked: list[dict], current: list[GateSpec]) -> bool:
    return sorted(
        (entry["id"], tuple(entry["stage"]), entry["command"], entry["type"])
        for entry in locked
    ) == sorted(
        (gate.id, tuple(sorted(gate.stage)), gate.command, gate.type)
        for gate in current
    )
