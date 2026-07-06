"""Dashboard API payload builders."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agos.adapters.reviewers import LlmCliReviewerAdapter
from agos.cli.cmd_review import run_review
from agos.cli.cmd_start import StartTaskError, start_task
from agos.cli.cmd_start import ExecutorSelection
from agos.core.config import load_config
from agos.core.execution import ArbiterDecision, CandidatePatch, CandidateTestRun, ExecutionSubtask
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger
from agos.core.merge_gate import verify_merge_gate
from agos.core.review_orchestrator import ParallelReviewOrchestrator, ReviewerSpec
from agos.core.review_service import ReviewService
from agos.core.review_store import ReviewStore
from agos.core.repo import AgosPaths, git_head, repo_paths
from agos.core.status import load_status
from agos.core.task import load_task, task_output_ref
from agos.web.evidence import EvidenceResolutionError, read_evidence_text, resolve_evidence_ref


class DashboardApiError(RuntimeError):
    """Structured dashboard API error for server-side conversion."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def payload(self) -> dict[str, object]:
        error: dict[str, object] = {"code": self.code, "message": self.message}
        if self.hint is not None:
            error["hint"] = self.hint
        return {"ok": False, "error": error}


_SECRET_KEY_RE = re.compile(
    r"(token|secret|password|api[_-]?key|access[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_REDACTED = "***REDACTED***"


def health_payload(repo_root: Path) -> dict[str, object]:
    """Return dashboard health without requiring an initialized AGOS repo."""

    root = Path(repo_root)
    paths = repo_paths(root)
    payload: dict[str, object] = {
        "ok": True,
        "repo_root": str(root),
        "initialized": paths.agos_yaml.is_file(),
        "agos_dir": str(paths.agos_dir),
    }
    try:
        payload["head_hash"] = git_head(root)
    except Exception as exc:  # health must not fail for uninitialized/non-git probes
        payload["head_hash"] = None
        payload["git_error"] = str(exc)
    return payload


def config_payload(repo_root: Path) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    _load_current_task(paths)
    config = load_config(paths.root)
    return {
        "ok": True,
        "repo_root": str(paths.root),
        "config": _redact(config.model_dump(mode="json")),
    }


def agents_payload(repo_root: Path) -> dict[str, object]:
    """Return configured local task/review agents for dashboard selectors."""

    paths = _require_initialized(repo_root)
    config = load_config(paths.root)
    return {
        "ok": True,
        "repo_root": str(paths.root),
        "task_agents": _task_agent_rows(config),
        "review_agents": _review_agent_rows(config),
    }


def status_payload(repo_root: Path) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    _load_current_task(paths)
    status = load_status(paths)
    return {
        "ok": True,
        "repo_root": str(paths.root),
        "status_present": status is not None,
        "status": _dump_model(status) if status is not None else None,
    }


def runs_payload(repo_root: Path) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    if not paths.task_yaml.is_file():
        return {
            "ok": True,
            "repo_root": str(paths.root),
            "current_run_id": None,
            "runs": [],
        }
    task = _load_current_task(paths)
    status = load_status(paths)
    phase = status.phase if status is not None else "unknown"
    return {
        "ok": True,
        "repo_root": str(paths.root),
        "current_run_id": task.id,
        "runs": [
            {
                "id": task.id,
                "title": task.title,
                "workflow": task.workflow,
                "phase": phase,
            }
        ],
    }


def current_run_payload(repo_root: Path) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    task = _load_current_task(paths)
    status = load_status(paths)
    if status is None:
        raise DashboardApiError(
            "status_missing",
            "Current AGOS task status is missing.",
            hint="Run AGOS task start/status recovery before opening the dashboard.",
        )

    ledger = ledger_payload(paths.root)
    execution = execution_payload(paths.root)
    candidates = candidates_payload(paths.root)
    reviews = reviews_payload(paths.root)
    merge_gate = _merge_gate_payload(paths)
    execution_plan = execution.get("plan") if isinstance(execution.get("plan"), dict) else None
    candidate_rows = candidates.get("candidates", [])

    task_payload = task.model_dump(mode="json")
    status_payload_value = status.model_dump(mode="json")
    run = {
        "id": task.id,
        "title": task.title,
        "workflow": task.workflow,
        "phase": status.phase,
        "executor_run": _dump_model(status.executor_run),
        "output_ref": task_output_ref(task),
        "output_dir": str(paths.root / task_output_ref(task)),
    }
    pipeline = {
        "task_id": task.id,
        "workflow": task.workflow,
        "phase": status.phase,
        "execution_plan_id": execution_plan.get("id") if execution_plan else None,
        "subtasks_count": len(execution.get("subtasks", [])),
        "candidates_count": candidates.get("count", 0),
        "ledger_verified": ledger.get("verified"),
        "merge_gate_passed": merge_gate.get("passed"),
    }
    distillation = {
        "title": task.title,
        "intent": task.intent,
        "candidate_summaries": [
            item.get("summary") for item in candidate_rows if isinstance(item, dict) and item.get("summary")
        ],
    }
    run.update(
        {
            "task": task_payload,
            "status": status_payload_value,
            "ledger": ledger,
            "execution": execution,
            "candidates": candidates,
            "reviews": reviews,
            "merge_gate": merge_gate,
            "pipeline": pipeline,
            "distillation": distillation,
        }
    )

    return {
        "ok": True,
        "repo_root": str(paths.root),
        "run": run,
        "task": task_payload,
        "status": status_payload_value,
        "ledger": ledger,
        "execution": execution,
        "candidates": candidates,
        "reviews": reviews,
        "merge_gate": merge_gate,
        "pipeline": pipeline,
        "distillation": distillation,
    }


def start_run_payload(repo_root: Path, request_payload: dict[str, Any]) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    title = request_payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise DashboardApiError("invalid_request", "title is required")

    intent = request_payload.get("intent")
    if intent is not None and not isinstance(intent, str):
        raise DashboardApiError("invalid_request", "intent must be a string")

    workflow = request_payload.get("workflow")
    if workflow is not None:
        if not isinstance(workflow, str):
            raise DashboardApiError("invalid_request", "workflow must be a string")
        workflow = workflow.strip() or None
    agent_selection = _resolve_task_agent(paths.root, request_payload.get("agent"))
    replace_active = bool(request_payload.get("replace_active"))
    if replace_active:
        archive_current_task_payload(paths.root)

    try:
        _task, run = start_task(
            repo_root=paths.root,
            title=title.strip(),
            intent=intent.strip() if isinstance(intent, str) else None,
            workflow=workflow,
            gate_overrides=_parse_gate_request(request_payload.get("gates")),
            executor_selection=agent_selection,
        )
    except StartTaskError as exc:
        raise DashboardApiError("start_failed", str(exc)) from exc
    except KeyError as exc:
        raise DashboardApiError("invalid_workflow", str(exc)) from exc

    current = current_run_payload(paths.root)
    return {
        "ok": True,
        "run_id": run.run_id,
        "issue_id": run.issue_id,
        "run": current["run"],
        "current": current,
    }


def archive_current_task_payload(repo_root: Path) -> dict[str, object]:
    """Archive the active current task without deleting its evidence."""

    paths = _require_initialized(repo_root)
    if not paths.current_task.exists() or not any(paths.current_task.iterdir()):
        raise DashboardApiError("active_task_missing", "No active AGOS task is available.")
    task_id = _archive_task_id(paths)
    archive_root = paths.tasks / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_dir = archive_root / f"{_fsafe_name(task_id)}-{_archive_timestamp()}"
    counter = 1
    while archive_dir.exists():
        archive_dir = archive_root / f"{_fsafe_name(task_id)}-{_archive_timestamp()}-{counter}"
        counter += 1
    paths.current_task.rename(archive_dir)
    return {
        "ok": True,
        "archived_task_id": task_id,
        "archive_path": str(archive_dir),
    }


def review_run_payload(repo_root: Path, request_payload: dict[str, Any]) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    reviewers = _parse_reviewer_request(request_payload.get("reviewer"))
    try:
        review_run = (
            _run_local_review_agent(paths, reviewers[0])
            if len(reviewers) == 1 and _is_local_review_agent_id(reviewers[0])
            else run_review(paths.root, reviewers=reviewers)
        )
    except ValueError as exc:
        raise DashboardApiError("review_failed", str(exc)) from exc
    except Exception as exc:
        raise DashboardApiError("review_failed", str(exc)) from exc

    return {
        "ok": True,
        "review_run": review_run,
        "reviews": reviews_payload(paths.root),
    }


def ledger_payload(repo_root: Path) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    ledger = Ledger(paths.ledger)
    exists = paths.ledger.is_file()
    verified = False
    error: str | None = None
    records: list[dict[str, Any]] = []
    head_hash = ""

    if exists:
        try:
            ledger.verify_chain()
            verified = True
        except Exception as exc:
            error = str(exc)
        try:
            records = ledger.read_all()
            head_hash = ledger.head_hash()
        except Exception as exc:
            if error is None:
                error = str(exc)
    return {
        "ok": True,
        "exists": exists,
        "verified": verified,
        "error": error,
        "head_hash": head_hash,
        "count": len(records),
        "records": records[-100:],
    }


def execution_payload(repo_root: Path) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    store = ExecutionStore(paths)
    plan = None
    subtasks: list[dict[str, Any]] = []
    if store.plan_path.is_file():
        try:
            plan_model = store.read_plan()
        except Exception as exc:
            raise DashboardApiError("execution_unreadable", f"Execution plan is unreadable: {exc}") from exc
        plan = plan_model.model_dump(mode="json")
        subtasks = [subtask.model_dump(mode="json") for subtask in plan_model.subtasks]

    subtask_dir = store.execution_dir / "subtasks"
    if subtask_dir.exists():
        subtask_rows, subtask_errors = _read_model_dir(paths, subtask_dir, ExecutionSubtask)
        subtasks = [*subtask_rows, *subtask_errors]

    return {
        "ok": True,
        "plan_present": plan is not None,
        "plan": plan,
        "subtasks": subtasks,
        "bundle_decisions": _read_json_dir(paths, store.execution_dir / "bundle_decisions"),
        "merge_previews": _read_json_dir(paths, store.execution_dir / "merge_previews"),
    }


def candidates_payload(repo_root: Path) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    rows: list[dict[str, Any]] = []
    test_rows, test_errors = _read_model_dir(
        paths, paths.current_task / "execution" / "tests", CandidateTestRun
    )
    decision_rows, decision_errors = _read_model_dir(
        paths, paths.current_task / "execution" / "decisions", ArbiterDecision
    )
    for item in _read_json_dir(paths, paths.current_task / "execution" / "candidates"):
        if "_error" in item:
            rows.append(item)
            continue
        try:
            candidate = CandidatePatch.model_validate(item)
        except Exception as exc:
            rows.append({**item, "_error": f"invalid candidate metadata: {exc}"})
            continue
        row = candidate.model_dump(mode="json")
        row["tests"] = [run for run in test_rows if run.get("candidate_id") == candidate.id]
        row["decisions"] = [
            decision for decision in decision_rows if decision.get("candidate_id") == candidate.id
        ]
        try:
            resolve_evidence_ref(paths, candidate.patch_ref)
            row["patch_exists"] = True
        except Exception:
            row["patch_exists"] = False
        rows.append(row)
    return {
        "ok": True,
        "count": len(rows),
        "candidates": rows,
        "test_errors": test_errors,
        "decision_errors": decision_errors,
    }


def reviews_payload(repo_root: Path) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    reports = [
        report
        for report in _read_json_dir(paths, paths.reviews, "*/findings.json")
        if "_error" not in report
    ]
    packets = [
        _packet_summary(packet)
        for packet in _read_json_dir(paths, paths.reviews, "*/packet.json")
        if "_error" not in packet
    ]
    return {"ok": True, "reports": reports, "packets": packets}


def evidence_payload(repo_root: Path, ref: str) -> dict[str, object]:
    paths = _require_initialized(repo_root)
    try:
        evidence = read_evidence_text(paths, ref)
    except EvidenceResolutionError as exc:
        raise DashboardApiError("invalid_evidence_ref", str(exc)) from exc
    return {"ok": True, **evidence}


def error_payload(exc: Exception) -> dict[str, object]:
    if isinstance(exc, DashboardApiError):
        return exc.payload()
    return {
        "ok": False,
        "error": {"code": "internal_error", "message": str(exc)},
    }


def _require_initialized(repo_root: Path) -> AgosPaths:
    root = Path(repo_root)
    paths = repo_paths(root)
    if not paths.agos_yaml.is_file():
        raise DashboardApiError(
            "not_initialized",
            "AGOS repository is not initialized.",
            hint="Run `agos init` in this repository first.",
        )
    return paths


def _load_current_task(paths: AgosPaths):
    if not paths.task_yaml.is_file():
        raise DashboardApiError(
            "active_task_missing",
            "No active AGOS task is available.",
            hint="Start or restore an AGOS task before opening this dashboard view.",
        )
    return load_task(paths.task_yaml)


def _archive_task_id(paths: AgosPaths) -> str:
    if paths.task_yaml.is_file():
        try:
            return load_task(paths.task_yaml).id
        except Exception:
            pass
    return paths.current_task.name


def _archive_timestamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _fsafe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-") or "task"


def _merge_gate_payload(paths: AgosPaths) -> dict[str, object]:
    try:
        result = verify_merge_gate(paths)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "passed": False, "checks": []}
    payload = result.model_dump(mode="json")
    payload["ok"] = True
    return payload


