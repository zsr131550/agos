from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agos.core.adapter import ExecutorRun
from agos.core.config import AGOSConfig, WorkerConfig
from agos.core.execution import (
    ArbiterDecision,
    CandidatePatch,
    CandidateTestRun,
    ExecutionPlan,
    ExecutionSubtask,
    ExecutionWorker,
)
from agos.core.execution_store import ExecutionStore
from agos.core.gate import gates_locked_payload
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.review import ReviewPacket, ReviewReport
from agos.core.review_store import ReviewStore
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.web.api import (
    DashboardApiError,
    config_payload,
    current_run_payload,
    error_payload,
    evidence_payload,
    health_payload,
    runs_payload,
    status_payload,
)


def test_health_payload_reports_uninitialized_repo(tmp_repo: Path) -> None:
    payload = health_payload(tmp_repo)

    assert payload["ok"] is True
    assert payload["initialized"] is False
    assert payload["repo_root"] == str(tmp_repo)


def test_config_payload_redacts_sensitive_config_values(dashboard_repo: Path) -> None:
    payload = config_payload(dashboard_repo)

    worker = payload["config"]["workers"]["docs_agent"]
    assert worker["type"] == "codex_cli"
    assert worker["command"] == "codex"
    assert worker["token"] == "***REDACTED***"
    assert worker["env"]["API_KEY"] == "***REDACTED***"
    assert worker["env"]["PUBLIC_NAME"] == "docs"


def test_runs_and_current_run_payloads_include_pipeline_state(dashboard_repo: Path) -> None:
    runs = runs_payload(dashboard_repo)
    current = current_run_payload(dashboard_repo)

    assert runs["runs"] == [
        {
            "id": "agos-dashboard-01",
            "title": "构建可视化控制台",
            "workflow": "feature",
            "phase": "executing",
        }
    ]
    assert current["run"]["id"] == "agos-dashboard-01"
    assert current["run"]["title"] == "构建可视化控制台"
    assert current["run"]["workflow"] == "feature"
    assert current["run"]["phase"] == "executing"
    assert current["run"]["task"]["workflow"] == "feature"
    assert current["run"]["status"]["phase"] == "executing"
    assert current["run"]["execution"]["plan"]["id"] == "plan-dashboard-01"
    assert current["run"]["candidates"]["candidates"][0]["id"] == "candidate-01"
    assert current["run"]["pipeline"]["candidates_count"] == 1
    assert current["task"]["workflow"] == "feature"
    assert current["execution"]["plan"]["id"] == "plan-dashboard-01"
    assert current["execution"]["subtasks"][0]["worker"]["adapter"] == "docs_agent"
    assert current["candidates"]["candidates"][0]["id"] == "candidate-01"
    assert current["pipeline"]["candidates_count"] == 1
    assert current["candidates"]["candidates"][0]["patch_exists"] is True
    assert current["candidates"]["candidates"][0]["tests"][0]["gate_id"] == "tests_pass"
    assert current["candidates"]["candidates"][0]["decisions"][0]["decision"] == "accepted"
    assert current["reviews"]["packets"][0]["review_id"] == "review-01"
    assert current["reviews"]["reports"][0]["review_id"] == "review-01"


def test_evidence_payload_reads_safe_evidence_ref(dashboard_repo: Path) -> None:
    payload = evidence_payload(dashboard_repo, "evidence/gates/tests_pass.log")

    assert payload["ok"] is True
    assert payload["text"] == "ok\n"


def test_evidence_payload_rejects_invalid_ref(dashboard_repo: Path) -> None:
    with pytest.raises(DashboardApiError) as err:
        evidence_payload(dashboard_repo, "../README.md")

    assert err.value.code == "invalid_evidence_ref"
    assert error_payload(err.value) == {
        "ok": False,
        "error": {"code": "invalid_evidence_ref", "message": err.value.message},
    }


def test_current_run_payload_is_json_serializable_and_preserves_chinese_title(
    dashboard_repo: Path,
) -> None:
    payload = current_run_payload(dashboard_repo)

    encoded = json.dumps(payload, ensure_ascii=False)
    assert "构建可视化控制台" in encoded
    assert json.loads(encoded)["run"]["title"] == "构建可视化控制台"


