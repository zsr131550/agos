from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from agos.core.adapter import ExecutorRun
from agos.core.config import AGOSConfig, ReviewerConfig, WorkerConfig
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
from agos.core.repo import repo_paths, task_paths
from agos.core.review import ReviewPacket, ReviewReport
from agos.core.review_store import ReviewStore
from agos.core.status import TaskStatus, load_status, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.web.api import (
    DashboardApiError,
    archive_current_task_payload,
    agents_payload,
    config_payload,
    continue_archived_task_payload,
    current_run_payload,
    candidates_payload,
    error_payload,
    execution_payload,
    evidence_payload,
    health_payload,
    pause_current_task_payload,
    review_run_payload,
    restart_current_task_payload,
    resume_current_task_payload,
    runs_payload,
    start_run_payload,
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


def test_agents_payload_lists_configured_task_and_review_agents(monkeypatch, dashboard_repo: Path) -> None:
    monkeypatch.setattr("agos.web.api.shutil.which", lambda _command: None)

    payload = agents_payload(dashboard_repo)

    assert payload["ok"] is True
    assert [agent["id"] for agent in payload["task_agents"]] == [
        "executor:codex_cli:codex",
        "worker:docs_agent",
    ]
    assert payload["task_agents"][0]["selected"] is True
    assert payload["task_agents"][0]["adapter"] == "codex_cli"
    assert payload["task_agents"][1]["label"] == "docs_agent"
    assert payload["review_agents"][0]["id"] == "reviewer:security"
    assert payload["review_agents"][0]["role"] == "security_reviewer"


def test_agents_payload_discovers_local_agent_commands(monkeypatch, dashboard_repo: Path) -> None:
    commands = {
        "codex": "C:/bin/codex.cmd",
        "claude": "C:/bin/claude.cmd",
        "multica": "C:/bin/multica.cmd",
    }
    monkeypatch.setattr("agos.web.api.shutil.which", lambda command: commands.get(command))

    payload = agents_payload(dashboard_repo)

    local_agents = [agent for agent in payload["task_agents"] if agent["source"] == "local"]
    assert [agent["id"] for agent in local_agents] == [
        "local:codex_cli:codex",
        "local:claude_code:claude",
        "local:multica:codex",
    ]
    assert all(agent["available"] is True for agent in local_agents)


def test_agents_payload_discovers_local_review_agent_commands(monkeypatch, tmp_repo: Path) -> None:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    AGOSConfig.default(agent="codex").save(paths.agos_yaml)
    commands = {
        "codex": "C:/bin/codex.cmd",
        "claude": "C:/bin/claude.cmd",
    }
    monkeypatch.setattr("agos.web.api.shutil.which", lambda command: commands.get(command))

    payload = agents_payload(tmp_repo)

    assert [agent["id"] for agent in payload["review_agents"]] == [
        "local:reviewer:codex_cli",
        "local:reviewer:claude_code",
    ]
    assert payload["review_agents"][0]["label"] == "codex review"
    assert payload["review_agents"][1]["label"] == "claude review"


def test_start_run_payload_uses_selected_task_agent(tmp_repo: Path, monkeypatch) -> None:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    AGOSConfig.default(
        executor="multica",
        agent="Lambda",
        workers={
            "codex_local": WorkerConfig(type="codex_cli", command="codex"),
        },
    ).save(paths.agos_yaml)
    captured = {}

    def fake_start(self, task):
        captured["adapter"] = self.name
        captured["task_executor"] = task.executor.model_dump()
        return ExecutorRun(adapter=self.name, run_id="codex-local-run", issue_id=None)

    monkeypatch.setattr("agos.adapters.local_cli_executor.CodexCliExecutorAdapter.start", fake_start)

    payload = start_run_payload(
        tmp_repo,
        {
            "title": "Use selected agent",
            "agent": "worker:codex_local",
        },
    )

    assert payload["ok"] is True
    assert captured == {
        "adapter": "codex_cli",
        "task_executor": {"adapter": "codex_cli", "agent": "codex_local"},
    }
    task = yaml.safe_load(paths.task_yaml.read_text(encoding="utf-8"))
    assert task["executor"] == {"adapter": "codex_cli", "agent": "codex_local"}


def test_archive_current_task_payload_moves_active_task_to_archive(dashboard_repo: Path) -> None:
    paths = repo_paths(dashboard_repo)

    payload = archive_current_task_payload(dashboard_repo)

    assert payload["ok"] is True
    assert payload["archived_task_id"] == "agos-dashboard-01"
    archive_path = Path(payload["archive_path"])
    assert archive_path.is_dir()
    assert archive_path.parent == paths.tasks / "archive"
    assert (archive_path / "task.yaml").is_file()
    assert not paths.current_task.exists()
    assert load_status(task_paths(dashboard_repo, archive_path)).phase == "done"


def test_runs_payload_lists_archived_tasks(dashboard_repo: Path) -> None:
    archived = archive_current_task_payload(dashboard_repo)

    payload = runs_payload(dashboard_repo)

    assert payload["current_run_id"] is None
    assert payload["runs"][0]["id"] == "agos-dashboard-01"
    assert payload["runs"][0]["scope"] == "archived"
    assert payload["runs"][0]["archive_id"] == Path(archived["archive_path"]).name
    assert payload["runs"][0]["title"] == "构建可视化控制台"
    assert payload["runs"][0]["phase"] == "done"


def test_runs_payload_uses_completed_executor_evidence_for_archived_phase(dashboard_repo: Path) -> None:
    paths = repo_paths(dashboard_repo)
    run_id = "run-01"
    run_state = paths.evidence / "executor_runs" / f"{run_id}.json"
    run_state.parent.mkdir(parents=True, exist_ok=True)
    run_state.write_text(
        json.dumps({"run_id": run_id, "adapter": "codex_cli", "state": "completed"}),
        encoding="utf-8",
    )
    archived = archive_current_task_payload(dashboard_repo)
    archived_paths = task_paths(dashboard_repo, Path(archived["archive_path"]))
    status = load_status(archived_paths)
    status.phase = "executing"  # type: ignore[assignment]
    save_status(status, archived_paths)

    payload = runs_payload(dashboard_repo)

    assert payload["runs"][0]["phase"] == "done"


def test_runs_payload_uses_completed_executor_evidence_for_current_phase(dashboard_repo: Path) -> None:
    paths = repo_paths(dashboard_repo)
    run_id = "run-01"
    run_state = paths.evidence / "executor_runs" / f"{run_id}.json"
    run_state.parent.mkdir(parents=True, exist_ok=True)
    run_state.write_text(
        json.dumps({"run_id": run_id, "adapter": "codex_cli", "state": "completed"}),
        encoding="utf-8",
    )

    payload = runs_payload(dashboard_repo)
    current = current_run_payload(dashboard_repo)

    assert payload["runs"][0]["phase"] == "done"
    assert current["run"]["phase"] == "done"
    assert load_status(paths).phase == "done"


def test_restart_payload_keeps_explicit_restarted_phase_for_completed_output(
    dashboard_repo: Path,
) -> None:
    paths = repo_paths(dashboard_repo)
    run_id = "run-01"
    run_state = paths.evidence / "executor_runs" / f"{run_id}.json"
    run_state.parent.mkdir(parents=True, exist_ok=True)
    run_state.write_text(
        json.dumps({"run_id": run_id, "adapter": "codex_cli", "state": "completed"}),
        encoding="utf-8",
    )
    assert current_run_payload(dashboard_repo)["run"]["phase"] == "done"

    restarted = restart_current_task_payload(dashboard_repo)
    current = current_run_payload(dashboard_repo)

    assert restarted["run"]["phase"] == "executing"
    assert current["run"]["phase"] == "executing"
    status = load_status(paths)
    assert status is not None
    assert status.phase == "executing"
    assert status.last_event_seq is not None


def test_completed_executor_without_outputs_is_blocked_not_done(dashboard_repo: Path) -> None:
    paths = repo_paths(dashboard_repo)
    shutil.rmtree(paths.current_task / "execution")
    run_id = "run-01"
    run_state = paths.evidence / "executor_runs" / f"{run_id}.json"
    run_state.parent.mkdir(parents=True, exist_ok=True)
    run_state.write_text(
        json.dumps({"run_id": run_id, "adapter": "codex_cli", "state": "completed"}),
        encoding="utf-8",
    )

    payload = runs_payload(dashboard_repo)
    current = current_run_payload(dashboard_repo)

    assert payload["runs"][0]["phase"] == "blocked"
    assert current["run"]["phase"] == "blocked"
    assert load_status(paths).phase == "blocked"


def test_completed_executor_without_outputs_stays_blocked_after_lifecycle_actions(
    dashboard_repo: Path,
) -> None:
    paths = repo_paths(dashboard_repo)
    shutil.rmtree(paths.current_task / "execution")
    output_dir = paths.root / "outputs" / "agos-dashboard-01"
    shutil.rmtree(output_dir, ignore_errors=True)
    run_id = "run-01"
    run_state = paths.evidence / "executor_runs" / f"{run_id}.json"
    run_state.parent.mkdir(parents=True, exist_ok=True)
    run_state.write_text(
        json.dumps({"run_id": run_id, "adapter": "codex_cli", "state": "completed"}),
        encoding="utf-8",
    )
    assert current_run_payload(dashboard_repo)["run"]["phase"] == "blocked"

    resumed = resume_current_task_payload(dashboard_repo)
    restarted = restart_current_task_payload(dashboard_repo)
    current = current_run_payload(dashboard_repo)

    assert resumed["run"]["phase"] == "blocked"
    assert restarted["run"]["phase"] == "blocked"
    assert current["run"]["phase"] == "blocked"
    status = load_status(paths)
    assert status is not None
    assert status.phase == "blocked"
    assert status.last_event_seq is None
    records = Ledger(paths.ledger).read_all()
    lifecycle_records = [
        record
        for record in records
        if record["type"] in {"dashboard_resumed", "dashboard_restarted"}
    ]
    assert [record["phase"] for record in lifecycle_records[-2:]] == ["blocked", "blocked"]
    assert [record["requested_phase"] for record in lifecycle_records[-2:]] == [
        "executing",
        "executing",
    ]


def test_continue_archived_task_payload_restores_archive_as_current(dashboard_repo: Path) -> None:
    paths = repo_paths(dashboard_repo)
    archived = archive_current_task_payload(dashboard_repo)
    archive_id = Path(archived["archive_path"]).name

    payload = continue_archived_task_payload(dashboard_repo, archive_id)

    assert payload["ok"] is True
    assert payload["run"]["id"] == "agos-dashboard-01"
    assert paths.task_yaml.is_file()
    assert not Path(archived["archive_path"]).exists()


def test_current_task_lifecycle_payloads_update_phase(dashboard_repo: Path) -> None:
    paths = repo_paths(dashboard_repo)

    paused = pause_current_task_payload(dashboard_repo)
    assert paused["run"]["phase"] == "blocked"
    assert load_status(paths).phase == "blocked"

    resumed = resume_current_task_payload(dashboard_repo)
    assert resumed["run"]["phase"] == "executing"
    assert load_status(paths).phase == "executing"

    restarted = restart_current_task_payload(dashboard_repo)
    assert restarted["run"]["phase"] == "executing"
    assert load_status(paths).phase == "executing"
    records = Ledger(paths.ledger).read_all()
    assert [record["type"] for record in records[-3:]] == [
        "dashboard_paused",
        "dashboard_resumed",
        "dashboard_restarted",
    ]


def test_start_run_payload_can_replace_active_task(dashboard_repo: Path, monkeypatch) -> None:
    paths = repo_paths(dashboard_repo)
    captured = {}

    def fake_start(self, task):
        captured["title"] = task.title
        return ExecutorRun(adapter=self.name, run_id="replace-run", issue_id=None)

    monkeypatch.setattr("agos.adapters.local_cli_executor.CodexCliExecutorAdapter.start", fake_start)

    payload = start_run_payload(
        dashboard_repo,
        {
            "title": "Replace active task",
            "replace_active": True,
            "agent": "executor:codex_cli:codex",
        },
    )

    assert payload["ok"] is True
    assert payload["run"]["title"] == "Replace active task"
    assert captured["title"] == "Replace active task"
    assert (paths.tasks / "archive").is_dir()
    assert paths.task_yaml.is_file()
    assert yaml.safe_load(paths.task_yaml.read_text(encoding="utf-8"))["title"] == "Replace active task"


def test_review_run_payload_uses_selected_review_agent(dashboard_repo: Path) -> None:
    payload = review_run_payload(dashboard_repo, {"reviewer": "reviewer:security"})

    assert payload["ok"] is True
    assert payload["review_run"]["state"] == "completed"
    assert payload["review_run"]["reviewers"] == ["security"]


def test_review_run_payload_accepts_local_review_agent(dashboard_repo: Path, monkeypatch) -> None:
    captured = {}

    def fake_run(self, *, run_id, packet, reviewers, max_parallel=4):
        captured["adapter_names"] = sorted(self._reviewers)
        captured["reviewers"] = [reviewer.model_dump() for reviewer in reviewers]
        from agos.core.review_orchestrator import ReviewRunResult

        return ReviewRunResult(run_id=run_id, state="completed")

    monkeypatch.setattr("agos.core.review_orchestrator.ParallelReviewOrchestrator.run", fake_run)

    payload = review_run_payload(dashboard_repo, {"reviewer": "local:reviewer:codex_cli"})

    assert payload["ok"] is True
    assert captured["adapter_names"] == ["local_codex_cli"]
    assert captured["reviewers"][0]["id"] == "local_codex_cli"
    assert captured["reviewers"][0]["role"] == "codex_reviewer"
    assert payload["review_run"]["reviewers"] == ["local_codex_cli"]


def test_runs_and_current_run_payloads_include_pipeline_state(dashboard_repo: Path) -> None:
    runs = runs_payload(dashboard_repo)
    current = current_run_payload(dashboard_repo)

    assert runs["runs"] == [
        {
            "id": "agos-dashboard-01",
            "title": "构建可视化控制台",
            "workflow": "feature",
            "phase": "executing",
            "scope": "current",
        }
    ]
    assert current["run"]["id"] == "agos-dashboard-01"
    assert current["run"]["title"] == "构建可视化控制台"
    assert current["run"]["workflow"] == "feature"
    assert current["run"]["phase"] == "executing"
    assert Path(current["run"]["output_dir"]).parts[-2:] == ("outputs", "agos-dashboard-01")
    assert current["run"]["output_ref"] == "outputs/agos-dashboard-01"
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


def test_current_run_payload_maps_agent_returned_options_to_candidates(
    dashboard_repo: Path,
) -> None:
    paths = repo_paths(dashboard_repo)
    ledger = Ledger(paths.ledger)
    ledger.append(
        {
            "type": "executor_completed",
            "run_id": "run-01",
            "state": "completed",
            "detail": "\n".join(
                [
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {
                                "type": "agent_message",
                                "text": (
                                    "方案 A：Add read-only dashboard API payloads\n"
                                    "方案 B：Only update copywriting"
                                ),
                            },
                        },
                        ensure_ascii=False,
                    )
                ]
            ),
        }
    )

    payload = current_run_payload(dashboard_repo)

    options = payload["run"]["agent_options"]["options"]
    assert payload["run"]["agent_options"]["count"] == 2
    assert options[0]["id"] == "option-1"
    assert options[0]["title"] == "方案 A"
    assert options[0]["summary"] == "Add read-only dashboard API payloads"
    assert options[0]["source_run_id"] == "run-01"
    assert options[0]["mapped_candidate_id"] == "candidate-01"
    assert options[0]["mapped_candidate_status"] == "accepted"
    assert options[1]["mapped_candidate_id"] is None
    assert payload["agent_options"]["options"][0]["mapped_candidate_id"] == "candidate-01"


