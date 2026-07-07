"""Tests for the gate engine, gates_locked, and secret_scan."""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import pytest

from agos.core.config import GateSpec
from agos.core.gate import (
    BUILTIN_SECRET_PATTERNS,
    CommandGate,
    ExternalSecurityGate,
    GateContext,
    SecretScanGate,
    build_gate,
    gate_command_text,
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


def test_command_gate_blocks_on_timeout_and_oserror(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch):
    spec = GateSpec(id="timeout", stage=["pre-commit"], argv=["tool"])

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["tool"], timeout=3)

    monkeypatch.setattr("agos.core.gate.run_command", timeout)
    timeout_result = CommandGate(spec).evaluate(ctx(tmp_repo))
    assert timeout_result.state == "block"
    assert "timed out" in timeout_result.reason

    monkeypatch.setattr(
        "agos.core.gate.run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")),
    )
    os_result = CommandGate(spec).evaluate(ctx(tmp_repo))
    assert os_result.state == "block"
    assert "failed to start" in os_result.reason


def test_command_gate_supports_structured_argv(tmp_repo: Path):
    spec = GateSpec(id="argv_ok", stage=["pre-commit"], argv=[sys.executable, "-c", "raise SystemExit(0)"])

    res = CommandGate(spec).evaluate(ctx(tmp_repo))

    assert res.state == "pass"
    assert res.evidence_path is not None
    log = Path(res.evidence_path).read_text(encoding="utf-8")
    assert "argv:" in log


