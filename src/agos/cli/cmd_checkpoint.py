"""`agos checkpoint` command."""
from __future__ import annotations

import time

import typer

from agos.adapters.multica import MulticaAdapter
from agos.core.evidence import EvidenceStore
from agos.core.ledger import append_task_record
from agos.core.repo import find_initialized_repo_root, git_head, git_status_porcelain, repo_paths
from agos.core.status import ExecutorRunInfo, TaskStatus, load_status, save_status


def _anchor_ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _as_run_info(run_info: ExecutorRunInfo | None) -> ExecutorRunInfo:
    if run_info is None:
        raise typer.BadParameter("No active executor run in .agos/tasks/current/status.json")
    return run_info


def _checkpoint_once(*, adapter: MulticaAdapter, status: TaskStatus, paths) -> tuple[bool, int | None]:
    run_info = _as_run_info(status.executor_run)
    store = EvidenceStore(paths.evidence)
    events = list(adapter.stream_events(run_info.run_id, since=status.last_event_seq))
    if not events:
        run_status = adapter.status(run_info.run_id, issue_id=run_info.issue_id)
        return run_status.state == "completed", status.last_event_seq

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
    record = append_task_record(
        paths.ledger,
        "checkpoint",
        run_id=run_info.run_id,
        evidence_refs=[
            f"messages/{run_info.run_id}.jsonl",
            f"repo_anchor/{anchor_path.name}",
        ],
        repo_head=repo_head,
        last_seq=events[-1].seq,
    )
    status.last_event_seq = events[-1].seq
    status.ledger_head_hash = record["hash"]
    save_status(status, paths)
    return any(event.kind == "run_complete" for event in events), events[-1].seq


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