def _parse_gate_request(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        gates: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise DashboardApiError("invalid_request", "gates must contain only strings")
            stripped = item.strip()
            if stripped:
                gates.append(stripped)
        return gates
    raise DashboardApiError("invalid_request", "gates must be a list of strings or comma-separated string")


def _parse_reviewer_request(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [_normalize_reviewer_id(value)]
    if isinstance(value, list):
        reviewers: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise DashboardApiError("invalid_request", "reviewer must contain only strings")
            stripped = item.strip()
            if stripped:
                reviewers.append(_normalize_reviewer_id(stripped))
        return reviewers
    raise DashboardApiError("invalid_request", "reviewer must be a string or list of strings")


def _normalize_reviewer_id(value: str) -> str:
    stripped = value.strip()
    if _is_local_review_agent_id(stripped):
        return stripped
    return stripped.removeprefix("reviewer:")


def _is_local_review_agent_id(value: str) -> bool:
    return value.startswith("local:reviewer:")


def _resolve_task_agent(repo_root: Path, value: Any) -> ExecutorSelection | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise DashboardApiError("invalid_request", "agent must be a string")
    requested = value.strip()
    if not requested:
        return None
    config = load_config(repo_root)
    selections = _task_agent_selections(config)
    selection = selections.get(requested)
    if selection is None:
        raise DashboardApiError("invalid_agent", f"unknown task agent: {requested}")
    return selection


def _task_agent_rows(config) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    default_id = _executor_agent_id(config.executor.name, config.executor.agent)
    seen_ids.add(default_id)
    rows.append(
        {
            "id": default_id,
            "label": config.executor.agent,
            "adapter": config.executor.name,
            "source": "executor",
            "command": _command_for_adapter(config.executor.name, config.executor.command),
            "available": _command_available(config.executor.name, config.executor.command),
            "selected": True,
        }
    )
    supported = {"multica", "codex_cli", "claude_code"}
    for name, worker in config.workers.items():
        if worker.type not in supported:
            continue
        row_id = f"worker:{name}"
        seen_ids.add(row_id)
        rows.append(
            {
                "id": row_id,
                "label": worker.agent or name,
                "adapter": worker.type,
                "source": "worker",
                "command": _command_for_adapter(worker.type, worker.command),
                "available": _command_available(worker.type, worker.command),
                "selected": False,
            }
        )
    rows.extend(_local_task_agent_rows(config, seen_ids))
    return rows


def _task_agent_selections(config) -> dict[str, ExecutorSelection]:
    selections = {
        _executor_agent_id(config.executor.name, config.executor.agent): ExecutorSelection(
            adapter=config.executor.name,
            agent=config.executor.agent,
            command=config.executor.command,
        )
    }
    supported = {"multica", "codex_cli", "claude_code"}
    for name, worker in config.workers.items():
        if worker.type not in supported:
            continue
        selections[f"worker:{name}"] = ExecutorSelection(
            adapter=worker.type,
            agent=worker.agent or name,
            command=worker.command,
        )
    for row in _local_task_agent_rows(config, set(selections)):
        row_id = str(row["id"])
        adapter = str(row["adapter"])
        command = row.get("command")
        selections[row_id] = ExecutorSelection(
            adapter=adapter,
            agent=str(row["agent"]),
            command=str(command) if command else None,
        )
    return selections


def _local_task_agent_rows(config, seen_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates = [
        ("codex_cli", "codex", "codex"),
        ("claude_code", "claude", "claude"),
        ("multica", config.executor.agent, "multica"),
    ]
    for adapter, agent, command in candidates:
        resolved = shutil.which(command)
        if resolved is None and command != f"{command}.cmd":
            resolved = shutil.which(f"{command}.cmd")
        if resolved is None:
            continue
        row_id = f"local:{adapter}:{agent}"
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        rows.append(
            {
                "id": row_id,
                "label": agent,
                "agent": agent,
                "adapter": adapter,
                "source": "local",
                "command": resolved,
                "available": True,
                "selected": False,
            }
        )
    return rows


def _review_agent_rows(config) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for name, reviewer in config.reviewers.items():
        row_id = f"reviewer:{name}"
        seen_ids.add(row_id)
        command = _command_for_reviewer(reviewer.type, reviewer.command, reviewer.executor)
        rows.append(
            {
                "id": row_id,
                "label": name,
                "type": reviewer.type,
                "role": reviewer.role,
                "required": reviewer.required,
                "command": command,
                "available": _reviewer_available(reviewer.type, command, config.allow_fake_reviewer),
            }
        )
    rows.extend(_local_review_agent_rows(seen_ids))
    return rows


def _local_review_agent_rows(seen_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates = [
        ("codex_cli", "codex review", "codex", "codex_reviewer"),
        ("claude_code", "claude review", "claude", "claude_reviewer"),
    ]
    for executor, label, command, role in candidates:
        resolved = shutil.which(command)
        if resolved is None:
            resolved = shutil.which(f"{command}.cmd")
        if resolved is None:
            continue
        row_id = f"local:reviewer:{executor}"
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        rows.append(
            {
                "id": row_id,
                "label": label,
                "type": executor,
                "role": role,
                "required": True,
                "command": resolved,
                "available": True,
                "source": "local",
            }
        )
    return rows


def _run_local_review_agent(paths: AgosPaths, reviewer_id: str) -> dict[str, object]:
    specs_by_id = {
        row["id"]: row
        for row in _local_review_agent_rows(set())
    }
    row = specs_by_id.get(reviewer_id)
    if row is None:
        raise DashboardApiError("invalid_agent", f"unknown local review agent: {reviewer_id}")
    executor = str(row["type"])
    adapter_id = f"local_{executor}"
    service = ReviewService(paths)
    packet_ref, packet = service.create_packet(diff_kind="governed_repo_diff")
    review_store = ReviewStore(paths)
    adapter = LlmCliReviewerAdapter(
        name=adapter_id,
        executor=executor,
        command=str(row["command"]),
        role=str(row["role"]),
        review_store=review_store,
    )
    run_id = f"review-run-{packet.review_id.removeprefix('review-')}"
    spec = ReviewerSpec(
        id=adapter_id,
        role=str(row["role"]),
        adapter=adapter_id,
        required=True,
    )
    result = ParallelReviewOrchestrator({adapter_id: adapter}).run(
        run_id=run_id,
        packet=packet,
        reviewers=[spec],
    )
    if result.state != "completed":
        failed = ", ".join(result.failed_reviewers)
        raise DashboardApiError("review_failed", f"required reviewers failed: {failed}")
    report_ref, report = service.ingest_findings(packet.review_id, result.findings)
    return {
        "backend": "local_review_agent",
        "kind": "review_run",
        "run_id": result.run_id,
        "review_id": packet.review_id,
        "packet_ref": packet_ref,
        "report_ref": report_ref,
        "reviewers": [spec.id],
        "state": result.state,
        "finding_count": len(report.findings),
    }


def _executor_agent_id(adapter: str, agent: str) -> str:
    return f"executor:{adapter}:{agent}"


def _command_for_adapter(adapter: str, command: str | None) -> str | None:
    if command:
        return command
    if adapter == "multica":
        return "multica"
    if adapter == "codex_cli":
        return "codex"
    if adapter == "claude_code":
        return "claude"
    return None


def _command_for_reviewer(
    reviewer_type: str,
    command: str | None,
    executor: str | None,
) -> str | None:
    if command:
        return command
    if reviewer_type == "codex_cli" or executor == "codex_cli":
        return "codex"
    if reviewer_type == "claude_code" or executor == "claude_code":
        return "claude"
    return None


def _command_available(adapter: str, command: str | None) -> bool:
    resolved = _command_for_adapter(adapter, command)
    return True if resolved is None else shutil.which(resolved) is not None


def _reviewer_available(reviewer_type: str, command: str | None, allow_fake: bool) -> bool:
    if reviewer_type == "manual":
        return True
    if reviewer_type == "fake":
        return allow_fake
    return True if command is None else shutil.which(command) is not None


def _dump_model(value: BaseModel | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return value.model_dump(mode="json")


def _redact(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _SECRET_KEY_RE.search(key):
        return _REDACTED
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _read_json_dir(paths: AgosPaths, directory: Path, pattern: str = "*.json") -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    if _safe_task_path(paths, directory) is None:
        return [
            {
                "path": _task_display_path(paths, directory),
                "_error": "path escapes current task",
            }
        ]
    rows: list[dict[str, Any]] = []
    for path in sorted(directory.glob(pattern)):
        if not path.is_file():
            continue
        safe_path = _safe_task_path(paths, path)
        display_path = _task_display_path(paths, path)
        if safe_path is None:
            rows.append({"path": display_path, "_error": "path escapes current task"})
            continue
        try:
            rows.append(json.loads(safe_path.read_text(encoding="utf-8")))
        except Exception as exc:
            rows.append({"path": display_path, "_error": str(exc)})
    return rows


def _read_model_dir(
    paths: AgosPaths,
    directory: Path,
    model_type: type[BaseModel],
    pattern: str = "*.json",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not directory.exists():
        return [], []
    if _safe_task_path(paths, directory) is None:
        return [], [
            {
                "path": _task_display_path(paths, directory),
                "_error": "path escapes current task",
            }
        ]
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for path in sorted(directory.glob(pattern)):
        if not path.is_file():
            continue
        safe_path = _safe_task_path(paths, path)
        display_path = _task_display_path(paths, path)
        if safe_path is None:
            errors.append({"path": display_path, "_error": "path escapes current task"})
            continue
        try:
            payload = json.loads(safe_path.read_text(encoding="utf-8"))
            rows.append(_model_row(model_type.model_validate(payload)))
        except Exception as exc:
            errors.append({"path": display_path, "_error": str(exc)})
    return rows, errors


def _safe_task_path(paths: AgosPaths, path: Path) -> Path | None:
    try:
        resolved = path.resolve()
        resolved.relative_to(paths.current_task.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _task_display_path(paths: AgosPaths, path: Path) -> str:
    try:
        return path.resolve().relative_to(paths.current_task.resolve()).as_posix()
    except (OSError, ValueError):
        try:
            return path.relative_to(paths.current_task).as_posix()
        except ValueError:
            return path.name


def _model_row(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _packet_summary(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_id": packet.get("review_id"),
        "task_id": packet.get("task_id"),
        "task_title": packet.get("task_title"),
        "diff_kind": packet.get("diff_kind"),
        "diff_evidence_ref": packet.get("diff_evidence_ref"),
        "subject": packet.get("subject", {}),
        "context_refs": packet.get("context_refs", []),
        "gate_refs": packet.get("gate_refs", {}),
    }