def test_config_and_status_payloads_require_active_task(tmp_repo: Path) -> None:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    AGOSConfig.default(agent="codex").save(paths.agos_yaml)

    for payload_func in (config_payload, status_payload):
        with pytest.raises(DashboardApiError) as err:
            payload_func(tmp_repo)

        assert err.value.code == "active_task_missing"


@pytest.fixture
def dashboard_repo(tmp_repo: Path) -> Path:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    config = AGOSConfig.default(
        agent="codex",
        workers={
            "docs_agent": WorkerConfig(
                type="codex_cli",
                command="codex",
                token="secret-token",
                env={"API_KEY": "secret-api-key", "PUBLIC_NAME": "docs"},
            )
        },
    )
    config.save(paths.agos_yaml)

    task = Task(
        id="agos-dashboard-01",
        title="构建可视化控制台",
        intent="展示 AGOS 流水线",
        workflow="feature",
        gates=["tests_pass"],
        executor=ExecutorBinding(adapter="codex_cli", agent="codex"),
    )
    save_task(task, paths.task_yaml)

    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    ledger.append(
        {
            "type": "gates_locked",
            "task_id": task.id,
            "gates": gates_locked_payload(config.resolve_gates(task.workflow, task.gates)),
        }
    )
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="codex_cli", run_id="run-01", issue_id=None),
            ledger_head_hash=started["hash"],
        ),
        paths,
    )

    store = ExecutionStore(paths)
    subtask = ExecutionSubtask(
        id="subtask-dashboard-api",
        title="Build API payloads",
        intent="Expose read-only dashboard state",
        write_scope=["src/agos/web/api.py", "tests/web/test_api.py"],
        worker=ExecutionWorker(adapter="docs_agent"),
        status="running",
    )
    store.write_plan(
        ExecutionPlan(
            id="plan-dashboard-01",
            task_id=task.id,
            max_parallel=1,
            requires_candidate_review=True,
            subtasks=[subtask],
        )
    )
    store.write_subtask(subtask)
    patch_ref, patch_sha = store.write_candidate_patch("candidate-01", b"diff --git a/x b/x\n")
    test_ref = store.write_test_run(
        CandidateTestRun(
            id="test-01",
            candidate_id="candidate-01",
            gate_id="tests_pass",
            command="pytest -q",
            state="passed",
            evidence_ref="evidence/gates/tests_pass.log",
            workspace_ref="execution/workspaces/subtask-dashboard-api.json",
        )
    )
    decision_ref = store.write_decision(
        ArbiterDecision(
            id="decision-01",
            candidate_id="candidate-01",
            decision="accepted",
            reason="tests passed",
            evidence_refs=[test_ref],
            decided_by="arbiter",
        )
    )
    base_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_repo, text=True).strip()
    store.write_candidate(
        CandidatePatch(
            id="candidate-01",
            task_id=task.id,
            subtask_id=subtask.id,
            source_agent="docs_agent",
            workspace_ref="execution/workspaces/subtask-dashboard-api.json",
            patch_ref=patch_ref,
            patch_sha256=patch_sha,
            base_commit=base_commit,
            summary="Add read-only dashboard API payloads",
            status="accepted",
            test_refs=[test_ref],
            decision_ref=decision_ref,
        )
    )

    review_store = ReviewStore(paths)
    packet_ref = review_store.write_packet(
        ReviewPacket(
            review_id="review-01",
            task_id=task.id,
            task_title=task.title,
            task_intent=task.intent,
            subject={"candidate_id": "candidate-01"},
            diff_kind="candidate_patch",
            diff_evidence_ref=patch_ref,
            ledger_head_hash=ledger.head_hash(),
        )
    )
    review_store.write_report(
        ReviewReport(review_id="review-01", task_id=task.id, packet_ref=packet_ref, findings=[])
    )

    evidence_path = paths.evidence / "gates" / "tests_pass.log"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(b"ok\n")
    return tmp_repo