def test_runs_payload_returns_empty_list_without_active_task(tmp_repo: Path) -> None:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    AGOSConfig.default(agent="codex").save(paths.agos_yaml)

    payload = runs_payload(tmp_repo)

    assert payload["ok"] is True
    assert payload["current_run_id"] is None
    assert payload["runs"] == []


def test_current_run_payload_uses_strict_merge_gate_for_missing_candidate_review(
    dashboard_repo: Path,
) -> None:
    paths = repo_paths(dashboard_repo)
    store = ExecutionStore(paths)
    candidate = store.read_candidates()[0]
    Ledger(paths.ledger).append(
        {
            "type": "candidate_patch_created",
            "task_id": candidate.task_id,
            "subtask_id": candidate.subtask_id,
            "candidate_id": candidate.id,
            "patch_ref": candidate.patch_ref,
            "patch_sha256": candidate.patch_sha256,
        }
    )
    store.write_test_run(
        CandidateTestRun(
            id="test-patch-applies",
            candidate_id=candidate.id,
            gate_id="patch_applies",
            command="git apply --check",
            state="passed",
            evidence_ref="evidence/gates/patch_applies.log",
            workspace_ref=candidate.workspace_ref,
        )
    )

    payload = current_run_payload(dashboard_repo)

    assert payload["merge_gate"]["passed"] is False
    candidate_evidence = next(
        check for check in payload["merge_gate"]["checks"] if check["name"] == "candidate_evidence"
    )
    assert candidate_evidence["state"] == "block"
    assert any("completed clean" in detail for detail in candidate_evidence["details"])


