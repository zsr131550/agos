"""Compile and run review flows through orchestration backends."""
from __future__ import annotations

from dataclasses import dataclass

from agos.backends.native_async import BackendRunHandle
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.registry import OrchestrationRegistry
from agos.core.repo import AgosPaths
from agos.core.review_service import ReviewService
from agos.core.task import load_task
from ulid import ULID


@dataclass(frozen=True)
class ReviewRun:
    """Started or resumed review orchestration run."""

    backend: str
    kind: str
    run_id: str
    review_id: str
    packet_ref: str
    reviewers: list[str]
    spec: OrchestrationRunSpec
    handle: BackendRunHandle


class ReviewOrchestrator:
    """Compile review requests into persisted orchestration runs."""

    def __init__(self, paths: AgosPaths, *, registry: OrchestrationRegistry) -> None:
        self.paths = paths
        self.registry = registry
        self.review_service = ReviewService(paths)

    def build_spec(
        self,
        *,
        review_id: str,
        packet_ref: str,
        reviewers: list[str],
        diff_kind: str,
    ) -> OrchestrationRunSpec:
        task = load_task(self.paths.task_yaml)
        nodes = tuple(
            NodeSpec(
                id=f"reviewer-{reviewer}",
                kind="wait_for_manual_input",
                backend="native_async",
                metadata={
                    "review_id": review_id,
                    "packet_ref": packet_ref,
                    "reviewer": reviewer,
                },
            )
            for reviewer in reviewers
        )
        return OrchestrationRunSpec(
            run_id=_new_run_id(),
            task_id=task.id,
            nodes=nodes,
            metadata={
                "kind": "review_run",
                "review_id": review_id,
                "packet_ref": packet_ref,
                "diff_kind": diff_kind,
                "reviewers": ",".join(reviewers),
            },
        )

    def start_manual_review(self, *, diff_kind: str, reviewers: list[str]) -> ReviewRun:
        reviewers = _validated_reviewers(reviewers)

        packet_ref, packet = self.review_service.start_manual_review_packet(diff_kind=diff_kind)
        spec = self.build_spec(
            review_id=packet.review_id,
            packet_ref=packet_ref,
            reviewers=reviewers,
            diff_kind=diff_kind,
        )
        handle = self.registry.resolve_orchestration("native_async").run(spec)
        self._save_run_spec(spec)
        return self._review_run_from_spec(spec, handle)

    def resume_manual_review(self, run_id: str) -> ReviewRun:
        spec = self._load_run_spec(run_id)
        handle = self.registry.resolve_orchestration("native_async").run(spec)
        return self._review_run_from_spec(spec, handle)

    def _save_run_spec(self, spec: OrchestrationRunSpec) -> None:
        path = self.paths.orchestration_runs / f"{spec.run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")

    def _load_run_spec(self, run_id: str) -> OrchestrationRunSpec:
        path = self.paths.orchestration_runs / f"{run_id}.json"
        return OrchestrationRunSpec.model_validate_json(path.read_text(encoding="utf-8"))

    def _review_run_from_spec(self, spec: OrchestrationRunSpec, handle: BackendRunHandle) -> ReviewRun:
        reviewers = [node.metadata["reviewer"] for node in spec.nodes]
        return ReviewRun(
            backend=handle.backend,
            kind=spec.metadata["kind"],
            run_id=spec.run_id,
            review_id=spec.metadata["review_id"],
            packet_ref=spec.metadata["packet_ref"],
            reviewers=reviewers,
            spec=spec,
            handle=handle,
        )


def _new_run_id() -> str:
    return f"review-run-{ULID()}"


def _validated_reviewers(reviewers: list[str]) -> list[str]:
    if not reviewers:
        raise ValueError("at least one reviewer is required")

    normalized: list[str] = []
    seen: set[str] = set()
    for reviewer in reviewers:
        if not reviewer:
            raise ValueError("reviewer names must be non-empty")
        if reviewer != reviewer.strip():
            raise ValueError("reviewer names must not contain leading or trailing whitespace")
        if reviewer in seen:
            raise ValueError(f"duplicate reviewer: {reviewer}")
        normalized.append(reviewer)
        seen.add(reviewer)
    return normalized
