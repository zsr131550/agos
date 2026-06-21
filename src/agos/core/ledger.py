"""Ledgers for repo state and hash-chained task state."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    """Return an RFC3339 timestamp in UTC."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(obj: dict) -> str:
    """Return deterministic compact JSON for hashing."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(prev_hash: str, record: dict) -> str:
    """Return the hash of `prev_hash + canonical_json(record)`."""

    payload = prev_hash + canonical_json(record)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_repo_record(path: Path, record_type: str, **payload: Any) -> dict[str, Any]:
    """Append a plain JSONL record to the repo ledger."""

    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": utc_now(), "type": record_type, **payload}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


class LedgerTamperError(Exception):
    """Raised when the ledger chain fails verification."""


class Ledger:
    """Append-only hash-chained JSONL ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _records(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def read_all(self) -> list[dict]:
        """Return all ledger records in order."""

        return self._records()

    def head_hash(self) -> str:
        """Return the last record hash, or an empty string when empty."""

        records = self._records()
        return records[-1]["hash"] if records else ""

    def next_seq(self) -> int:
        """Return the next 1-based sequence number."""

        records = self._records()
        return records[-1]["seq"] + 1 if records else 1

    def append(self, record: dict) -> dict:
        """Append a record with assigned `seq`, `prev_hash`, and `hash`."""

        prev_hash = self.head_hash()
        full = dict(record)
        full.setdefault("seq", self.next_seq())
        full.setdefault("ts", utc_now())
        full["prev_hash"] = prev_hash
        body = {key: value for key, value in full.items() if key != "hash"}
        full["hash"] = compute_hash(prev_hash, body)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(full, ensure_ascii=False) + "\n")
        return full

    def verify_chain(self) -> None:
        """Recompute every hash from line 1 and raise on any mismatch."""

        prev_hash = ""
        expected_seq = 1
        for line_no, record in enumerate(self._records(), start=1):
            if record.get("prev_hash") != prev_hash:
                raise LedgerTamperError(
                    f"record {line_no}: prev_hash mismatch "
                    f"(expected {prev_hash!r}, got {record.get('prev_hash')!r})"
                )
            if record.get("seq") != expected_seq:
                raise LedgerTamperError(
                    f"record {line_no}: seq mismatch "
                    f"(expected {expected_seq}, got {record.get('seq')!r})"
                )

            body = {key: value for key, value in record.items() if key != "hash"}
            expected_hash = compute_hash(prev_hash, body)
            if record.get("hash") != expected_hash:
                raise LedgerTamperError(f"record {line_no}: hash mismatch")

            prev_hash = record["hash"]
            expected_seq += 1


def append_task_record(path: Path, record_type: str, **payload: Any) -> dict[str, Any]:
    """Compatibility helper for appending one task-ledger record."""

    return Ledger(path).append({"type": record_type, **payload})


def read_last_task_record(path: Path) -> dict[str, Any] | None:
    """Return the final record from a task ledger if present."""

    records = Ledger(path).read_all()
    return records[-1] if records else None
