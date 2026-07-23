from __future__ import annotations

import json

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.adapter import Event, ExecutorRun, RunStatus
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
    config = default_config(agent="Lambda")
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

    monkeypatch.setattr("agos.cli.executor_registry.MulticaAdapter.stream_events", fake_stream)

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
    assert checkpoint["task_id"] == _task.id
    assert checkpoint["evidence_refs"] == [
        "messages/run-123.jsonl",
        f"repo_anchor/{anchor_files[0].name}",
    ]

    status = load_status(paths)
    assert status is not None
    assert status.last_event_seq == 2
    assert status.ledger_head_hash == checkpoint["hash"]


def test_checkpoint_once_does_not_apply_old_run_after_redispatch(monkeypatch, tmp_repo):
    paths, task = _write_active_task(tmp_repo)

    class FakeAdapter:
        def stream_events(self, run_id: str, since: int | None = None):
            assert run_id == "run-123"
            assert since is None
            append_task_record(
                paths.ledger,
                "executor_dispatched",
                task_id=task.id,
                adapter="multica",
                run_id="run-current",
                issue_id="AGO-100",
            )
            return iter(
                [
                    Event(
                        seq=1,
                        ts="2026-06-21T00:00:01Z",
                        kind="run_complete",
                        content="done",
                        raw={"seq": 1, "kind": "run_complete", "content": "done"},
                    )
                ]
            )

        def status(self, run_id: str, issue_id: str | None = None):
            raise AssertionError(f"old run must not be finalized: {run_id}/{issue_id}")

    from agos.cli import cmd_checkpoint

    monkeypatch.setattr(cmd_checkpoint, "git_head", lambda _repo: "head-sha")
    monkeypatch.setattr(cmd_checkpoint, "git_status_porcelain", lambda _repo: "")
    status = load_status(paths)
    assert status is not None

    completed, last_seq = cmd_checkpoint._checkpoint_once(
        adapter=FakeAdapter(),
        status=status,
        paths=paths,
    )

    assert completed is False
    assert last_seq is None
    records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in records][-1] == "executor_dispatched"
    assert not [
        record
        for record in records
        if record.get("run_id") == "run-123"
        and record["type"] in {"checkpoint", "executor_completed", "executor_blocked"}
    ]
    current = load_status(paths)
    assert current is not None
    assert current.phase == "executing"
    assert current.executor_run is not None
    assert current.executor_run.run_id == "run-current"


