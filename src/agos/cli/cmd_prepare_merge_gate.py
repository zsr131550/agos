"""`agos prepare-merge-gate` command."""
from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import typer

from agos.core.adapter import ExecutorRun
from agos.core.arbiters import CandidateDecisionArbiter, CandidateDecisionSnapshot
from agos.core.command import run_command
from agos.core.config import load_config, resolve_gates
from agos.core.execution import (
    CandidatePatch,
    CandidateTestRun,
    ExecutionSubtask,
    ReviewBinding,
    WorkspaceBinding,
)
from agos.core.execution_store import ExecutionStore
from agos.core.execution_workspace import candidate_patch_paths
from agos.core.gate import GateContext, build_gate, gate_command_text, gates_locked_payload
from agos.core.ledger import Ledger
from agos.core.repo import find_initialized_repo_root, git_head, repo_paths
from agos.core.status import TaskStatus, load_status, save_status
from agos.core.review import ReviewPacket, ReviewReport
from agos.core.review_store import ReviewStore
from agos.core.task import ExecutorBinding, Task, new_task_id, save_task
from agos.core.trust_anchor import FileTrustAnchorStore, publish_current_anchor


def prepare_merge_gate_command(
    base: str = typer.Option(..., "--base", help="Base git ref for the submitted PR diff."),
    head: str = typer.Option(..., "--head", help="Head git ref for the submitted PR diff."),
    anchor_path: Path = typer.Option(
        ...,
        "--anchor-path",
        help="File trust-anchor path written for the merge-gate verification job.",
    ),
    issuer: str = typer.Option(..., "--issuer", help="Trust-anchor issuer name."),
    title: str = typer.Option(
        "Prepared PR merge-gate task",
        "--title",
        help="Human-readable AGOS task title.",
    ),
    workflow: str | None = typer.Option(None, "--workflow", help="Workflow name from agos.yaml."),
) -> None:
    """Prepare a fresh active AGOS task plus candidate evidence for CI merge-gate."""

    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        config = load_config(repo_root)
        workflow_name = workflow or config.default_workflow
        resolved_gates = resolve_gates(config, workflow_name)
        _require_head_checkout(repo_root, head)

        # CI must never reuse stale current-task state from the checked-out repo.
        shutil.rmtree(paths.current_task, ignore_errors=True)
        paths.current_task.mkdir(parents=True, exist_ok=True)

        task = _write_task_state(
            paths,
            title=title,
            workflow_name=workflow_name,
            resolved_gates=resolved_gates,
        )

        patch_bytes = _submitted_diff(repo_root, base, head)
        if patch_bytes.strip():
            _materialize_candidate_evidence(
                paths,
                task=task,
                base=base,
                patch_bytes=patch_bytes,
                resolved_gates=resolved_gates,
            )

        anchor_store = FileTrustAnchorStore(_anchor_file_path(repo_root, anchor_path))
        payload = publish_current_anchor(paths, anchor_store, issuer=issuer)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"prepared merge-gate task {payload.task_id} "
        f"seq={payload.ledger_seq} head={payload.ledger_head_hash}"
    )


def _write_task_state(
    paths,
    *,
    title: str,
    workflow_name: str,
    resolved_gates,
) -> Task:
    task = Task(
        id=f"agos-{new_task_id()}",
        title=title,
        workflow=workflow_name,
        gates=[gate.id for gate in resolved_gates],
        executor=ExecutorBinding(adapter="ci_prepare", agent="github_actions"),
    )
    save_task(task, paths.task_yaml)

    ledger = Ledger(paths.ledger)
    started = ledger.append(
        {
            "type": "task_started",
            "task_id": task.id,
            "title": task.title,
            "workflow": task.workflow,
        }
    )
    locked = ledger.append(
        {
            "type": "gates_locked",
            "task_id": task.id,
            "gates": gates_locked_payload(list(resolved_gates)),
        }
    )
    del started
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="ci_prepare", run_id="prepare-merge-gate"),
            ledger_head_hash=locked["hash"],
        ),
        paths,
    )
    return task


