"""End-to-end smoke: init -> start -> checkpoint --once -> ci --local.

Skipped unless AGOS_INTEGRATION=1 and a real Multica daemon/workspace are
reachable. This creates a real Multica issue assigned to the configured agent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agos.adapters.multica import resolve_multica_bin

pytestmark = pytest.mark.integration


def _integration_agent() -> str:
    return os.environ.get("AGOS_INTEGRATION_AGENT", "Lambda")


def _integration_title(repo_root: Path) -> str:
    return f"smoke-{repo_root.parent.name}"


def _extract_messages(payload: dict | list) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return payload.get("messages", [])


def _run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _multica_ready() -> bool:
    multica_bin = resolve_multica_bin()
    if shutil.which(multica_bin) is None and not Path(multica_bin).exists():
        return False

    for command in (
        [multica_bin, "daemon", "status"],
        [multica_bin, "workspace", "list", "--output", "json"],
    ):
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            return False
    return True


def _wait_for_messages(run_id: str, *, cwd: Path, timeout_seconds: int = 60) -> list[dict]:
    multica_bin = resolve_multica_bin()
    deadline = time.time() + timeout_seconds
    last_stdout = ""
    last_stderr = ""
    while time.time() < deadline:
        completed = _run(
            multica_bin,
            "issue",
            "run-messages",
            run_id,
            "--output",
            "json",
            cwd=cwd,
            check=False,
        )
        last_stdout = completed.stdout
        last_stderr = completed.stderr
        if completed.returncode == 0:
            payload = json.loads(completed.stdout or "{}")
            messages = _extract_messages(payload)
            if messages:
                return messages
        time.sleep(2)

    pytest.fail(
        "timed out waiting for Multica run messages; "
        f"stdout={last_stdout!r} stderr={last_stderr!r}"
    )


@pytest.fixture(autouse=True)
def _skip_unless_opted_in():
    if os.environ.get("AGOS_INTEGRATION") != "1":
        pytest.skip("set AGOS_INTEGRATION=1 to run the real Multica smoke test")
    if not _multica_ready():
        pytest.skip("real Multica daemon/workspace not reachable")


def test_round_trip(tmp_repo: Path):
    _run("agos", "init", "--executor", "multica", "--agent", _integration_agent(), cwd=tmp_repo)
    _run("agos", "start", "--title", _integration_title(tmp_repo), "--workflow", "docs_only", cwd=tmp_repo)

    task_yaml = tmp_repo / ".agos" / "tasks" / "current" / "task.yaml"
    assert task_yaml.is_file()

    status_path = tmp_repo / ".agos" / "tasks" / "current" / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    run_id = status["executor_run"]["run_id"]
    _wait_for_messages(run_id, cwd=tmp_repo)

    _run("agos", "checkpoint", "--once", cwd=tmp_repo)
    ci = _run("agos", "ci", "--local", "--stage", "pre-commit", cwd=tmp_repo)
    assert ci.returncode == 0

    ledger_path = tmp_repo / ".agos" / "tasks" / "current" / "ledger.jsonl"
    assert ledger_path.is_file()
    ledger_records = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(record["type"] == "checkpoint" for record in ledger_records)

    evidence_dir = tmp_repo / ".agos" / "tasks" / "current" / "evidence"
    message_files = list((evidence_dir / "messages").glob("*.jsonl"))
    anchor_files = list((evidence_dir / "repo_anchor").glob("*.json"))
    assert message_files
    assert anchor_files

    anchor = json.loads(anchor_files[0].read_text(encoding="utf-8"))
    assert anchor["claim"] is None
