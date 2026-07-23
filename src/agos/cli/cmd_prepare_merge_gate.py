"""`agos prepare-merge-gate` command."""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import typer

from agos.core.command import run_command
from agos.core.config import AGOSConfig, load_config, resolve_gates
from agos.core.execution import (
    CandidatePatch,
    CandidateProvenance,
    CandidateTestRun,
    ExecutionSubtask,
    WorkspaceBinding,
)
from agos.core.execution_store import ExecutionStore
from agos.core.execution_workspace import candidate_patch_paths
from agos.core.gate import GateContext, build_gate, gate_command_text, gates_locked_payload
from agos.core.repo import find_initialized_repo_root, git_head, repo_paths
from agos.core.task import ExecutorBinding, Task, new_task_id, save_task
from agos.core.task_state import TaskEvent, TaskRevision, TaskState
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
    trusted_config: Path | None = typer.Option(
        None,
        "--trusted-config",
        help="Protected-base agos.yaml used for workflow and gate resolution.",
    ),
) -> None:
    """Prepare a fresh active AGOS task plus candidate evidence for CI merge-gate."""

    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        config = (
            AGOSConfig.load(trusted_config)
            if trusted_config is not None
            else load_config(repo_root)
        )
        if trusted_config is not None and workflow not in {None, config.default_workflow}:
            raise ValueError(
                "--workflow must match the trusted config default_workflow: "
                f"{workflow!r} != {config.default_workflow!r}"
            )
        workflow_name = (
            config.default_workflow
            if trusted_config is not None
            else (workflow or config.default_workflow)
        )
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

    task_state = TaskState(paths)
    initialized = task_state.record(
        TaskEvent(
            "task_started",
            {
                "task_id": task.id,
                "title": task.title,
                "workflow": task.workflow,
            },
        ),
        TaskEvent(
            "gates_locked",
            {
                "task_id": task.id,
                "gates": gates_locked_payload(list(resolved_gates)),
            },
        ),
        expected=TaskRevision.empty(),
    )
    task_state.record(
        TaskEvent(
            "executor_dispatched",
            {
                "task_id": task.id,
                "adapter": "ci_prepare",
                "run_id": "prepare-merge-gate",
            },
        ),
        expected=initialized.snapshot.revision,
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
    created = _append_event(
        paths,
        {
            "type": "candidate_patch_created",
            "task_id": task.id,
            "subtask_id": subtask.id,
            "candidate_id": candidate_id,
            "patch_ref": patch_ref,
            "patch_sha256": patch_sha,
            "provenance_source": "ci_reconstructed",
            "source_agent": "ci_prepare",
            "workspace_ref": workspace.ref,
            "base_commit": base,
            "attestation_ref": None,
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
        provenance=CandidateProvenance(
            source="ci_reconstructed",
            ledger_head_hash=str(created["hash"]),
        ),
    )
    store.write_candidate(candidate)

    diff_text = patch_bytes.decode("utf-8", errors="replace")
    runs = [
        _record_patch_applies(
            paths,
            task_id=task.id,
            candidate_id=candidate_id,
            workspace_ref=workspace.ref,
            base=base,
            patch_bytes=patch_bytes,
        )
    ]
    for gate_spec in resolved_gates:
        runs.append(
            _run_gate(
                paths,
                task_id=task.id,
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


def _record_patch_applies(
    paths,
    *,
    task_id: str,
    candidate_id: str,
    workspace_ref: str,
    base: str,
    patch_bytes: bytes,
) -> CandidateTestRun:
    _append_event(
        paths,
        {
            "type": "candidate_test_started",
            "task_id": task_id,
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
            "task_id": task_id,
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
    task_id: str,
    candidate_id: str,
    workspace_ref: str,
    diff_text: str,
    gate_spec,
) -> CandidateTestRun:
    _append_event(
        paths,
        {
            "type": "candidate_test_started",
            "task_id": task_id,
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
            "task_id": task_id,
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
    with tempfile.TemporaryDirectory(
        prefix="agos-merge-gate-verify-", dir=str(repo_root.parent)
    ) as tmp:
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
    event_name = record.get("type")
    if not isinstance(event_name, str) or not event_name:
        raise ValueError("task event type must be non-empty text")
    facts = {key: value for key, value in record.items() if key != "type"}
    commit = TaskState(paths).record(TaskEvent(event_name, facts))
    return commit.records[-1]


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