def _materialize_candidate_evidence(
    paths,
    *,
    task: Task,
    base: str,
    patch_bytes: bytes,
    resolved_gates,
) -> None:
    store = ExecutionStore(paths)
    touched_paths = sorted(candidate_patch_paths(patch_bytes))
    if not touched_paths:
        raise ValueError("submitted diff did not contain any patch paths")

    subtask_id = "pr-diff"
    candidate_id = "candidate-pr-diff"
    workspace = WorkspaceBinding(
        subtask_id=subtask_id,
        path=str(paths.root),
        base_ref=base,
        base_commit=base,
    )
    subtask = ExecutionSubtask(
        id=subtask_id,
        title="Validate submitted PR diff",
        intent="Bind the submitted diff to AGOS candidate evidence for CI merge-gate.",
        depends_on=[],
        write_scope=touched_paths,
        worker={"adapter": "ci_prepare", "role": "worker_agent"},
        status="completed",
        workspace_ref=workspace.ref,
    )
    store.write_workspace(workspace)
    store.write_subtask(subtask)

    patch_ref, patch_sha = store.write_candidate_patch(candidate_id, patch_bytes)
    _append_event(
        paths,
        {
            "type": "candidate_patch_created",
            "task_id": task.id,
            "subtask_id": subtask.id,
            "candidate_id": candidate_id,
            "patch_ref": patch_ref,
            "patch_sha256": patch_sha,
        },
    )

    candidate = CandidatePatch(
        id=candidate_id,
        task_id=task.id,
        subtask_id=subtask.id,
        source_agent="ci_prepare",
        workspace_ref=workspace.ref,
        patch_ref=patch_ref,
        patch_sha256=patch_sha,
        base_commit=base,
        summary="Submitted pull-request diff",
        status="testing",
    )
    store.write_candidate(candidate)

    diff_text = patch_bytes.decode("utf-8", errors="replace")
    runs = [
        _record_patch_applies(paths, candidate_id=candidate_id, workspace_ref=workspace.ref, base=base, patch_bytes=patch_bytes)
    ]
    for gate_spec in resolved_gates:
        runs.append(
            _run_gate(
                paths,
                candidate_id=candidate_id,
                workspace_ref=workspace.ref,
                diff_text=diff_text,
                gate_spec=gate_spec,
            )
        )

    test_refs = [store.write_test_run(run) for run in runs]
    failed = [run.gate_id for run in runs if run.state != "passed"]
    final_status = "tested" if not failed else "proposed"
    candidate = candidate.model_copy(update={"status": final_status, "test_refs": test_refs})
    store.write_candidate(candidate)

    if failed:
        raise ValueError("candidate gates failed: " + ", ".join(failed))

    _materialize_clean_ci_review(paths, candidate=candidate, task=task)
    candidate = store.read_candidate(candidate.id)
    _materialize_ci_decision(paths, candidate=candidate, task=task)
    candidate = store.read_candidate(candidate.id)
    candidate = candidate.model_copy(update={"status": "applied"})
    store.write_candidate(candidate)

    _append_event(
        paths,
        {
            "type": "candidate_applied",
            "task_id": task.id,
            "candidate_id": candidate.id,
            "patch_ref": candidate.patch_ref,
            "decision_ref": candidate.decision_ref,
        },
    )


