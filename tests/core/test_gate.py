"""Tests for the gate engine, gates_locked, and secret_scan."""
from __future__ import annotations

import sys
from pathlib import Path

from agos.core.config import GateSpec
from agos.core.gate import (
    BUILTIN_SECRET_PATTERNS,
    CommandGate,
    GateContext,
    SecretScanGate,
    build_gate,
    gates_locked_payload,
    gates_match,
)


PASS_COMMAND = f'"{sys.executable}" -c "raise SystemExit(0)"'
FAIL_COMMAND = f'"{sys.executable}" -c "raise SystemExit(1)"'


def ctx(repo: Path, diff: str = "") -> GateContext:
    return GateContext(
        repo_root=repo,
        stage="pre-commit",
        diff=diff,
        evidence_dir=repo / ".agos" / "tasks" / "current" / "evidence",
    )


def test_command_gate_pass(tmp_repo: Path):
    spec = GateSpec(id="echo_ok", stage=["pre-commit"], command=PASS_COMMAND)
    res = CommandGate(spec).evaluate(ctx(tmp_repo))
    assert res.state == "pass"
    assert res.evidence_path is not None
    assert Path(res.evidence_path).exists()


def test_command_gate_block_on_nonzero(tmp_repo: Path):
    spec = GateSpec(id="echo_fail", stage=["pre-commit"], command=FAIL_COMMAND)
    res = CommandGate(spec).evaluate(ctx(tmp_repo))
    assert res.state == "block"
    assert res.evidence_path is not None


def test_command_gate_block_when_command_missing(tmp_repo: Path):
    spec = GateSpec(id="nope", stage=["pre-commit"], command="this-command-does-not-exist-xyz")
    res = CommandGate(spec).evaluate(ctx(tmp_repo))
    assert res.state == "block"
    assert "not" in res.reason.lower() or "fail" in res.reason.lower()


def test_secret_scan_clear(tmp_repo: Path):
    spec = GateSpec(id="no_secrets", stage=["pre-commit"], type="secret_scan")
    res = SecretScanGate(spec).evaluate(ctx(tmp_repo, diff="print('hello world')\n"))
    assert res.state == "pass"


def test_secret_scan_finds_aws_key(tmp_repo: Path):
    spec = GateSpec(id="no_secrets", stage=["pre-commit"], type="secret_scan")
    diff = "AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
    res = SecretScanGate(spec).evaluate(ctx(tmp_repo, diff=diff))
    assert res.state == "block"
    assert "secret" in res.reason.lower()


def test_secret_scan_finds_github_pat(tmp_repo: Path):
    spec = GateSpec(id="no_secrets", stage=["pre-commit"], type="secret_scan")
    diff = "token = 'ghp_0123456789012345678901234567890123456'\n"
    res = SecretScanGate(spec).evaluate(ctx(tmp_repo, diff=diff))
    assert res.state == "block"


def test_secret_scan_finds_sk_token(tmp_repo: Path):
    spec = GateSpec(id="no_secrets", stage=["pre-commit"], type="secret_scan")
    diff = "token = 'sk-abcdefghijklmnopqrstuvwxyz123456'\n"
    res = SecretScanGate(spec).evaluate(ctx(tmp_repo, diff=diff))
    assert res.state == "block"


def test_build_gate_factory():
    assert isinstance(build_gate(GateSpec(id="t", stage=["pre-commit"], command=PASS_COMMAND)), CommandGate)
    assert isinstance(build_gate(GateSpec(id="s", stage=["pre-commit"], type="secret_scan")), SecretScanGate)


def test_gates_locked_payload_stable():
    specs = [
        GateSpec(id="tests_pass", stage=["pre-commit", "pre-push"], command="pytest -q"),
        GateSpec(id="no_secrets", stage=["pre-commit"], type="secret_scan"),
    ]
    p = gates_locked_payload(specs)
    assert p[0]["id"] == "tests_pass"
    assert p[0]["stage"] == ["pre-commit", "pre-push"]
    assert p[0]["command"] == "pytest -q"
    assert p[1]["type"] == "secret_scan"


def test_gates_match_true_when_unchanged():
    specs = [
        GateSpec(id="a", stage=["pre-commit"], command="x"),
        GateSpec(id="b", stage=["pre-push"], command="y"),
    ]
    locked = gates_locked_payload(specs)
    assert gates_match(locked, specs) is True


def test_gates_match_false_when_gate_removed():
    specs = [
        GateSpec(id="a", stage=["pre-commit"], command="x"),
        GateSpec(id="b", stage=["pre-push"], command="y"),
    ]
    locked = gates_locked_payload(specs)
    assert gates_match(locked, [GateSpec(id="a", stage=["pre-commit"], command="x")]) is False


def test_gates_match_false_when_command_changed():
    specs = [GateSpec(id="a", stage=["pre-commit"], command="x")]
    locked = gates_locked_payload(specs)
    assert gates_match(locked, [GateSpec(id="a", stage=["pre-commit"], command="EVAL ALWAYS TRUE")]) is False


def test_builtin_patterns_nonempty():
    assert len(BUILTIN_SECRET_PATTERNS) >= 3
