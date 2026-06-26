"""Build configured reviewer adapters at the CLI boundary."""
from __future__ import annotations

from agos.adapters.reviewers import (
    FakeReviewerAdapter,
    LlmCliReviewerAdapter,
    ManualReviewerAdapter,
)
from agos.core.config import load_config
from agos.core.repo import repo_paths
from agos.core.review_adapter import ReviewerAdapter
from agos.core.review_orchestrator import ReviewerSpec
from agos.core.review_store import ReviewStore

# Reviewer types backed by a local `codex`/`claude` CLI subprocess.
_LLM_CLI_REVIEWER_TYPES = ("codex_cli", "claude_code")


def configured_reviewer_adapters(repo_root) -> dict[str, ReviewerAdapter]:
    config = load_config(repo_root)
    review_store = ReviewStore(repo_paths(repo_root))
    adapters: dict[str, ReviewerAdapter] = {}
    for name, reviewer in config.reviewers.items():
        if reviewer.type == "manual":
            adapters[name] = ManualReviewerAdapter(name=name)
        elif reviewer.type == "fake":
            if not config.allow_fake_reviewer:
                raise ValueError(
                    "fake reviewer is not allowed in production config; "
                    "set allow_fake_reviewer: true for local development only"
                )
            adapters[name] = FakeReviewerAdapter(name=name, review_store=review_store)
        elif reviewer.type in _LLM_CLI_REVIEWER_TYPES:
            adapters[name] = LlmCliReviewerAdapter(
                name=name,
                executor=reviewer.executor or reviewer.type,
                command=reviewer.command,
                role=reviewer.role,
                timeout_seconds=reviewer.timeout_seconds,
                blocking_severity=reviewer.blocking_severity,
                review_store=review_store,
            )
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