def _materialize_clean_ci_review(paths, *, candidate: CandidatePatch, task: Task) -> None:
    """Create deterministic candidate-bound CI review evidence for PR diff candidates."""

    review_store = ReviewStore(paths)
    review_id = "review-ci-pr-diff"
    packet = ReviewPacket(
        review_id=review_id,
        task_id=task.id,
        task_title=task.title,
        task_intent=task.intent,
        acceptance=list(task.acceptance),
        subject={
            "type": "candidate",
            "candidate_id": candidate.id,
            "subtask_id": candidate.subtask_id,
            "task_id": task.id,
        },
        context_refs=[
            f"execution/candidates/{candidate.id}.json",
            f"execution/subtasks/{candidate.subtask_id}.json",
            candidate.workspace_ref,
            *candidate.test_refs,
        ],
        diff_kind="candidate_patch",
        diff_evidence_ref=candidate.patch_ref,
        ledger_head_hash=_ledger_head(paths),
    )
    packet_ref = review_store.write_packet(packet)
    raw_ref = review_store.write_raw_output(
        review_id,
        "ci_prepare",
        {
            "reviewer_id": "ci_prepare",
            "state": "completed",
            "findings": [],
            "policy": "CI prepared PR diff passed locked gates; no missing-review override used.",
        },
    )
    report = ReviewReport(
        review_id=review_id,
        task_id=task.id,
        packet_ref=packet_ref,
        findings=[],
    )
    report_ref = review_store.write_report(report)
    review_store.write_markdown_report(report)
    completed = _append_event(
        paths,
        {
            "type": "candidate_review_completed",
            "task_id": task.id,
            "candidate_id": candidate.id,
            "review_id": review_id,
            "report_ref": report_ref,
            "open_blocking_count": 0,
        },
    )
    binding = ReviewBinding(
        review_id=review_id,
        packet_ref=packet_ref,
        report_ref=report_ref,
        raw_refs=[raw_ref],
        patch_sha256=candidate.patch_sha256,
        base_commit=candidate.base_commit,
        write_scope=sorted(candidate_patch_paths((paths.current_task / candidate.patch_ref).read_bytes())),
        test_refs=list(candidate.test_refs),
        ledger_head_at_start=completed["prev_hash"],
        ledger_head_at_completion=completed["hash"],
        open_blocking_count=0,
        state="completed",
        completed_at=_utc_now(),
    )
    store = ExecutionStore(paths)
    latest = store.read_candidate(candidate.id)
    store.write_candidate(
        latest.model_copy(
            update={
                "status": "reviewed",
                "review_refs": [*latest.review_refs, binding],
            }
        )
    )


def _materialize_ci_decision(paths, *, candidate: CandidatePatch, task: Task) -> None:
    completed_reviews = [binding for binding in candidate.review_refs if binding.state == "completed"]
    if not completed_reviews or completed_reviews[-1].report_ref is None:
        raise ValueError("CI candidate decision requires a completed review report")
    review = completed_reviews[-1]
    evidence_refs = [candidate.patch_ref, *candidate.test_refs, review.report_ref]
    result = CandidateDecisionArbiter().decide(
        CandidateDecisionSnapshot(
            candidate_id=candidate.id,
            decision="accepted",
            reason="CI preparation accepted the candidate after passed gates and bound review.",
            decided_by="ci_prepare",
            evidence_refs=tuple(evidence_refs),
            tests_passed=True,
            review_binding_current=True,
            review_open_blocking_count=0,
            patch_ref=candidate.patch_ref,
            test_refs=tuple(candidate.test_refs),
            review_report_ref=review.report_ref,
        )
    )
    store = ExecutionStore(paths)
    decision_ref = store.write_decision(result.decision)
    store.write_candidate(
        candidate.model_copy(
            update={"status": result.candidate_status, "decision_ref": decision_ref}
        )
    )
    _append_event(
        paths,
        {
            "type": "candidate_decision_recorded",
            "task_id": task.id,
            "candidate_id": candidate.id,
            "decision": result.decision.decision,
            "decision_ref": decision_ref,
            "evidence_refs": evidence_refs,
        },
    )


def _ledger_head(paths) -> str:
    status = load_status(paths)
    return status.ledger_head_hash if status is not None else ""


def _record_patch_applies(
    paths,
    *,
    candidate_id: str,
    workspace_ref: str,
    base: str,
    patch_bytes: bytes,
) -> CandidateTestRun:
    _append_event(
        paths,
        {
            "type": "candidate_test_started",
            "task_id": load_status(paths).task_id if load_status(paths) is not None else "unknown-task",
            "candidate_id": candidate_id,
            "gate_id": "patch_applies",
        },
    )
    state, evidence_ref, command = _check_patch_applies_against_base(
        paths.root,
        base=base,
        patch_bytes=patch_bytes,
        evidence_dir=paths.evidence,
        candidate_id=candidate_id,
    )
    run = CandidateTestRun(
        id="candidate-pr-diff-patch-applies",
        candidate_id=candidate_id,
        gate_id="patch_applies",
        command=command,
        state=state,
        evidence_ref=evidence_ref,
        workspace_ref=workspace_ref,
        completed_at=_utc_now(),
    )
    _append_event(
        paths,
        {
            "type": "candidate_test_completed",
            "task_id": load_status(paths).task_id if load_status(paths) is not None else "unknown-task",
            "candidate_id": candidate_id,
            "gate_id": "patch_applies",
            "state": run.state,
            "evidence_ref": run.evidence_ref,
        },
    )
    return run


