from __future__ import annotations

import importlib

from agos.core import execution


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
