from __future__ import annotations

import importlib
import json
from pathlib import Path

from agos.core import execution
from agos.core.config import TrustedSignerConfig
from agos.core.repo import repo_paths


PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIJ1hsZ3v/VpguoRK9JLsLMREScVpezJpGXA7rAMcrn9g
-----END PRIVATE KEY-----
"""
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=
-----END PUBLIC KEY-----
"""


def _legacy_candidate_payload() -> dict[str, object]:
    return {
        "id": "candidate-legacy",
        "task_id": "agos-legacy",
        "subtask_id": "subtask-legacy",
        "source_agent": "local_worktree",
        "workspace_ref": "execution/workspaces/subtask-legacy.json",
        "patch_ref": "evidence/candidate_patches/candidate-legacy.patch",
        "patch_sha256": "a" * 64,
        "base_commit": "b" * 40,
        "summary": "Legacy candidate without provenance metadata.",
    }


def test_candidate_without_provenance_metadata_is_legacy_unattested():
    candidate_model = getattr(execution, "CandidatePatch")
    assert getattr(execution, "CandidateProvenance", None) is not None
    provenance = importlib.import_module("agos.core.provenance")
    candidate = candidate_model.model_validate(_legacy_candidate_payload())

    assert candidate.provenance is None
    assert provenance.candidate_provenance_source(candidate) == "legacy_unattested"
    assert "provenance" not in candidate.model_dump(exclude_unset=True)


def test_explicit_candidate_provenance_round_trips_without_changing_legacy_fields():
    candidate_model = getattr(execution, "CandidatePatch")
    candidate_provenance_model = getattr(execution, "CandidateProvenance", None)
    assert candidate_provenance_model is not None
    provenance = importlib.import_module("agos.core.provenance")
    candidate = candidate_model.model_validate(
        {
            **_legacy_candidate_payload(),
            "provenance": {
                "source": "worker_export",
                "ledger_head_hash": "c" * 64,
            },
        }
    )

    assert candidate.provenance == candidate_provenance_model(
        source="worker_export",
        ledger_head_hash="c" * 64,
    )
    assert provenance.candidate_provenance_source(candidate) == "worker_export"


def _attestation_signer_files(tmp_repo: Path) -> tuple[Path, Path, TrustedSignerConfig]:
    private_key_path = tmp_repo.parent / "external-private.pem"
    private_key_path.write_text(PRIVATE_KEY_PEM, encoding="ascii")
    trusted_config_path = tmp_repo.parent / "trusted" / ".agos" / "agos.yaml"
    public_key_path = trusted_config_path.parent / "keys" / "external-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_text(PUBLIC_KEY_PEM, encoding="ascii")
    trusted_config_path.write_text("merge_gate: {}\n", encoding="utf-8")
    signer = TrustedSignerConfig(
        issuer="external-worker",
        key_id="worker-2026",
        public_key_path="keys/external-public.pem",
    )
    return private_key_path, trusted_config_path, signer


def _external_candidate(attestation_ref: str):
    return execution.CandidatePatch.model_validate(
        {
            **_legacy_candidate_payload(),
            "id": "candidate-external",
            "provenance": {
                "source": "external_attested",
                "attestation_ref": attestation_ref,
            },
        }
    )


def test_external_candidate_attestation_verifies_all_bound_fields(tmp_repo: Path):
    provenance = importlib.import_module("agos.core.provenance")
    assert getattr(provenance, "CandidateAttestationPayload", None) is not None
    private_key_path, trusted_config_path, signer = _attestation_signer_files(tmp_repo)
    paths = repo_paths(tmp_repo)
    attestation_ref = "evidence/external-attestation.json"
    candidate = _external_candidate(attestation_ref)
    payload = provenance.CandidateAttestationPayload(
        candidate_id=candidate.id,
        patch_sha256=candidate.patch_sha256,
        base_commit=candidate.base_commit,
        source_agent=candidate.source_agent,
        created_at="2026-07-14T00:00:00Z",
    )
    envelope = provenance.sign_candidate_attestation(
        payload,
        issuer=signer.issuer,
        key_id=signer.key_id,
        private_key_path=private_key_path,
    )
    path = paths.current_task / attestation_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(envelope.canonical_json() + "\n", encoding="utf-8")

    verification = provenance.verify_candidate_attestation(
        paths,
        candidate,
        trusted_signers=[signer],
        trusted_config_path=trusted_config_path,
    )

    assert verification.passed is True
    assert verification.signer_issuer == signer.issuer
    assert verification.signer_key_id == signer.key_id


def test_external_candidate_attestation_rejects_candidate_field_drift(tmp_repo: Path):
    provenance = importlib.import_module("agos.core.provenance")
    assert getattr(provenance, "CandidateAttestationPayload", None) is not None
    private_key_path, trusted_config_path, signer = _attestation_signer_files(tmp_repo)
    paths = repo_paths(tmp_repo)
    attestation_ref = "evidence/external-attestation.json"
    candidate = _external_candidate(attestation_ref)
    payload = provenance.CandidateAttestationPayload(
        candidate_id=candidate.id,
        patch_sha256="f" * 64,
        base_commit=candidate.base_commit,
        source_agent=candidate.source_agent,
        created_at="2026-07-14T00:00:00Z",
    )
    envelope = provenance.sign_candidate_attestation(
        payload,
        issuer=signer.issuer,
        key_id=signer.key_id,
        private_key_path=private_key_path,
    )
    path = paths.current_task / attestation_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope.model_dump(mode="json")), encoding="utf-8")

    verification = provenance.verify_candidate_attestation(
        paths,
        candidate,
        trusted_signers=[signer],
        trusted_config_path=trusted_config_path,
    )

    assert verification.passed is False
    assert any("patch_sha256 mismatch" in issue for issue in verification.issues)


def test_external_candidate_attestation_rejects_symlink_outside_task(tmp_repo: Path):
    provenance = importlib.import_module("agos.core.provenance")
    private_key_path, trusted_config_path, signer = _attestation_signer_files(tmp_repo)
    paths = repo_paths(tmp_repo)
    attestation_ref = "evidence/external-attestation.json"
    candidate = _external_candidate(attestation_ref)
    payload = provenance.CandidateAttestationPayload(
        candidate_id=candidate.id,
        patch_sha256=candidate.patch_sha256,
        base_commit=candidate.base_commit,
        source_agent=candidate.source_agent,
        created_at="2026-07-14T00:00:00Z",
    )
    envelope = provenance.sign_candidate_attestation(
        payload,
        issuer=signer.issuer,
        key_id=signer.key_id,
        private_key_path=private_key_path,
    )
    outside = tmp_repo.parent / "outside-attestation.json"
    outside.write_text(envelope.canonical_json(), encoding="utf-8")
    path = paths.current_task / attestation_ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)

    verification = provenance.verify_candidate_attestation(
        paths,
        candidate,
        trusted_signers=[signer],
        trusted_config_path=trusted_config_path,
    )

    assert verification.passed is False
    assert any("escapes current task" in issue for issue in verification.issues)
