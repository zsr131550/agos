"""`agos checkpoint` command."""
from __future__ import annotations

import time

import typer

from agos.adapters.multica import MulticaAdapter
from agos.core.adapter import RunStatus
from agos.core.evidence import EvidenceStore
from agos.core.ledger import Ledger
from agos.core.repo import find_initialized_repo_root, git_head, git_status_porcelain, repo_paths
from agos.core.status import ExecutorRunInfo, TaskStatus, load_status, save_status


def _anchor_ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _as_run_info(run_info: ExecutorRunInfo | None) -> ExecutorRunInfo:
    if run_info is None:
        raise typer.BadParameter("No active executor run in .agos/tasks/current/status.json")
    return run_info


def _record_terminal_status(
    *,
    run_info: ExecutorRunInfo,
    run_status: RunStatus,
    status: TaskStatus,
    paths,
) -> bool:
    if run_status.state == "running":
        return False

    phase = "done" if run_status.state == "completed" else "blocked"
    if status.phase == phase:
        return True

    ledger = Ledger(paths.ledger)
    record = ledger.append(
        {
            "type": "executor_completed" if run_status.state == "completed" else "executor_blocked",
            "run_id": run_info.run_id,
            "issue_id": run_info.issue_id,
            "state": run_status.state,
            "detail": run_status.detail,
        }
    )
    status.phase = phase
    status.ledger_head_hash = record["hash"]
    save_status(status, paths)
    return True


def _checkpoint_once(*, adapter: MulticaAdapter, status: TaskStatus, paths) -> tuple[bool, int | None]:
    run_info = _as_run_info(status.executor_run)
    store = EvidenceStore(paths.evidence)
    events = list(adapter.stream_events(run_info.run_id, since=status.last_event_seq))
    if not events:
        run_status = adapter.status(run_info.run_id, issue_id=run_info.issue_id)
        return _record_terminal_status(
            run_info=run_info,
            run_status=run_status,
            status=status,
            paths=paths,
        ), status.last_event_seq

    for event in events:
        store.append_message(run_info.run_id, event.raw or {
            "seq": event.seq,
            "ts": event.ts,
            "kind": event.kind,
            "content": event.content,
        })

    anchor_name = f"{_anchor_ts()}-{events[-1].seq}"
    repo_head = git_head(paths.root)
    anchor_path = store.write_repo_anchor(
        anchor_name,
        repo_head,
        git_status_porcelain(paths.root),
    )
    ledger = Ledger(paths.ledger)
    record = ledger.append(
        {
            "type": "checkpoint",
            "run_id": run_info.run_id,
            "evidence_refs": [
                f"messages/{run_info.run_id}.jsonl",
                f"repo_anchor/{anchor_path.name}",
            ],
            "repo_head": repo_head,
            "last_seq": events[-1].seq,
        }
    )
    status.last_event_seq = events[-1].seq
    status.ledger_head_hash = record["hash"]
    save_status(status, paths)
    if any(event.kind == "run_complete" for event in events):
        completed = _record_terminal_status(
            run_info=run_info,
            run_status=RunStatus(state="completed", detail="run_complete"),
            status=status,
            paths=paths,
        )
        return completed, events[-1].seq
    return False, events[-1].seq


def checkpoint_command(
    follow: bool = typer.Option(False, "--follow", help="Poll until the executor run completes."),
    once: bool = typer.Option(False, "--once", help="Poll a single time and exit."),
) -> None:
    """Capture executor messages plus a governed-repo anchor."""

    if follow and once:
        raise typer.BadParameter("Choose either --follow or --once, not both.")

    try:
        repo_root = find_initialized_repo_root()
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    paths = repo_paths(repo_root)
    status = load_status(paths)
    if status is None:
        typer.echo("No active AGOS task found", err=True)
        raise typer.Exit(code=1)

    adapter = MulticaAdapter()
    poll_once = once or not follow

    while True:
        completed, _last_seq = _checkpoint_once(adapter=adapter, status=status, paths=paths)
        if poll_once or completed:
            return
        time.sleep(3)
