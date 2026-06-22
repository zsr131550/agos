"""Build configured reviewer adapters at the CLI boundary."""
from __future__ import annotations

from agos.adapters.reviewers import FakeReviewerAdapter, ManualReviewerAdapter
from agos.core.config import load_config
from agos.core.review_adapter import ReviewerAdapter
from agos.core.review_orchestrator import ReviewerSpec


def configured_reviewer_adapters(repo_root) -> dict[str, ReviewerAdapter]:
    config = load_config(repo_root)
    adapters: dict[str, ReviewerAdapter] = {}
    for name, reviewer in config.reviewers.items():
        if reviewer.type == "manual":
            adapters[name] = ManualReviewerAdapter(name=name)
        elif reviewer.type == "fake":
            adapters[name] = FakeReviewerAdapter(name=name)
        else:
            raise ValueError(f"unsupported reviewer adapter type: {reviewer.type}")
    return adapters


def configured_reviewer_specs(repo_root) -> list[ReviewerSpec]:
    config = load_config(repo_root)
    return [
        ReviewerSpec(
            id=name,
            role=reviewer.role,
            adapter=name,
            required=reviewer.required,
        )
        for name, reviewer in config.reviewers.items()
    ]
