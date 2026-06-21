from __future__ import annotations

import json
from types import SimpleNamespace

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import Event, ExecutorRun
from agos.core.config import default_config, resolve_gates
from agos.core.gate import gates_locked_payload
from agos.core.ledger import append_task_record
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, load_status, save_status
from agos.core.task import ExecutorBinding, Task, new_task_id, save_task

runner = CliRunner()


def _write_active_task(tmp_repo, *, run_id: str = "run-123", last_event_seq: int | None = None):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    config = default_config()
    paths.agos_yaml.write_text(
        yaml.safe_dump(config.model_dump(mode="python"), sort_keys=False),
        encoding="utf-8",
    )

    task = Task(
        id=f"agos-{new_task_id()}",
        title="Checkpoint task",
        intent="Collect executor evidence",
        workflow="feature",
        gates=["tests_pass", "no_secrets_in_diff"],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)

    append_task_record(paths.ledger, "task_started", task_id=task.id, title=task.title)
    resolved_gates = resolve_gates(config, task.workflow, override=task.gates)
    append_task_record(
        paths.ledger,
        "gates_locked",
        task_id=task.id,
        gates=gates_locked_payload(resolved_gates),
    )
    dispatched = append_task_record(
        paths.ledger,
        "executor_dispatched",
        task_id=task.id,
        adapter="multica",
        run_id=run_id,
        issue_id="AGO-99",
    )

    status = TaskStatus.for_started_task(
        task=task,
        run=ExecutorRun(adapter="multica", run_id=run_id, issue_id="AGO-99"),
        ledger_head_hash=dispatched["hash"],
    )
    status.last_event_seq = last_event_seq
    save_status(status, paths)
    return paths, task


def test_checkpoint_once_writes_messages_anchor_ledger_and_status(monkeypatch, tmp_repo):
    paths, _task = _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    events = [
        Event(
            seq=1,
            ts="2026-06-21T00:00:01Z",
            kind="text",
            content="hello",
            raw={"seq": 1, "ts": "2026-06-21T00:00:01Z", "kind": "text", "content": "hello"},
        ),
        Event(
            seq=2,
            ts="2026-06-21T00:00:02Z",
            kind="tool_call",
            content="edit file",
            raw={
                "seq": 2,
                "ts": "2026-06-21T00:00:02Z",
                "kind": "tool_call",
                "content": "edit file",
            },
        ),
    ]
    seen = {}

    def fake_stream(self, run_id: str, since: int | None = None):
        seen["run_id"] = run_id
        seen["since"] = since
        return iter(events)

    monkeypatch.setattr("agos.cli.cmd_checkpoint.MulticaAdapter.stream_events", fake_stream)

    result = runner.invoke(app, ["checkpoint", "--once"])

    assert result.exit_code == 0
    assert seen == {"run_id": "run-123", "since": None}

    message_path = paths.evidence / "messages" / "run-123.jsonl"
    lines = [json.loads(line) for line in message_path.read_text(encoding="utf-8").splitlines()]
    assert [line["seq"] for line in lines] == [1, 2]
    assert [line["kind"] for line in lines] == ["text", "tool_call"]

    anchor_files = list((paths.evidence / "repo_anchor").glob("*.json"))
    assert len(anchor_files) == 1
    anchor = json.loads(anchor_files[0].read_text(encoding="utf-8"))
    assert len(anchor["head"]) == 40
    assert "status_porcelain" in anchor
    assert anchor.get("claim") is None

    ledger_records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    checkpoint = ledger_records[-1]
    assert checkpoint["type"] == "checkpoint"
    assert checkpoint["repo_head"] == anchor["head"]
    assert checkpoint["last_seq"] == 2
    assert checkpoint["evidence_refs"] == [
        "messages/run-123.jsonl",
        f"repo_anchor/{anchor_files[0].name}",
    ]

    status = load_status(paths)
    assert status is not None
    assert status.last_event_seq == 2
    assert status.ledger_head_hash == checkpoint["hash"]


def test_checkpoint_follow_stops_after_run_complete(monkeypatch, tmp_repo):
    paths, _task = _write_active_task(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    calls: list[int | None] = []

    def fake_stream(self, run_id: str, since: int | None = None):
        del run_id
        calls.append(since)
        if since is None:
            return iter(
                [
                    Event(
                        seq=1,
                        ts="2026-06-21T00:00:01Z",
                        kind="text",
                        content="started",
                        raw={
                            "seq": 1,
                            "ts": "2026-06-21T00:00:01Z",
                            "kind": "text",
                            "content": "started",
                        },
                    )
                ]
            )
        return iter(
            [
                Event(
                    seq=2,
                    ts="2026-06-21T00:00:02Z",
                    kind="run_complete",
                    content="done",
                    raw={
                        "seq": 2,
                        "ts": "2026-06-21T00:00:02Z",
                        "kind": "run_complete",
                        "content": "done",
                    },
                )
            ]
        )

    monkeypatch.setattr("agos.cli.cmd_checkpoint.MulticaAdapter.stream_events", fake_stream)
    monkeypatch.setattr("agos.cli.cmd_checkpoint.time.sleep", lambda _seconds: None)

    result = runner.invoke(app, ["checkpoint", "--follow"])

    assert result.exit_code == 0
    assert calls == [None, 1]

    ledger_records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in ledger_records[-2:]] == ["checkpoint", "checkpoint"]

    status = load_status(paths)
    assert status is not None
    assert status.last_event_seq == 2

    message_path = paths.evidence / "messages" / "run-123.jsonl"
    lines = [json.loads(line) for line in message_path.read_text(encoding="utf-8").splitlines()]
    assert [line["kind"] for line in lines] == ["text", "run_complete"]
