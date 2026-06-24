"""Out-of-band trust anchors for AGOS task ledger heads."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, field_validator

from agos.core.command import run_command
from agos.core.ledger import Ledger
from agos.core.repo import AgosPaths, git_head
from agos.core.status import load_status
from agos.core.task import load_task


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class TrustAnchorPayload(BaseModel):
    schema_version: int = 1
    task_id: str
    ledger_head_hash: str
    ledger_seq: int
    repo_head: str
    created_at: str
    issuer: str

    @field_validator("task_id", "ledger_head_hash", "repo_head", "created_at", "issuer")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trust anchor fields must be non-empty")
        return value

    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="python"))


class TrustAnchorVerification(BaseModel):
    task_id: str
    passed: bool
    issues: list[str] = Field(default_factory=list)
    anchor: TrustAnchorPayload | None = None


class TrustAnchorStore(Protocol):
    def write(self, payload: TrustAnchorPayload) -> None: ...

    def read(self, task_id: str) -> TrustAnchorPayload: ...


class FileTrustAnchorStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, payload: TrustAnchorPayload) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(payload.canonical_json() + "\n", encoding="utf-8")

    def read(self, task_id: str) -> TrustAnchorPayload:
        if not self.path.is_file():
            raise FileNotFoundError(f"anchor not found: {self.path}")
        payload = TrustAnchorPayload.model_validate_json(self.path.read_text(encoding="utf-8"))
        if payload.task_id != task_id:
            raise ValueError(
                f"anchor task mismatch: expected {task_id!r}, got {payload.task_id!r}"
            )
        return payload


class GitRefTrustAnchorStore:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def ref_name(self, task_id: str) -> str:
        return f"refs/agos/anchors/{_safe_task_id(task_id)}"

    def write(self, payload: TrustAnchorPayload) -> None:
        ref = self.ref_name(payload.task_id)
        obj = run_command(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=self.repo_root,
            input=payload.canonical_json(),
            capture_output=True,
            text=True,
            check=True,
        )
        sha = obj.stdout.strip()
        run_command(
            ["git", "update-ref", ref, sha],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )

    def read(self, task_id: str) -> TrustAnchorPayload:
        ref = self.ref_name(task_id)
        show = run_command(
            ["git", "cat-file", "-p", ref],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = TrustAnchorPayload.model_validate_json(show.stdout)
        if payload.task_id != task_id:
            raise ValueError(
                f"anchor task mismatch: expected {task_id!r}, got {payload.task_id!r}"
            )
        return payload


def publish_current_anchor(
    paths: AgosPaths,
    store: TrustAnchorStore,
    issuer: str,
) -> TrustAnchorPayload:
    task = load_task(paths.task_yaml)
    status = load_status(paths)
    if status is None:
        raise ValueError("current task status is missing")
    ledger = Ledger(paths.ledger)
    ledger.verify_chain()
    payload = TrustAnchorPayload(
        task_id=task.id,
        ledger_head_hash=ledger.head_hash(),
        ledger_seq=ledger.next_seq() - 1,
        repo_head=git_head(paths.root),
        created_at=utc_now(),
        issuer=issuer,
    )
    store.write(payload)
    return payload


def verify_current_anchor(
    paths: AgosPaths,
    store: TrustAnchorStore,
) -> TrustAnchorVerification:
    issues: list[str] = []
    try:
        task = load_task(paths.task_yaml)
    except Exception as exc:
        return TrustAnchorVerification(task_id="", passed=False, issues=[str(exc)])

    ledger = Ledger(paths.ledger)
    try:
        ledger.verify_chain()
    except Exception as exc:
        issues.append(f"ledger verification failed: {exc}")
        return TrustAnchorVerification(task_id=task.id, passed=False, issues=issues)

    current = TrustAnchorPayload(
        schema_version=1,
        task_id=task.id,
        ledger_head_hash=ledger.head_hash(),
        ledger_seq=ledger.next_seq() - 1,
        repo_head=git_head(paths.root),
        created_at=utc_now(),
        issuer="current",
    )
    try:
        anchor = store.read(task.id)
    except Exception as exc:
        issues.append(str(exc))
        return TrustAnchorVerification(task_id=task.id, passed=False, issues=issues, anchor=None)

    if anchor.schema_version != current.schema_version:
        issues.append(
            f"schema version mismatch: expected {current.schema_version}, got {anchor.schema_version}"
        )
    if anchor.ledger_head_hash != current.ledger_head_hash:
        issues.append(
            "ledger head mismatch: "
            f"expected {current.ledger_head_hash}, got {anchor.ledger_head_hash}"
        )
    if anchor.ledger_seq != current.ledger_seq:
        issues.append(
            f"ledger seq mismatch: expected {current.ledger_seq}, got {anchor.ledger_seq}"
        )
    if anchor.repo_head != current.repo_head:
        issues.append(
            f"repo head mismatch: expected {current.repo_head}, got {anchor.repo_head}"
        )
    return TrustAnchorVerification(
        task_id=task.id,
        passed=not issues,
        issues=issues,
        anchor=anchor,
    )


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_task_id(task_id: str) -> str:
    if not task_id or task_id != task_id.strip():
        raise ValueError("task_id must be non-empty")
    if "/" in task_id or "\\" in task_id:
        raise ValueError(f"invalid task_id: {task_id!r}")
    return task_id
