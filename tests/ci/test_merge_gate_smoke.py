"""CI smoke test for the strict AGOS merge-gate command."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from agos.core.adapter import ExecutorRun
from agos.core.config import AGOSConfig, WorkflowConfig
from agos.core.execution import ArbiterDecision, CandidatePatch, CandidateTestRun, ReviewBinding
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.review import ReviewReport
from agos.core.review_store import ReviewStore
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.core.trust_anchor import GitRefTrustAnchorStore, publish_current_anchor


def test_strict_merge_gate_smoke() -> None:
    with tempfile.TemporaryDirectory(prefix="agos-merge-gate-") as tmp:
        repo = Path(tmp)
        _init_repo(repo)
        _write_agos_state(repo)
        _write_anchor(repo)
        base = _git(repo, "rev-parse", "HEAD")
        _write_candidate_diff(repo, base)
        _write_anchor(repo)
        head = _git(repo, "rev-parse", "HEAD")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agos.cli.main",
                "merge-gate",
                "--json",
                "--require-anchor",
                "--anchor-backend",
                "git-ref",
                "--base",
                base,
                "--head",
                head,
            ],
            cwd=repo,
            check=True,
        )


def test_strict_merge_gate_blocks_when_anchor_stale() -> None:
    """A PR head whose anchor was not republished after the checkpoint blocks."""
    with tempfile.TemporaryDirectory(prefix="agos-merge-gate-") as tmp:
        repo = Path(tmp)
        _init_repo(repo)
        _write_agos_state(repo)
        _write_anchor(repo)
        base = _git(repo, "rev-parse", "HEAD")
        _write_candidate_diff(repo, base)
        head = _git(repo, "rev-parse", "HEAD")
        # No second _write_anchor: the published anchor still points at the base
        # ledger/repo head, so verify_merge_gate must fail closed on the anchor.
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "agos.cli.main",
                "merge-gate",
                "--json",
                "--require-anchor",
                "--anchor-backend",
                "git-ref",
                "--base",
                base,
                "--head",
                head,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        result = json.loads(proc.stdout)
        assert result["passed"] is False
        trust = next(check for check in result["checks"] if check["name"] == "trust_anchor")
        assert trust["state"] == "block"


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "ci"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repo, check=True)
    (repo / "README.md").write_text("# smoke\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _write_agos_state(repo: Path) -> None:
    paths = repo_paths(repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    AGOSConfig(
        executor={"name": "multica", "agent": "Lambda"},
        default_workflow="feature",
        workflows={"feature": WorkflowConfig(gates=[])},
    ).save(paths.agos_yaml)
    task = Task(
        id="agos-smoke",
        title="Merge gate smoke",
        workflow="feature",
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    ledger.append({"type": "gates_locked", "task_id": task.id, "gates": []})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-smoke"),
            ledger_head_hash=started["hash"],
        ),
        paths,
    )


def _write_candidate_diff(repo: Path, base: str) -> None:
    paths = repo_paths(repo)
    (repo / "README.md").write_text("# smoke\nchanged\n", encoding="utf-8")
    patch_bytes = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    store = ExecutionStore(paths)
    patch_ref, patch_sha = store.write_candidate_patch("candidate-01", patch_bytes)
    ledger = Ledger(paths.ledger)
    ledger.append(
        {
            "type": "candidate_patch_created",
            "task_id": "agos-smoke",
            "subtask_id": "subtask-01",
            "candidate_id": "candidate-01",
            "patch_ref": patch_ref,
            "patch_sha256": patch_sha,
        }
    )
    test_ref = store.write_test_run(
        CandidateTestRun(
            id="test-patch",
            candidate_id="candidate-01",
            gate_id="patch_applies",
            state="passed",
            evidence_ref="execution/tests/patch.json",
            workspace_ref="execution/workspaces/subtask-01.json",
        )
    )
    review_id = "review-01"
    packet_ref = f"reviews/{review_id}/packet.json"
    report_ref = ReviewStore(paths).write_report(
        ReviewReport(
            review_id=review_id,
            task_id="agos-smoke",
            packet_ref=packet_ref,
            findings=[],
        )
    )
    completed = ledger.append(
        {
            "type": "candidate_review_completed",
            "task_id": "agos-smoke",
            "candidate_id": "candidate-01",
            "review_id": review_id,
            "report_ref": report_ref,
            "open_blocking_count": 0,
        }
    )
    decision_ref = store.write_decision(
        ArbiterDecision(
            id="decision-candidate-01",
            candidate_id="candidate-01",
            decision="accepted",
            reason="Strict merge-gate smoke accepted complete candidate evidence.",
            evidence_refs=[patch_ref, test_ref, report_ref],
            decided_by="ci",
        )
    )
    ledger.append(
        {
            "type": "candidate_decision_recorded",
            "task_id": "agos-smoke",
            "candidate_id": "candidate-01",
            "decision": "accepted",
            "decision_ref": decision_ref,
            "evidence_refs": [patch_ref, test_ref, report_ref],
        }
    )
    candidate = CandidatePatch(
        id="candidate-01",
        task_id="agos-smoke",
        subtask_id="subtask-01",
        source_agent="ci",
        workspace_ref="execution/workspaces/subtask-01.json",
        patch_ref=patch_ref,
        patch_sha256=patch_sha,
        base_commit=base,
        summary="CI merge-gate smoke candidate",
        status="applied",
        test_refs=[test_ref],
        decision_ref=decision_ref,
        review_refs=[
            ReviewBinding(
                review_id=review_id,
                packet_ref=packet_ref,
                report_ref=report_ref,
                patch_sha256=patch_sha,
                base_commit=base,
                test_refs=[test_ref],
                state="completed",
                ledger_head_at_completion=completed["hash"],
                open_blocking_count=0,
            )
        ],
    )
    store.write_candidate(candidate)
    ledger.append(
        {
            "type": "candidate_applied",
            "task_id": "agos-smoke",
            "candidate_id": candidate.id,
            "patch_ref": candidate.patch_ref,
            "decision_ref": candidate.decision_ref,
        }
    )
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "apply candidate"], cwd=repo, check=True)


def _write_anchor(repo: Path) -> None:
    paths = repo_paths(repo)
    publish_current_anchor(paths, GitRefTrustAnchorStore(repo), issuer="ci")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()