def test_candidates_payload_rejects_patch_ref_path_escape(dashboard_repo: Path) -> None:
    paths = repo_paths(dashboard_repo)
    store = ExecutionStore(paths)
    candidate = store.read_candidates()[0]
    outside_patch = paths.tasks / "outside.patch"
    outside_patch.parent.mkdir(parents=True, exist_ok=True)
    outside_patch.write_text("diff --git a/secret b/secret\n", encoding="utf-8")
    candidate.patch_ref = "evidence/../../outside.patch"
    store.write_candidate(candidate)

    payload = candidates_payload(dashboard_repo)

    assert payload["candidates"][0]["patch_exists"] is False


def test_execution_payload_reports_bad_json_rows_without_failing(dashboard_repo: Path) -> None:
    paths = repo_paths(dashboard_repo)
    good = paths.current_task / "execution" / "bundle_decisions" / "good.json"
    bad = paths.current_task / "execution" / "bundle_decisions" / "bad.json"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_text('{"id":"good"}', encoding="utf-8")
    bad.write_text('{', encoding="utf-8")

    payload = execution_payload(dashboard_repo)

    assert {row.get("id") for row in payload["bundle_decisions"]} >= {"good"}
    errors = [row for row in payload["bundle_decisions"] if row.get("_error")]
    assert errors
    assert errors[0]["path"] == "execution/bundle_decisions/bad.json"


