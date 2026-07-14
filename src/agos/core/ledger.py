"""Ledgers for repo state and hash-chained task state."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agos.core.file_lock import exclusive_file_lock


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
    with exclusive_file_lock(path):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


class LedgerTamperError(Exception):
    """Raised when the ledger chain fails verification."""


class Ledger:
    """Append-only hash-chained JSONL ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _last_record(self) -> dict | None:
        with exclusive_file_lock(self.path):
            return self._last_record_unlocked()

    def _last_record_unlocked(self) -> dict | None:
        if not self.path.exists():
            return None

        with self.path.open("rb") as handle:
            handle.seek(0, 2)
            end = handle.tell()
            if end == 0:
                return None

            position = end - 1
            while position >= 0:
                handle.seek(position)
                byte = handle.read(1)
                if byte not in {b"\n", b"\r"}:
                    break
                position -= 1

            if position < 0:
                return None

            while position >= 0:
                handle.seek(position)
                byte = handle.read(1)
                if byte == b"\n":
                    position += 1
                    break
                position -= 1
            else:
                position = 0

            handle.seek(position)
            line = handle.read(end - position).decode("utf-8").strip()
            if not line:
                return None
            return json.loads(line)

    def _records(self) -> list[dict]:
        with exclusive_file_lock(self.path):
            return self._records_unlocked()

    def _records_unlocked(self) -> list[dict]:
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

        record = self._last_record()
        return record["hash"] if record else ""

    def next_seq(self) -> int:
        """Return the next 1-based sequence number."""

        record = self._last_record()
        return record["seq"] + 1 if record else 1

    def append(self, record: dict) -> dict:
        """Append a record with assigned `seq`, `prev_hash`, and `hash`."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            tail = self._last_record_unlocked()
            prev_hash = tail["hash"] if tail else ""
            full = dict(record)
            full.setdefault("seq", tail["seq"] + 1 if tail else 1)
            full.setdefault("ts", utc_now())
            full["prev_hash"] = prev_hash
            body = {key: value for key, value in full.items() if key != "hash"}
            full["hash"] = compute_hash(prev_hash, body)

            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(full, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
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
