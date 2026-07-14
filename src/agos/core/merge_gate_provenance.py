"""Cryptographic provenance policy evaluation for merge-gate."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agos.core.config import ProvenancePolicy, TrustedSignerConfig
from agos.core.execution import CandidatePatch
from agos.core.provenance import (
    candidate_provenance_source,
    verify_candidate_attestation,
)
from agos.core.repo import AgosPaths
from agos.core.trust_anchor import TrustAnchorVerification


ProvenanceState = Literal["proven", "unprovenanced", "disabled"]


class ProvenanceEvaluation(BaseModel):
    state: ProvenanceState
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    reconstructed_candidate_ids: list[str] = Field(default_factory=list)
    governed_candidate_ids: list[str] = Field(default_factory=list)


def evaluate_candidate_provenance(
    paths: AgosPaths,
    candidates: list[CandidatePatch],
    records: list[dict],
    *,
    policy: ProvenancePolicy,
    trusted_signers: list[TrustedSignerConfig],
    trusted_config_path: Path,
    signed_anchor_verification: TrustAnchorVerification | None,
) -> ProvenanceEvaluation:
    issues: list[str] = []
    warnings: list[str] = []
    reconstructed: list[str] = []
    governed: list[str] = []
    sources: dict[str, str] = {}
    created_by_candidate = _candidate_creation_records(records)

    for candidate in candidates:
        source = candidate_provenance_source(candidate)
        sources[candidate.id] = source
        if source == "ci_reconstructed":
            reconstructed.append(candidate.id)
        else:
            governed.append(candidate.id)

        provenance = candidate.provenance
        created = created_by_candidate.get(candidate.id, [])
        if provenance is None:
            if len(created) == 1 and created[0].get("provenance_source") is not None:
                issues.append(
                    f"{candidate.id}: provenance metadata is missing but creation ledger declares it"
                )
        else:
            if len(created) != 1:
                issues.append(
                    f"{candidate.id}: provenance requires one candidate_patch_created record"
                )
            elif provenance.ledger_head_hash != created[0].get("hash"):
                issues.append(
                    f"{candidate.id}: provenance creation ledger hash does not match candidate_patch_created"
                )
            else:
                expected_creation_fields = {
                    "provenance_source": source,
                    "source_agent": candidate.source_agent,
                    "workspace_ref": candidate.workspace_ref,
                    "base_commit": candidate.base_commit,
                    "attestation_ref": provenance.attestation_ref,
                }
                for field, expected in expected_creation_fields.items():
                    if created[0].get(field) != expected:
                        issues.append(
                            f"{candidate.id}: provenance {field} does not match creation ledger"
                        )

        if source == "external_attested":
            verification = verify_candidate_attestation(
                paths,
                candidate,
                trusted_signers=trusted_signers,
                trusted_config_path=trusted_config_path,
            )
            issues.extend(f"{candidate.id}: {issue}" for issue in verification.issues)

    signed_valid = False
    if signed_anchor_verification is not None:
        if signed_anchor_verification.passed and signed_anchor_verification.signed:
            signed_valid = True
        else:
            issues.extend(
                f"signed anchor: {issue}" for issue in signed_anchor_verification.issues
            )
            if not signed_anchor_verification.issues:
                issues.append("signed anchor did not provide valid Ed25519 provenance")

    if policy == "required":
        if not candidates:
            issues.append("required provenance has no candidate evidence")
        for candidate_id, source in sources.items():
            if source not in {"worker_export", "external_attested"}:
                issues.append(
                    f"{candidate_id}: {source} cannot satisfy required provenance"
                )
        if signed_anchor_verification is None:
            issues.append("required provenance needs an allowed valid signed anchor")
        elif not signed_valid and not signed_anchor_verification.issues:
            issues.append("required provenance needs an allowed valid signed anchor")
        state: ProvenanceState = "proven" if not issues and signed_valid else "unprovenanced"
    elif policy == "disabled":
        state = "disabled"
    else:
        can_be_proven = bool(candidates) and all(
            source in {"worker_export", "external_attested"} for source in sources.values()
        )
        state = "proven" if not issues and can_be_proven and signed_valid else "unprovenanced"
        if state == "unprovenanced":
            if any(source == "legacy_unattested" for source in sources.values()):
                warnings.append("legacy candidate evidence is unprovenanced")
            elif candidates and signed_anchor_verification is None:
                warnings.append("candidate provenance is not covered by an allowed signed anchor")

    return ProvenanceEvaluation(
        state=state,
        issues=issues,
        warnings=warnings,
        reconstructed_candidate_ids=reconstructed,
        governed_candidate_ids=governed,
    )


def _candidate_creation_records(records: list[dict]) -> dict[str, list[dict]]:
    by_candidate: dict[str, list[dict]] = {}
    for record in records:
        if record.get("type") != "candidate_patch_created" or not record.get("candidate_id"):
            continue
        by_candidate.setdefault(str(record["candidate_id"]), []).append(record)
    return by_candidate