def test_execution_payload_reports_schema_invalid_subtasks_without_failing(
    dashboard_repo: Path,
) -> None:
    paths = repo_paths(dashboard_repo)
    bad = paths.current_task / "execution" / "subtasks" / "bad.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"id":"bad"}', encoding="utf-8")

    payload = execution_payload(dashboard_repo)

    errors = [row for row in payload["subtasks"] if row.get("_error")]
    assert errors
    assert errors[0]["path"] == "execution/subtasks/bad.json"


def test_candidates_payload_reports_schema_invalid_tests_and_decisions_without_failing(
    dashboard_repo: Path,
) -> None:
    paths = repo_paths(dashboard_repo)
    bad_test = paths.current_task / "execution" / "tests" / "bad.json"
    bad_decision = paths.current_task / "execution" / "decisions" / "bad.json"
    bad_test.parent.mkdir(parents=True, exist_ok=True)
    bad_decision.parent.mkdir(parents=True, exist_ok=True)
    bad_test.write_text('{"id":"bad-test"}', encoding="utf-8")
    bad_decision.write_text('{"id":"bad-decision"}', encoding="utf-8")

    payload = candidates_payload(dashboard_repo)

    assert payload["count"] == 1
    assert payload["test_errors"][0]["path"] == "execution/tests/bad.json"
    assert payload["decision_errors"][0]["path"] == "execution/decisions/bad.json"


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
        executor="codex_cli",
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
    config.reviewers = {
        "security": ReviewerConfig(type="fake", role="security_reviewer", required=True)
    }
    config.allow_fake_reviewer = True
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
