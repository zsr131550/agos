"""Candidate provenance classification and verification helpers."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agos.core.config import TrustedSignerConfig
from agos.core.execution import CandidatePatch, CandidateProvenanceSource
from agos.core.repo import AgosPaths
from agos.core.signing import (
    canonical_json,
    sign_ed25519,
    signature_message,
    trusted_public_key_path,
    verify_ed25519,
)


def candidate_provenance_source(candidate: CandidatePatch) -> CandidateProvenanceSource:
    """Classify old candidate records without mutating their persisted form."""

    if candidate.provenance is None:
        return "legacy_unattested"
    return candidate.provenance.source


class CandidateAttestationPayload(BaseModel):
    schema_version: Literal[1] = 1
    candidate_id: str
    patch_sha256: str
    base_commit: str
    source_agent: str
    created_at: str


class SignedCandidateAttestation(BaseModel):
    schema_version: Literal[1] = 1
    algorithm: Literal["Ed25519"] = "Ed25519"
    issuer: str
    key_id: str
    payload: CandidateAttestationPayload
    signature: str

    @model_validator(mode="after")
    def _non_empty_envelope(self) -> "SignedCandidateAttestation":
        if not self.issuer.strip() or not self.key_id.strip() or not self.signature.strip():
            raise ValueError("candidate attestation envelope fields must be non-empty")
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


class CandidateAttestationVerification(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    signer_issuer: str | None = None
    signer_key_id: str | None = None


def sign_candidate_attestation(
    payload: CandidateAttestationPayload,
    *,
    issuer: str,
    key_id: str,
    private_key_path: Path,
) -> SignedCandidateAttestation:
    unsigned = signature_message(
        algorithm="Ed25519",
        issuer=issuer,
        key_id=key_id,
        payload=payload.model_dump(mode="python"),
    )
    return SignedCandidateAttestation(
        issuer=issuer,
        key_id=key_id,
        payload=payload,
        signature=sign_ed25519(unsigned, private_key_path),
    )


def verify_candidate_attestation(
    paths: AgosPaths,
    candidate: CandidatePatch,
    *,
    trusted_signers: list[TrustedSignerConfig],
    trusted_config_path: Path,
) -> CandidateAttestationVerification:
    provenance = candidate.provenance
    if provenance is None or provenance.source != "external_attested":
        return CandidateAttestationVerification(
            passed=False,
            issues=["candidate is not classified as external_attested"],
        )
    if not provenance.attestation_ref:
        return CandidateAttestationVerification(
            passed=False,
            issues=["external_attested candidate is missing attestation_ref"],
        )
    try:
        path = _task_ref_path(paths, provenance.attestation_ref)
        envelope = SignedCandidateAttestation.model_validate_json(path.read_text(encoding="utf-8"))
        public_key_path = trusted_public_key_path(
            trusted_signers,
            issuer=envelope.issuer,
            key_id=envelope.key_id,
            trusted_config_path=trusted_config_path,
        )
        verify_ed25519(envelope.signing_bytes(), envelope.signature, public_key_path)
    except Exception as exc:
        return CandidateAttestationVerification(passed=False, issues=[str(exc)])

    expected = {
        "candidate_id": candidate.id,
        "patch_sha256": candidate.patch_sha256,
        "base_commit": candidate.base_commit,
        "source_agent": candidate.source_agent,
    }
    issues = [
        f"candidate attestation {field} mismatch"
        for field, value in expected.items()
        if getattr(envelope.payload, field) != value
    ]
    return CandidateAttestationVerification(
        passed=not issues,
        issues=issues,
        signer_issuer=envelope.issuer,
        signer_key_id=envelope.key_id,
    )


def _task_ref_path(paths: AgosPaths, ref: str) -> Path:
    relative = PurePosixPath(ref)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"invalid candidate attestation_ref: {ref}")
    current_task = paths.current_task.resolve()
    resolved = paths.current_task.joinpath(*relative.parts).resolve()
    if not resolved.is_relative_to(current_task):
        raise ValueError(f"candidate attestation_ref escapes current task: {ref}")
    return resolved
