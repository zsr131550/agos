from __future__ import annotations

from agos.core.execution import CandidatePatch, CandidateProvenance
from agos.core.merge_gate_provenance import evaluate_candidate_provenance
from agos.core.repo import repo_paths
from agos.core.trust_anchor import TrustAnchorVerification


def _candidate(provenance: CandidateProvenance | None = None) -> CandidatePatch:
    return CandidatePatch(
        id="candidate-01",
        task_id="agos-task-01",
        subtask_id="subtask-01",
        source_agent="local_worktree",
        workspace_ref="execution/workspaces/subtask-01.json",
        patch_ref="evidence/candidate_patches/candidate-01.patch",
        patch_sha256="a" * 64,
        base_commit="b" * 40,
        summary="Candidate provenance evaluator fixture.",
        status="accepted",
        provenance=provenance,
    )


def _evaluate(tmp_repo, candidates, records, *, policy, signed=None):
    paths = repo_paths(tmp_repo)
    return evaluate_candidate_provenance(
        paths,
        candidates,
        records,
        policy=policy,
        trusted_signers=[],
        trusted_config_path=paths.agos_yaml,
        signed_anchor_verification=signed,
    )


def test_required_provenance_needs_candidate_and_signed_anchor(tmp_repo):
    result = _evaluate(tmp_repo, [], [], policy="required")

    assert result.state == "unprovenanced"
    assert "no candidate evidence" in "; ".join(result.issues)
    assert "signed anchor" in "; ".join(result.issues)


def test_disabled_provenance_makes_no_claim(tmp_repo):
    result = _evaluate(tmp_repo, [], [], policy="disabled")

    assert result.state == "disabled"
    assert result.issues == []


def test_optional_legacy_candidate_is_reported_unprovenanced(tmp_repo):
    result = _evaluate(tmp_repo, [_candidate()], [], policy="optional")

    assert result.state == "unprovenanced"
    assert result.issues == []
    assert "legacy candidate evidence" in "; ".join(result.warnings)


def test_explicit_provenance_requires_creation_record(tmp_repo):
    result = _evaluate(
        tmp_repo,
        [
            _candidate(
                CandidateProvenance(
                    source="worker_export",
                    ledger_head_hash="c" * 64,
                )
            )
        ],
        [],
        policy="optional",
    )

    assert "requires one candidate_patch_created" in "; ".join(result.issues)


def test_valid_signed_coverage_marks_worker_export_proven(tmp_repo):
    candidate = _candidate(
        CandidateProvenance(
            source="worker_export",
            ledger_head_hash="c" * 64,
        )
    )
    records = [
        {
            "type": "candidate_patch_created",
            "candidate_id": candidate.id,
            "hash": "c" * 64,
            "provenance_source": "worker_export",
            "source_agent": candidate.source_agent,
            "workspace_ref": candidate.workspace_ref,
            "base_commit": candidate.base_commit,
            "attestation_ref": None,
        }
    ]

    result = _evaluate(
        tmp_repo,
        [candidate],
        records,
        policy="optional",
        signed=TrustAnchorVerification(
            task_id=candidate.task_id,
            passed=True,
            signed=True,
        ),
    )

    assert result.state == "proven"
    assert result.issues == []
