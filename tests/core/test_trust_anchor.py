from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agos.core.adapter import ExecutorRun
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.core.trust_anchor import (
    FileTrustAnchorStore,
    GitRefTrustAnchorStore,
    TrustAnchorPayload,
    canonical_json,
    publish_current_anchor,
    verify_current_anchor,
)


def _payload(**overrides) -> TrustAnchorPayload:
    payload = {
        "schema_version": 1,
        "task_id": "agos-task-01",
        "ledger_head_hash": "b" * 64,
        "ledger_seq": 2,
        "repo_head": "c" * 40,
        "created_at": "2026-06-24T00:00:00Z",
        "issuer": "CI",
    }
    payload.update(overrides)
    return TrustAnchorPayload.model_validate(payload)


def _write_active_task(tmp_repo: Path) -> tuple[Task, object]:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    task = Task(
        id="agos-task-01",
        title="Trust anchor task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    first = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    ledger.append({"type": "gates_locked", "task_id": task.id, "gates": []})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-1", issue_id=None),
            ledger_head_hash=first["hash"],
        ),
        paths,
    )
    return task, paths


def test_canonical_json_is_sorted_compact():
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_trust_anchor_payload_rejects_empty_fields():
    with pytest.raises(ValueError):
        _payload(task_id="")


def test_file_store_publish_and_verify_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_repo: Path):
    task, paths = _write_active_task(tmp_repo)
    monkeypatch.setattr("agos.core.trust_anchor.utc_now", lambda: "2026-06-24T00:00:00Z")
    monkeypatch.setattr("agos.core.trust_anchor.git_head", lambda _root: "a" * 40)

    store = FileTrustAnchorStore(paths.evidence / "anchors.json")
    payload = publish_current_anchor(paths, store, issuer="CI")

    assert payload.task_id == task.id
    assert payload.ledger_seq == 2
    assert payload.repo_head == "a" * 40
    assert store.read(task.id) == payload
    verification = verify_current_anchor(paths, store)
    assert verification.passed is True
    assert verification.anchor == payload


def test_verify_current_anchor_rejects_stale_ledger_head(monkeypatch: pytest.MonkeyPatch, tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    monkeypatch.setattr("agos.core.trust_anchor.utc_now", lambda: "2026-06-24T00:00:00Z")
    monkeypatch.setattr("agos.core.trust_anchor.git_head", lambda _root: "a" * 40)
    store = FileTrustAnchorStore(paths.evidence / "anchors.json")
    publish_current_anchor(paths, store, issuer="CI")
    Ledger(paths.ledger).append({"type": "checkpoint", "repo_head": "a" * 40})

    verification = verify_current_anchor(paths, store)

    assert verification.passed is False
    assert any("ledger head" in issue for issue in verification.issues)


def test_file_store_rejects_task_mismatch(tmp_path: Path):
    store = FileTrustAnchorStore(tmp_path / "anchor.json")
    store.write(_payload(task_id="other-task"))

    with pytest.raises(ValueError, match="task mismatch"):
        store.read("agos-task-01")


def test_publish_current_anchor_requires_status(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    task = Task(
        id="agos-task-01",
        title="Trust anchor task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    Ledger(paths.ledger).append({"type": "task_started", "task_id": task.id})

    with pytest.raises(ValueError, match="status"):
        publish_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"), issuer="CI")


def test_verify_current_anchor_handles_missing_task_and_tampered_ledger(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    missing = verify_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"))
    assert missing.passed is False

    _task, paths = _write_active_task(tmp_repo)
    records = paths.ledger.read_text(encoding="utf-8").splitlines()
    record = json.loads(records[0])
    record["type"] = "forged"
    records[0] = json.dumps(record)
    paths.ledger.write_text("\n".join(records) + "\n", encoding="utf-8")
    tampered = verify_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"))
    assert tampered.passed is False
    assert "ledger verification failed" in tampered.issues[0]


def test_verify_current_anchor_reports_schema_and_repo_mismatches(monkeypatch, tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    monkeypatch.setattr("agos.core.trust_anchor.git_head", lambda _root: "a" * 40)
    anchor = publish_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"), issuer="CI")
    changed = anchor.model_copy(update={"schema_version": 2, "repo_head": "d" * 40})
    FileTrustAnchorStore(paths.evidence / "anchor.json").write(changed)

    verification = verify_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"))

    assert verification.passed is False
    assert any("schema version" in issue for issue in verification.issues)
    assert any("repo head" in issue for issue in verification.issues)


def test_git_ref_store_validates_task_id_and_uses_git_commands(monkeypatch: pytest.MonkeyPatch, tmp_repo: Path):
    store = GitRefTrustAnchorStore(tmp_repo)
    payload = TrustAnchorPayload(
        schema_version=1,
        task_id="agos-task-01",
        ledger_head_hash="b" * 64,
        ledger_seq=3,
        repo_head="c" * 40,
        created_at="2026-06-24T00:00:00Z",
        issuer="CI",
    )
    calls: list[list[str]] = []

    def fake_run_command(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["git", "hash-object"]:
            return SimpleNamespace(returncode=0, stdout="deadbeef\n", stderr="")
        if args[:2] == ["git", "update-ref"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["git", "cat-file"]:
            return SimpleNamespace(returncode=0, stdout=payload.canonical_json() + "\n", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr("agos.core.trust_anchor.run_command", fake_run_command)

    store.write(payload)
    loaded = store.read(payload.task_id)

    assert loaded == payload
    assert any(call[:2] == ["git", "update-ref"] for call in calls)
    assert any(call[:2] == ["git", "cat-file"] for call in calls)
    with pytest.raises(ValueError):
        store.ref_name("bad/task")
    with pytest.raises(ValueError):
        store.ref_name(" ")