def test_checkpoint_auto_publishes_file_trust_anchor_when_configured(monkeypatch, tmp_repo):
    paths, task = _write_active_task(tmp_repo)
    raw_config = yaml.safe_load(paths.agos_yaml.read_text(encoding="utf-8"))
    raw_config["trust_anchor"] = {
        "backend": "file",
        "path": ".agos/tasks/current/evidence/anchors.json",
        "auto_publish_on_checkpoint": True,
        "issuer": "checkpoint-ci",
    }
    paths.agos_yaml.write_text(yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8")
    monkeypatch.chdir(tmp_repo)

    def fake_stream(self, run_id: str, since: int | None = None):
        del self, run_id, since
        return iter(
            [
                Event(
                    seq=1,
                    ts="2026-06-21T00:00:01Z",
                    kind="text",
                    content="checkpointed",
                    raw={
                        "seq": 1,
                        "ts": "2026-06-21T00:00:01Z",
                        "kind": "text",
                        "content": "checkpointed",
                    },
                )
            ]
        )

    monkeypatch.setattr("agos.cli.executor_registry.MulticaAdapter.stream_events", fake_stream)

    result = runner.invoke(app, ["checkpoint", "--once"])

    assert result.exit_code == 0, result.stderr
    anchor_path = paths.evidence / "anchors.json"
    payload = json.loads(anchor_path.read_text(encoding="utf-8"))
    ledger_records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    assert payload["task_id"] == task.id
    assert payload["issuer"] == "checkpoint-ci"
    assert payload["ledger_seq"] == ledger_records[-1]["seq"]
    assert payload["ledger_head_hash"] == ledger_records[-1]["hash"]


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

    monkeypatch.setattr("agos.cli.executor_registry.MulticaAdapter.stream_events", fake_stream)
    monkeypatch.setattr(
        "agos.cli.executor_registry.MulticaAdapter.status",
        lambda self, run_id, issue_id=None: RunStatus(state="completed", detail="done"),
    )
    monkeypatch.setattr("agos.cli.cmd_checkpoint.time.sleep", lambda _seconds: None)

    result = runner.invoke(app, ["checkpoint", "--follow"])

    assert result.exit_code == 0
    assert calls == [None, 1]

    ledger_records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    assert [record["type"] for record in ledger_records[-3:]] == [
        "checkpoint",
        "checkpoint",
        "executor_completed",
    ]

    status = load_status(paths)
    assert status is not None
    assert status.last_event_seq == 2
    assert status.phase == "done"

    message_path = paths.evidence / "messages" / "run-123.jsonl"
    lines = [json.loads(line) for line in message_path.read_text(encoding="utf-8").splitlines()]
    assert [line["kind"] for line in lines] == ["text", "run_complete"]

    ledger_records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    assert ledger_records[-1]["type"] == "executor_completed"
    assert ledger_records[-1]["run_id"] == "run-123"


def test_checkpoint_uses_adapter_status_for_run_complete_event(monkeypatch, tmp_repo):
    paths, _task = _write_active_task(tmp_repo)

    class FakeAdapter:
        def stream_events(self, run_id: str, since: int | None = None):
            assert run_id == "run-123"
            assert since is None
            return iter(
                [
                    Event(
                        seq=1,
                        ts="2026-06-21T00:00:01Z",
                        kind="run_complete",
                        content="completed",
                        raw={
                            "seq": 1,
                            "ts": "2026-06-21T00:00:01Z",
                            "kind": "run_complete",
                            "content": "completed",
                        },
                    )
                ]
            )

        def status(self, run_id: str, issue_id: str | None = None):
            assert run_id == "run-123"
            assert issue_id == "AGO-99"
            return RunStatus(
                state="failed",
                detail="Executor completed without writing files to outputs/agos-01",
            )

    from agos.cli import cmd_checkpoint

    monkeypatch.setattr(cmd_checkpoint, "git_head", lambda _repo: "head-sha")
    monkeypatch.setattr(cmd_checkpoint, "git_status_porcelain", lambda _repo: "")

    status = load_status(paths)
    assert status is not None

    completed, last_seq = cmd_checkpoint._checkpoint_once(
        adapter=FakeAdapter(),
        status=status,
        paths=paths,
    )

    assert completed is True
    assert last_seq == 1
    reloaded = load_status(paths)
    assert reloaded is not None
    assert reloaded.phase == "blocked"
    ledger_records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    assert ledger_records[-1]["type"] == "executor_blocked"
    assert ledger_records[-1]["state"] == "failed"
    assert "without writing files" in ledger_records[-1]["detail"]


def test_checkpoint_once_marks_completed_when_run_finished_without_new_events(monkeypatch, tmp_repo):
    paths, _task = _write_active_task(tmp_repo)

    class FakeAdapter:
        def stream_events(self, run_id: str, since: int | None = None):
            del run_id, since
            return iter([])

        def status(self, run_id: str, issue_id: str | None = None):
            assert run_id == "run-123"
            assert issue_id == "AGO-99"
            return RunStatus(state="completed", detail="done")

    status = load_status(paths)
    assert status is not None

    from agos.cli import cmd_checkpoint

    completed, last_seq = cmd_checkpoint._checkpoint_once(
        adapter=FakeAdapter(),
        status=status,
        paths=paths,
    )

    assert completed is True
    assert last_seq is None
    reloaded = load_status(paths)
    assert reloaded is not None
    assert reloaded.phase == "done"
    ledger_records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    assert ledger_records[-1]["type"] == "executor_completed"


def test_checkpoint_once_marks_blocked_when_run_blocked_without_new_events(tmp_repo):
    paths, _task = _write_active_task(tmp_repo)

    class FakeAdapter:
        def stream_events(self, run_id: str, since: int | None = None):
            del run_id, since
            return iter([])

        def status(self, run_id: str, issue_id: str | None = None):
            assert run_id == "run-123"
            assert issue_id == "AGO-99"
            return RunStatus(state="blocked", detail="needs human")

    status = load_status(paths)
    assert status is not None

    from agos.cli import cmd_checkpoint

    completed, last_seq = cmd_checkpoint._checkpoint_once(
        adapter=FakeAdapter(),
        status=status,
        paths=paths,
    )

    assert completed is True
    assert last_seq is None
    reloaded = load_status(paths)
    assert reloaded is not None
    assert reloaded.phase == "blocked"
    ledger_records = [json.loads(line) for line in paths.ledger.read_text(encoding="utf-8").splitlines()]
    assert ledger_records[-1]["type"] == "executor_blocked"
    assert ledger_records[-1]["detail"] == "needs human"
