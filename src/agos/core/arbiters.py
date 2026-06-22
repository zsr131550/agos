"""Deterministic review arbitration helpers."""
from __future__ import annotations

from collections.abc import Iterable

from agos.core.review import Finding


class DeterministicReviewArbiter:
    """Rule-based arbiter that produces stable ordering for review findings."""

    name = "deterministic_review"

    def arbitrate(self, findings: Iterable[Finding]) -> list[Finding]:
        return sorted(findings, key=_finding_sort_key)


def _finding_sort_key(finding: Finding) -> tuple[int, str]:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return (order[finding.severity], finding.id)
