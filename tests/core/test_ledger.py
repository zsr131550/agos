"""Tests for the hash-chained ledger."""
from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import time

import pytest

from agos.core.ledger import Ledger, LedgerTamperError, canonical_json, compute_hash


def _append_batch(
    path_text: str,
    worker: int,
    count: int,
    start_event,
) -> None:
    """Append from one spawned process with an enlarged tail/write race window."""

    import agos.core.ledger as ledger_module

    original_dumps = ledger_module.json.dumps

    def delayed_dumps(value, *args, **kwargs):
        if isinstance(value, dict) and "hash" in value:
            time.sleep(0.01)
        return original_dumps(value, *args, **kwargs)

    ledger_module.json.dumps = delayed_dumps
    if not start_event.wait(timeout=10):
        raise RuntimeError("concurrent ledger test start timed out")
    ledger = Ledger(Path(path_text))
    for index in range(count):
        ledger.append({"type": "concurrent", "worker": worker, "index": index})


def test_canonical_json_is_sorted_compact():
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_compute_hash_first_record_prev_empty():
    rec = {
        "seq": 1,
        "ts": "2026-06-21T00:00:00Z",
        "type": "task_started",
        "prev_hash": "",
    }
    h = compute_hash("", {k: v for k, v in rec.items() if k != "hash"})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_append_first_record_has_empty_prev_hash(tmp_path: Path):
    led = Ledger(tmp_path / "ledger.jsonl")
    rec = led.append(
        {"ts": "2026-06-21T00:00:00Z", "type": "task_started", "task_id": "T1"}
    )
    assert rec["seq"] == 1
    assert rec["prev_hash"] == ""
    assert "hash" in rec
    assert led.head_hash() == rec["hash"]


def test_append_chains_prev_hash(tmp_path: Path):
    led = Ledger(tmp_path / "ledger.jsonl")
    r1 = led.append({"type": "task_started"})
    r2 = led.append({"type": "gates_locked", "gates": ["tests_pass"]})
    assert r2["seq"] == 2
    assert r2["prev_hash"] == r1["hash"]
    assert led.head_hash() == r2["hash"]


def test_verify_chain_passes_on_clean_ledger(tmp_path: Path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append({"type": "task_started"})
    led.append({"type": "gates_locked", "gates": ["a", "b"]})
    led.append({"type": "checkpoint", "evidence_refs": ["x.jsonl"], "repo_head": "abc"})
    led.verify_chain()


def test_verify_chain_detects_tampered_field(tmp_path: Path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append({"type": "task_started"})
    led.append({"type": "gates_locked", "gates": ["a"]})
    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["gates"] = ["b"]
    lines[1] = json.dumps(rec)
    (tmp_path / "ledger.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(LedgerTamperError):
        Ledger(tmp_path / "ledger.jsonl").verify_chain()


def test_verify_chain_detects_recomputed_single_hash_only(tmp_path: Path):
    """Recomputing only the tampered record's hash does not fool the chain."""

    led = Ledger(tmp_path / "ledger.jsonl")
    led.append({"type": "task_started"})
    led.append({"type": "gates_locked", "gates": ["a"]})
    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    rec1 = json.loads(lines[0])
    rec1["type"] = "task_started_X"
    rec1["hash"] = compute_hash("", {k: v for k, v in rec1.items() if k != "hash"})
    lines[0] = json.dumps(rec1)
    (tmp_path / "ledger.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(LedgerTamperError):
        Ledger(tmp_path / "ledger.jsonl").verify_chain()


def test_rewrite_all_and_recompute_does_NOT_raise(tmp_path: Path):
    """Documents the v0.1 trust-anchor limitation."""

    led = Ledger(tmp_path / "ledger.jsonl")
    led.append({"type": "task_started"})
    led.append({"type": "gates_locked", "gates": ["a"]})
    led.append({"type": "checkpoint"})
    lines = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    rewritten: list[str] = []
    prev = ""
    for line in lines:
        rec = json.loads(line)
        rec["type"] = rec["type"] + "_forged"
        rec["prev_hash"] = prev
        rec["hash"] = compute_hash(prev, {k: v for k, v in rec.items() if k != "hash"})
        rewritten.append(json.dumps(rec))
        prev = rec["hash"]
    (tmp_path / "ledger.jsonl").write_text(
        "\n".join(rewritten) + "\n",
        encoding="utf-8",
    )
    Ledger(tmp_path / "ledger.jsonl").verify_chain()


def test_read_all_returns_in_order(tmp_path: Path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append({"type": "a"})
    led.append({"type": "b"})
    assert [r["type"] for r in led.read_all()] == ["a", "b"]


def test_head_hash_empty_is_empty_string(tmp_path: Path):
    assert Ledger(tmp_path / "ledger.jsonl").head_hash() == ""
    assert Ledger(tmp_path / "ledger.jsonl").next_seq() == 1


def test_append_reads_existing_tail_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append({"type": "task_started"})

    calls = 0
    original = led._records

    def counted_records():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(led, "_records", counted_records)

    led.append({"type": "checkpoint"})

    assert calls <= 1


def test_concurrent_process_appends_preserve_one_chain(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    processes = [
        context.Process(
            target=_append_batch,
            args=(str(path), worker, 10, start_event),
        )
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    records = Ledger(path).read_all()
    assert [record["seq"] for record in records] == list(range(1, 41))
    assert len({(record["worker"], record["index"]) for record in records}) == 40
    Ledger(path).verify_chain()
