"""Candidate provenance classification and verification helpers."""
from __future__ import annotations

from agos.core.execution import CandidatePatch, CandidateProvenanceSource


def candidate_provenance_source(candidate: CandidatePatch) -> CandidateProvenanceSource:
    """Classify old candidate records without mutating their persisted form."""

    if candidate.provenance is None:
        return "legacy_unattested"
    return candidate.provenance.source
