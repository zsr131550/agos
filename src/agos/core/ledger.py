"""JSONL ledgers for repo and task state."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    """Return an RFC3339 timestamp in UTC."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def append_repo_record(path: Path, record_type: str, **payload: Any) -> dict[str, Any]:
    """Append a plain JSONL record to the repo ledger."""

    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": utc_now(), "type": record_type, **payload}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")
    return record


def append_task_record(path: Path, record_type: str, **payload: Any) -> dict[str, Any]:
    """Append a hash-chained task ledger record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    previous = read_last_task_record(path)
    prev_hash = previous["hash"] if previous else ""
    seq = previous["seq"] + 1 if previous else 1
    record = {
        "seq": seq,
        "ts": utc_now(),
        "type": record_type,
        **payload,
        "prev_hash": prev_hash,
    }
    record_hash = hashlib.sha256(f"{prev_hash}{_canonical_json(record)}".encode("utf-8")).hexdigest()
    record["hash"] = record_hash
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")
    return record


def read_last_task_record(path: Path) -> dict[str, Any] | None:
    """Return the final record from a task ledger if present."""

    if not path.exists():
        return None
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])

