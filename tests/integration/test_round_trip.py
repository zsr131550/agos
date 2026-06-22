"""End-to-end smoke: init -> start -> checkpoint --once -> ci --local.

Skipped unless AGOS_INTEGRATION=1 and a real Multica daemon/workspace are
reachable. This creates a real Multica issue assigned to the configured agent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agos.adapters.multica import resolve_multica_bin

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
AGOS_CLI = (sys.executable, "-m", "agos.cli.main")
TERMINAL_RUN_STATUSES = {"completed", "done", "blocked", "cancelled", "failed"}


def _integration_agent() -> str:
    agent = os.environ.get("AGOS_INTEGRATION_AGENT", "").strip()
    if not agent:
        pytest.skip("set AGOS_INTEGRATION_AGENT to run the real Multica smoke test")
    return agent


def _integration_title(repo_root: Path) -> str:
    return f"smoke-{repo_root.parent.name}"


def _extract_messages(payload: dict | list) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return payload.get("messages", [])


def _extract_runs(payload: dict | list) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return payload.get("runs", [])


def _run(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    pythonpath = str(SRC_ROOT)
    if os.environ.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + os.environ["PYTHONPATH"]
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": pythonpath},
    )


def _multica_ready() -> bool:
    multica_bin = resolve_multica_bin()
    if shutil.which(multica_bin) is None and not Path(multica_bin).exists():
        return False

    daemon_status = subprocess.run(
        [multica_bin, "daemon", "status"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if daemon_status.returncode != 0 or "running" not in daemon_status.stdout.lower():
        return False

    workspace_status = subprocess.run(
        [multica_bin, "workspace", "list", "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if workspace_status.returncode != 0:
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


def _wait_for_terminal_run(
    run_id: str,
    *,
    issue_id: str,
    cwd: Path,
    timeout_seconds: int | None = None,
) -> dict:
    if timeout_seconds is None:
        timeout_seconds = int(os.environ.get("AGOS_INTEGRATION_TIMEOUT_SECONDS", "300"))
    multica_bin = resolve_multica_bin()
    deadline = time.monotonic() + timeout_seconds
    last_stdout = ""
    last_stderr = ""
    last_run: dict = {}

    while time.monotonic() < deadline:
        completed = _run(
            multica_bin,
            "issue",
            "runs",
            issue_id,
            "--output",
            "json",
            cwd=cwd,
            check=False,
        )
        last_stdout = completed.stdout
        last_stderr = completed.stderr
        if completed.returncode == 0:
            try:
                payload = json.loads(completed.stdout or "[]")
            except json.JSONDecodeError as exc:
                last_stderr = f"{completed.stderr}\ninvalid JSON: {exc.msg}".strip()
            else:
                runs = _extract_runs(payload)
                matching = next((run for run in runs if run.get("id") == run_id), None)
                if matching is None and runs:
                    matching = runs[0]
                if matching is not None:
                    last_run = matching
                    if str(matching.get("status", "")) in TERMINAL_RUN_STATUSES:
                        return matching
        time.sleep(2)

    pytest.fail(
        "timed out waiting for Multica run to finish; "
        f"issue_id={issue_id!r} run_id={run_id!r} "
        f"status={last_run.get('status')!r} "
        f"started_at={last_run.get('started_at')!r} "
        f"completed_at={last_run.get('completed_at')!r} "
        f"stdout={last_stdout!r} stderr={last_stderr!r}"
    )


@pytest.fixture(autouse=True)
def _skip_unless_opted_in():
    if os.environ.get("AGOS_INTEGRATION") != "1":
        pytest.skip("set AGOS_INTEGRATION=1 to run the real Multica smoke test")
    if not _multica_ready():
        pytest.skip("real Multica daemon/workspace not reachable")


def test_round_trip(tmp_repo: Path):
    _run(*AGOS_CLI, "init", "--executor", "multica", "--agent", _integration_agent(), cwd=tmp_repo)
    _run(*AGOS_CLI, "start", "--title", _integration_title(tmp_repo), "--workflow", "docs_only", cwd=tmp_repo)

    task_yaml = tmp_repo / ".agos" / "tasks" / "current" / "task.yaml"
    assert task_yaml.is_file()

    status_path = tmp_repo / ".agos" / "tasks" / "current" / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    run_id = status["executor_run"]["run_id"]
    issue_id = status["executor_run"]["issue_id"]
    terminal_run = _wait_for_terminal_run(run_id, issue_id=issue_id, cwd=tmp_repo)
    assert terminal_run["status"] in {"completed", "done"}

    _run(*AGOS_CLI, "checkpoint", "--once", cwd=tmp_repo)
    # Real Multica runs may finish without a synthetic run_complete message.
    _run(*AGOS_CLI, "checkpoint", "--once", cwd=tmp_repo)
    ci = _run(*AGOS_CLI, "ci", "--local", "--stage", "pre-commit", cwd=tmp_repo)
    assert ci.returncode == 0

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["phase"] == "done"

    ledger_path = tmp_repo / ".agos" / "tasks" / "current" / "ledger.jsonl"
    assert ledger_path.is_file()
    ledger_records = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(record["type"] == "checkpoint" for record in ledger_records)
    assert any(record["type"] == "executor_completed" for record in ledger_records)

    evidence_dir = tmp_repo / ".agos" / "tasks" / "current" / "evidence"
    message_files = list((evidence_dir / "messages").glob("*.jsonl"))
    anchor_files = list((evidence_dir / "repo_anchor").glob("*.json"))
    assert message_files
    assert anchor_files

    anchor = json.loads(anchor_files[0].read_text(encoding="utf-8"))
    assert anchor["claim"] is None