def test_command_gate_passes_configured_timeout(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_run_command(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

        class Proc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Proc()

    monkeypatch.setattr("agos.core.gate.run_command", fake_run_command)
    spec = GateSpec(
        id="argv_timeout",
        stage=["pre-commit"],
        argv=[sys.executable, "-c", "raise SystemExit(0)"],
        timeout_seconds=123,
    )

    res = CommandGate(spec).evaluate(ctx(tmp_repo))

    assert res.state == "pass"
    assert captured["kwargs"]["timeout"] == 123


def test_command_gate_strips_git_hook_local_env(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GIT_DIR", "outer/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "outer")
    monkeypatch.setenv("GIT_INDEX_FILE", "outer/index")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("AGOS_TEST_KEEP", "kept")
    captured = {}

    def fake_run_command(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

        class Proc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Proc()

    monkeypatch.setattr("agos.core.gate.run_command", fake_run_command)
    spec = GateSpec(id="hook_env", stage=["pre-commit"], argv=["pytest", "-q"])

    res = CommandGate(spec).evaluate(ctx(tmp_repo))

    assert res.state == "pass"
    env = captured["kwargs"]["env"]
    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_INDEX_FILE" not in env
    assert "GIT_CONFIG_COUNT" not in env
    assert env["AGOS_TEST_KEEP"] == "kept"


def test_external_security_gate_passes_configured_timeout(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    captured = {}

    def fake_run_command(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

        class Proc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Proc()

    monkeypatch.setattr("agos.core.gate.run_command", fake_run_command)
    monkeypatch.setattr("agos.core.gate.shutil.which", lambda command: command)
    spec = GateSpec(
        id="semgrep_timeout",
        stage=["pre-commit"],
        type="semgrep",
        options={"command": "semgrep"},
        timeout_seconds=456,
    )

    res = ExternalSecurityGate(spec).evaluate(ctx(tmp_repo))

    assert res.state == "pass"
    assert captured["kwargs"]["timeout"] == 456


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
    assert isinstance(build_gate(GateSpec(id="o", stage=["pre-commit"], type="opa")), ExternalSecurityGate)


def test_gates_locked_payload_stable():
    specs = [
        GateSpec(id="tests_pass", stage=["pre-commit", "pre-push"], argv=["pytest", "-q"]),
        GateSpec(id="no_secrets", stage=["pre-commit"], type="secret_scan"),
        GateSpec(
            id="policy",
            stage=["pre-commit"],
            type="opa",
            options={"policy": "policy.rego", "input": "input.json"},
        ),
    ]
    p = gates_locked_payload(specs)
    assert p[0]["id"] == "tests_pass"
    assert p[0]["stage"] == ["pre-commit", "pre-push"]
    assert p[0]["argv"] == ["pytest", "-q"]
    assert p[1]["type"] == "secret_scan"
    assert p[2]["options"] == {"policy": "policy.rego", "input": "input.json"}


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


def test_gates_match_false_when_options_changed():
    specs = [GateSpec(id="a", stage=["pre-commit"], type="opa", options={"policy": "p.rego"})]
    locked = gates_locked_payload(specs)
    assert gates_match(locked, [GateSpec(id="a", stage=["pre-commit"], type="opa", options={"policy": "q.rego"})]) is False


def test_gates_match_accepts_legacy_lock_without_timeout_seconds():
    locked = [
        {
            "id": "tests_pass",
            "stage": ["pre-commit"],
            "command": None,
            "argv": ["pytest", "-q"],
            "type": None,
            "options": {},
        }
    ]
    current = [
        GateSpec(
            id="tests_pass",
            stage=["pre-commit"],
            argv=["pytest", "-q"],
            timeout_seconds=300,
        )
    ]

    assert gates_match(locked, current) is True


def test_gates_match_false_when_locked_timeout_changed():
    locked = gates_locked_payload(
        [GateSpec(id="tests_pass", stage=["pre-commit"], argv=["pytest", "-q"], timeout_seconds=300)]
    )

    assert (
        gates_match(
            locked,
            [
                GateSpec(
                    id="tests_pass",
                    stage=["pre-commit"],
                    argv=["pytest", "-q"],
                    timeout_seconds=120,
                )
            ],
        )
        is False
    )


def test_external_security_gate_builds_structured_argv(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_run_command(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

        class Proc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Proc()

    monkeypatch.setattr("agos.core.gate.run_command", fake_run_command)
    monkeypatch.setattr("agos.core.gate.shutil.which", lambda command: command)
    spec = GateSpec(
        id="opa_policy",
        stage=["pre-commit"],
        type="opa",
        options={
            "command": "opa",
            "policy": "policy.rego",
            "input": "input.json",
            "args": ["--stdin-input"],
        },
    )

    res = ExternalSecurityGate(spec).evaluate(ctx(tmp_repo))

    assert res.state == "pass"
    assert captured["args"] == ["opa", "eval", "--format", "json", "-d", "policy.rego", "-i", "input.json", "--stdin-input"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["cwd"] == tmp_repo


def test_external_security_gate_blocks_when_executable_missing(tmp_repo: Path):
    spec = GateSpec(id="missing", stage=["pre-commit"], type="semgrep")

    res = ExternalSecurityGate(spec).evaluate(ctx(tmp_repo))

    assert res.state == "block"
    assert res.evidence_path is not None
    assert Path(res.evidence_path).exists()
    log = Path(res.evidence_path).read_text(encoding="utf-8")
    assert "start_error" in log or "missing" in res.reason.lower()


def test_external_security_gate_blocks_on_nonzero_exit(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_run_command(args, **kwargs):
        class Proc:
            returncode = 7
            stdout = "warn"
            stderr = "bad"

        return Proc()

    monkeypatch.setattr("agos.core.gate.run_command", fake_run_command)
    monkeypatch.setattr("agos.core.gate.shutil.which", lambda command: command)
    spec = GateSpec(id="semgrep_policy", stage=["pre-commit"], type="semgrep", options={"command": "semgrep"})

    res = ExternalSecurityGate(spec).evaluate(ctx(tmp_repo))

    assert res.state == "block"
    assert res.evidence_path is not None
    log = Path(res.evidence_path).read_text(encoding="utf-8")
    assert "warn" in log
    assert "bad" in log


def test_external_security_gate_blocks_on_invalid_options(tmp_repo: Path):
    spec = GateSpec(id="bad_options", stage=["pre-commit"], type="semgrep", options={"args": "oops"})

    res = ExternalSecurityGate(spec).evaluate(ctx(tmp_repo))

    assert res.state == "block"
    assert "invalid gate options" in res.reason
    assert res.evidence_path is not None


def test_external_security_gate_blocks_on_timeout_and_oserror(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch):
    spec = GateSpec(id="timeout", stage=["pre-commit"], type="semgrep", options={"command": "semgrep"})
    monkeypatch.setattr("agos.core.gate.shutil.which", lambda command: command)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["semgrep"], timeout=4)

    monkeypatch.setattr("agos.core.gate.run_command", timeout)
    timeout_result = ExternalSecurityGate(spec).evaluate(ctx(tmp_repo))
    assert timeout_result.state == "block"
    assert "timed out" in timeout_result.reason

    monkeypatch.setattr(
        "agos.core.gate.run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")),
    )
    os_result = ExternalSecurityGate(spec).evaluate(ctx(tmp_repo))
    assert os_result.state == "block"
    assert "failed to start" in os_result.reason


def test_external_security_gate_argv_builders_cover_supported_types():
    assert ExternalSecurityGate(
        GateSpec(
            id="opa",
            stage=["pre-commit"],
            type="opa",
            options={"query": "data.agos.allow"},
        )
    )._argv() == ["opa", "eval", "--format", "json", "data.agos.allow"]
    assert ExternalSecurityGate(
        GateSpec(
            id="semgrep",
            stage=["pre-commit"],
            type="semgrep",
            options={"config": "p/security-audit"},
        )
    )._argv() == ["semgrep", "scan", "--config", "p/security-audit"]
    assert ExternalSecurityGate(
        GateSpec(
            id="trufflehog",
            stage=["pre-commit"],
            type="trufflehog",
            options={"input": "src", "args": ["--only-verified"]},
        )
    )._argv() == ["trufflehog", "filesystem", "src", "--only-verified"]
    assert ExternalSecurityGate(
        GateSpec(
            id="codeql",
            stage=["pre-commit"],
            type="codeql",
            options={"database": ".codeql-db", "query": "security", "config": "codeql.yml"},
        )
    )._argv() == [
        "codeql",
        "database",
        "analyze",
        ".codeql-db",
        "security",
        "--codescanning-config",
        "codeql.yml",
    ]


def test_external_security_gate_rejects_bad_option_types_and_missing_codeql_database():
    with pytest.raises(ValueError, match="options.command"):
        ExternalSecurityGate(
            GateSpec(id="bad", stage=["pre-commit"], type="semgrep", options={"command": 1})
        )._argv()
    with pytest.raises(ValueError, match="options.args"):
        ExternalSecurityGate(
            GateSpec(id="bad", stage=["pre-commit"], type="semgrep", options={"args": [1]})
        )._argv()
    with pytest.raises(ValueError, match="database"):
        ExternalSecurityGate(GateSpec(id="codeql", stage=["pre-commit"], type="codeql"))._argv()


def test_gate_command_text_handles_invalid_typed_gate_options():
    spec = GateSpec(id="codeql_policy", stage=["pre-commit"], type="codeql")

    assert gate_command_text(spec) == "codeql"


def test_builtin_patterns_nonempty():
    assert len(BUILTIN_SECRET_PATTERNS) >= 3
