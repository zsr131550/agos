"""Parallel review adapter scheduler."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from pydantic import BaseModel, Field

from agos.core.review import Finding, ReviewPacket
from agos.core.review_adapter import ReviewerAdapter, ReviewerRunStatus, ReviewerStartRequest


class ReviewerSpec(BaseModel):
    id: str
    role: str
    adapter: str
    required: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class ReviewRunResult:
    run_id: str
    state: str
    findings: tuple[Finding, ...] = ()
    failed_reviewers: tuple[str, ...] = ()
    # Refs to each reviewer's raw output (carries provenance such as dev_only).
    raw_refs: tuple[str, ...] = ()


class ParallelReviewOrchestrator:
    """Run independent reviewer adapters and normalize their terminal result."""

    def __init__(self, reviewers: dict[str, ReviewerAdapter]) -> None:
        self._reviewers = dict(reviewers)

    def run(
        self,
        *,
        run_id: str,
        packet: ReviewPacket,
        reviewers: list[ReviewerSpec],
        max_parallel: int = 4,
    ) -> ReviewRunResult:
        max_workers = max(1, min(max_parallel, len(reviewers) or 1))
        statuses: dict[str, ReviewerRunStatus] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._run_one, run_id, packet, spec): spec
                for spec in reviewers
            }
            for future in as_completed(futures):
                spec = futures[future]
                statuses[spec.id] = future.result()

        findings: list[Finding] = []
        failed: list[str] = []
        raw_refs: list[str] = []
        for spec in reviewers:
            status = statuses[spec.id]
            if status.raw_ref is not None:
                raw_refs.append(status.raw_ref)
            if status.state == "completed":
                findings.extend(status.findings)
            elif spec.required:
                failed.append(spec.id)

        return ReviewRunResult(
            run_id=run_id,
            state="failed" if failed else "completed",
            findings=tuple(findings),
            failed_reviewers=tuple(failed),
            raw_refs=tuple(raw_refs),
        )

    def _run_one(
        self,
        run_id: str,
        packet: ReviewPacket,
        spec: ReviewerSpec,
    ) -> ReviewerRunStatus:
        try:
            adapter = self._reviewers[spec.adapter]
        except KeyError as exc:
            raise ValueError(f"unsupported reviewer adapter: {spec.adapter}") from exc
        run = adapter.start(
            ReviewerStartRequest(
                run_id=run_id,
                reviewer_id=spec.id,
                role=spec.role,
                packet=packet,
                metadata=spec.metadata,
            )
        )
        return adapter.poll(run.run_id, reviewer_id=spec.id)
