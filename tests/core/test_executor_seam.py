"""Seam tests proving the core depends only on the executor interface."""
from __future__ import annotations

import importlib
import json
import pkgutil
import re
from collections.abc import Iterator
from pathlib import Path

from agos.core.adapter import Event, ExecutorAdapter, ExecutorRun, RunStatus
from agos.core.evidence import EvidenceStore
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import derive_status, load_status, save_status
from agos.core.task import ExecutorBinding, Task, new_task_id

ADAPTER_IMPORT_RE = re.compile(r"^\s*(?:from\s+agos\.adapters\b|import\s+agos\.adapters\b)", re.MULTILINE)
MULTICA_IMPORT_RE = re.compile(r"^\s*(?:from\s+multica\b|import\s+multica\b)", re.MULTILINE)


class FakeAdapter:
    """A non-Multica adapter implementing the executor protocol with canned events."""

    name = "fake"

    def __init__(self, events: list[Event]) -> None:
        self._events = events

    def start(self, task: Task) -> ExecutorRun:
        return ExecutorRun(adapter=self.name, run_id="fake-run-1", issue_id=f"ISSUE-{task.id}")

    def stream_events(self, run_id: str, since: int | None = None) -> Iterator[Event]:
        del run_id
        for event in self._events:
            if since is None or event.seq > since:
                yield event

    def status(self, run_id: str, issue_id: str | None = None) -> RunStatus:
        del run_id, issue_id
        return RunStatus(state="completed")


def _core_modules() -> Iterator[object]:
    import agos.core as core_pkg

    yield core_pkg
    core_dir = Path(core_pkg.__file__).parent
    for _, name, _ in pkgutil.walk_packages([str(core_dir)], prefix="agos.core."):
        yield importlib.import_module(name)


def test_core_does_not_import_adapters_or_multica():
    """Structural invariant: the core stays isolated from concrete executors."""

    for module in _core_modules():
        src = Path(module.__file__).read_text(encoding="utf-8")
        assert not ADAPTER_IMPORT_RE.search(src), (
            f"{module.__name__} imports agos.adapters, which breaks executor-agnosticism"
        )
        assert not MULTICA_IMPORT_RE.search(src), (
            f"{module.__name__} imports multica directly, which breaks executor-agnosticism"
        )


def test_fake_adapter_is_an_executor_adapter():
    adapter = FakeAdapter(events=[])
    assert isinstance(adapter, ExecutorAdapter)


def test_core_produces_ledger_evidence_and_status_from_fake_events(tmp_repo: Path):
    """Drive the core through the executor seam using only fake events."""

    paths = repo_paths(tmp_repo)
    ledger = Ledger(paths.ledger)
    task = Task(
        id=new_task_id(),
        title="t",
        intent="i",
        workflow="feature",
        gates=["tests_pass"],
        executor=ExecutorBinding(adapter="fake", agent="a"),
    )
    ledger.append({"type": "task_started", "task_id": task.id})
    ledger.append({"type": "gates_locked", "gates": task.gates})

    adapter = FakeAdapter(
        events=[
            Event(seq=1, ts="2026-06-21T00:00:01Z", kind="text", content="hi", raw={}),
            Event(seq=2, ts="2026-06-21T00:00:02Z", kind="tool_call", content="edit", raw={}),
            Event(seq=3, ts="2026-06-21T00:00:03Z", kind="run_complete", content="done", raw={}),
        ]
    )
    run = adapter.start(task)
    ledger.append({"type": "executor_dispatched", "run_id": run.run_id, "issue_id": run.issue_id})

    store = EvidenceStore(paths.evidence)
    last_seq = None
    for event in adapter.stream_events(run.run_id, since=last_seq):
        store.append_message(
            run.run_id,
            {"seq": event.seq, "ts": event.ts, "kind": event.kind, "content": event.content},
        )
        last_seq = event.seq
    ledger.append(
        {
            "type": "checkpoint",
            "evidence_refs": [f"messages/{run.run_id}.jsonl"],
            "last_seq": last_seq,
        }
    )

    status = derive_status(
        paths,
        task_id=task.id,
        gates=task.gates,
        ledger=ledger,
        executor_run=run,
        last_event_seq=last_seq,
        gate_states=None,
    )
    save_status(status, paths)

    records = ledger.read_all()
    ledger.verify_chain()
    assert [record["type"] for record in records] == [
        "task_started",
        "gates_locked",
        "executor_dispatched",
        "checkpoint",
    ]
    assert records[-1]["last_seq"] == 3
    assert status.last_event_seq == 3
    assert status.executor_run is not None
    assert status.executor_run.adapter == "fake"
    assert status.gates["tests_pass"].state == "unknown"
    assert status.ledger_head_hash == records[-1]["hash"]
    assert load_status(paths) == status

    message_path = paths.evidence / "messages" / f"{run.run_id}.jsonl"
    messages = [json.loads(line) for line in message_path.read_text(encoding="utf-8").splitlines()]
    assert [message["seq"] for message in messages] == [1, 2, 3]