def _run_gate(
    paths,
    *,
    candidate_id: str,
    workspace_ref: str,
    diff_text: str,
    gate_spec,
) -> CandidateTestRun:
    _append_event(
        paths,
        {
            "type": "candidate_test_started",
            "task_id": load_status(paths).task_id if load_status(paths) is not None else "unknown-task",
            "candidate_id": candidate_id,
            "gate_id": gate_spec.id,
        },
    )
    result = build_gate(gate_spec).evaluate(
        GateContext(
            repo_root=paths.root,
            stage="candidate",
            diff=diff_text,
            evidence_dir=paths.evidence,
        )
    )
    run = CandidateTestRun(
        id=f"{candidate_id}-{gate_spec.id}",
        candidate_id=candidate_id,
        gate_id=gate_spec.id,
        command=gate_command_text(gate_spec),
        state="passed" if result.state == "pass" else "failed",
        evidence_ref=_evidence_ref(paths.evidence, result.evidence_path),
        workspace_ref=workspace_ref,
        completed_at=_utc_now(),
    )
    _append_event(
        paths,
        {
            "type": "candidate_test_completed",
            "task_id": load_status(paths).task_id if load_status(paths) is not None else "unknown-task",
            "candidate_id": candidate_id,
            "gate_id": gate_spec.id,
            "state": run.state,
            "evidence_ref": run.evidence_ref,
        },
    )
    return run


def _check_patch_applies_against_base(
    repo_root: Path,
    *,
    base: str,
    patch_bytes: bytes,
    evidence_dir: Path,
    candidate_id: str,
) -> tuple[str, str, str]:
    command = "git apply --check --binary -"
    log_dir = evidence_dir / "gates"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_name = f"{candidate_id}-patch_applies-{_fsafe_ts()}-{uuid4().hex[:8]}.log"
    log_path = log_dir / log_name
    ref = f"gates/{log_name}"
    with tempfile.TemporaryDirectory(prefix="agos-merge-gate-verify-", dir=str(repo_root.parent)) as tmp:
        workspace = Path(tmp)
        run_command(
            ["git", "worktree", "add", "--detach", str(workspace), base],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            proc = run_command(
                ["git", "apply", "--check", "--binary", "-"],
                cwd=workspace,
                input=patch_bytes,
                capture_output=True,
            )
            stdout = _decode_output(proc.stdout)
            stderr = _decode_output(proc.stderr)
            log_path.write_text(
                (
                    f"command: {command}\n"
                    f"workspace: {workspace}\n"
                    f"base: {base}\n"
                    f"exit_code: {proc.returncode}\n"
                    f"--- stdout ---\n{stdout}\n"
                    f"--- stderr ---\n{stderr}\n"
                ),
                encoding="utf-8",
            )
            state = "passed" if proc.returncode == 0 else "failed"
            return state, ref, command
        finally:
            run_command(
                ["git", "worktree", "remove", "--force", str(workspace)],
                cwd=repo_root,
                capture_output=True,
                text=True,
            )


def _submitted_diff(repo_root: Path, base: str, head: str) -> bytes:
    proc = run_command(
        ["git", "diff", "--binary", f"{base}..{head}"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    return proc.stdout if isinstance(proc.stdout, bytes) else proc.stdout.encode()


def _append_event(paths, record: dict[str, object]) -> dict[str, object]:
    status = load_status(paths)
    if status is None:
        raise ValueError("No active AGOS task found")
    appended = Ledger(paths.ledger).append(record)
    status.ledger_head_hash = appended["hash"]
    save_status(status, paths)
    return appended


def _require_head_checkout(repo_root: Path, expected_head: str) -> None:
    current_head = git_head(repo_root)
    if current_head != expected_head:
        raise ValueError(
            "prepare-merge-gate must run on the PR head checkout; "
            f"expected HEAD {expected_head}, got {current_head}"
        )


def _anchor_file_path(repo_root: Path, anchor_path: Path) -> Path:
    return anchor_path if anchor_path.is_absolute() else repo_root / anchor_path


def _evidence_ref(evidence_dir: Path, evidence_path: str | None) -> str:
    if evidence_path is None:
        return ""
    path = Path(evidence_path)
    try:
        return path.resolve().relative_to(evidence_dir.resolve()).as_posix()
    except ValueError:
        return evidence_path


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fsafe_ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
