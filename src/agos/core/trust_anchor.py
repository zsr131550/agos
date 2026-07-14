"""Out-of-band trust anchors for AGOS task ledger heads."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from agos.core.command import run_command
from agos.core.config import TrustedSignerConfig, TrustAnchorConfig
from agos.core.ledger import Ledger
from agos.core.repo import AgosPaths, git_head
from agos.core.signing import (
    canonical_json,
    sign_ed25519,
    signature_message,
    trusted_public_key_path,
    verify_ed25519,
)
from agos.core.status import load_status
from agos.core.task import load_task


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
    signed: bool = False
    signer_issuer: str | None = None
    signer_key_id: str | None = None


class SignedTrustAnchorEnvelope(BaseModel):
    schema_version: Literal[1] = 1
    algorithm: Literal["Ed25519"] = "Ed25519"
    issuer: str
    key_id: str
    payload: TrustAnchorPayload
    signature: str

    @model_validator(mode="after")
    def _validate_envelope(self) -> "SignedTrustAnchorEnvelope":
        if not self.issuer.strip() or not self.key_id.strip() or not self.signature.strip():
            raise ValueError("signed anchor envelope fields must be non-empty")
        if self.issuer != self.payload.issuer:
            raise ValueError("signed anchor issuer does not match payload issuer")
        return self

    def signing_bytes(self) -> bytes:
        return signature_message(
            algorithm=self.algorithm,
            issuer=self.issuer,
            key_id=self.key_id,
            payload=self.payload.model_dump(mode="python"),
        )

    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="python"))


class TrustAnchorStore(Protocol):
    def write(self, payload: TrustAnchorPayload) -> None: ...

    def read(self, task_id: str) -> TrustAnchorPayload: ...


class SignedTrustAnchorStore(Protocol):
    def write(self, envelope: SignedTrustAnchorEnvelope) -> None: ...

    def read(self, task_id: str) -> SignedTrustAnchorEnvelope: ...


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


class SignedFileTrustAnchorStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, envelope: SignedTrustAnchorEnvelope) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(envelope.canonical_json() + "\n", encoding="utf-8")

    def read(self, task_id: str) -> SignedTrustAnchorEnvelope:
        if not self.path.is_file():
            raise FileNotFoundError(f"signed anchor not found: {self.path}")
        envelope = SignedTrustAnchorEnvelope.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )
        if envelope.payload.task_id != task_id:
            raise ValueError(
                "signed anchor task mismatch: "
                f"expected {task_id!r}, got {envelope.payload.task_id!r}"
            )
        return envelope


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


def store_from_config(paths: AgosPaths, config: TrustAnchorConfig) -> TrustAnchorStore:
    """Build the configured trust-anchor store for a governed repo."""

    if config.backend == "file":
        return FileTrustAnchorStore(_anchor_file_path(paths, config.path))
    if config.backend == "git-ref":
        return GitRefTrustAnchorStore(paths.root)
    raise ValueError(f"unsupported anchor backend: {config.backend}")


def _anchor_file_path(paths: AgosPaths, configured_path: str | None) -> Path:
    if configured_path is None:
        return paths.evidence / "anchors.json"
    path = Path(configured_path)
    return path if path.is_absolute() else paths.root / path


def publish_current_anchor(
    paths: AgosPaths,
    store: TrustAnchorStore,
    issuer: str,
) -> TrustAnchorPayload:
    payload = _current_anchor_payload(paths, issuer=issuer)
    store.write(payload)
    return payload


def publish_current_signed_anchor(
    paths: AgosPaths,
    store: SignedTrustAnchorStore,
    *,
    issuer: str,
    key_id: str,
    private_key_path: Path,
) -> SignedTrustAnchorEnvelope:
    provided_private_key = private_key_path.absolute()
    private_key = private_key_path.resolve()
    agos_dir = paths.agos_dir.resolve()
    if provided_private_key.is_relative_to(agos_dir) or private_key.is_relative_to(agos_dir):
        raise ValueError("private key must be outside .agos")
    payload = _current_anchor_payload(paths, issuer=issuer)
    signing_bytes = signature_message(
        algorithm="Ed25519",
        issuer=issuer,
        key_id=key_id,
        payload=payload.model_dump(mode="python"),
    )
    envelope = SignedTrustAnchorEnvelope(
        issuer=issuer,
        key_id=key_id,
        payload=payload,
        signature=sign_ed25519(signing_bytes, private_key),
    )
    store.write(envelope)
    return envelope


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

    current = _anchor_payload_from_verified_ledger(paths, task.id, ledger)
    try:
        anchor = store.read(task.id)
    except Exception as exc:
        issues.append(str(exc))
        return TrustAnchorVerification(task_id=task.id, passed=False, issues=issues, anchor=None)

    issues.extend(_anchor_payload_issues(current, anchor))
    return TrustAnchorVerification(
        task_id=task.id,
        passed=not issues,
        issues=issues,
        anchor=anchor,
    )


def verify_current_signed_anchor(
    paths: AgosPaths,
    store: SignedTrustAnchorStore,
    *,
    trusted_signers: list[TrustedSignerConfig],
    trusted_config_path: Path,
) -> TrustAnchorVerification:
    try:
        task = load_task(paths.task_yaml)
    except Exception as exc:
        return TrustAnchorVerification(task_id="", passed=False, issues=[str(exc)])

    ledger = Ledger(paths.ledger)
    try:
        ledger.verify_chain()
    except Exception as exc:
        return TrustAnchorVerification(
            task_id=task.id,
            passed=False,
            issues=[f"ledger verification failed: {exc}"],
        )
    current = _anchor_payload_from_verified_ledger(paths, task.id, ledger)
    try:
        envelope = store.read(task.id)
    except Exception as exc:
        return TrustAnchorVerification(task_id=task.id, passed=False, issues=[str(exc)])

    issues: list[str] = []
    signed = False
    try:
        public_key_path = trusted_public_key_path(
            trusted_signers,
            issuer=envelope.issuer,
            key_id=envelope.key_id,
            trusted_config_path=trusted_config_path,
        )
        verify_ed25519(envelope.signing_bytes(), envelope.signature, public_key_path)
        signed = True
    except Exception as exc:
        issues.append(str(exc))
    issues.extend(_anchor_payload_issues(current, envelope.payload))
    return TrustAnchorVerification(
        task_id=task.id,
        passed=not issues,
        issues=issues,
        anchor=envelope.payload,
        signed=signed,
        signer_issuer=envelope.issuer,
        signer_key_id=envelope.key_id,
    )


def _current_anchor_payload(paths: AgosPaths, *, issuer: str) -> TrustAnchorPayload:
    task = load_task(paths.task_yaml)
    if not paths.status_json.is_file():
        raise ValueError("current task status is missing")
    status = load_status(paths)
    if status is None:
        raise ValueError("current task status is missing")
    ledger = Ledger(paths.ledger)
    ledger.verify_chain()
    return _anchor_payload_from_verified_ledger(paths, task.id, ledger, issuer=issuer)


def _anchor_payload_from_verified_ledger(
    paths: AgosPaths,
    task_id: str,
    ledger: Ledger,
    *,
    issuer: str = "current",
) -> TrustAnchorPayload:
    return TrustAnchorPayload(
        task_id=task_id,
        ledger_head_hash=ledger.head_hash(),
        ledger_seq=ledger.next_seq() - 1,
        repo_head=git_head(paths.root),
        created_at=utc_now(),
        issuer=issuer,
    )


def _anchor_payload_issues(
    current: TrustAnchorPayload,
    anchor: TrustAnchorPayload,
) -> list[str]:
    issues: list[str] = []
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
    return issues


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_task_id(task_id: str) -> str:
    if not task_id or task_id != task_id.strip():
        raise ValueError("task_id must be non-empty")
    if "/" in task_id or "\\" in task_id:
        raise ValueError(f"invalid task_id: {task_id!r}")
    return task_id
