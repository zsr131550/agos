from __future__ import annotations

import os

import pytest

from agos.adapters.reviewers import LlmCliReviewerAdapter
from agos.core.review import ReviewPacket
from agos.core.review_adapter import ReviewerStartRequest


def _smoke_packet() -> ReviewPacket:
    return ReviewPacket(
        review_id="reviewer-smoke-01",
        task_id="agos-smoke",
        task_title="Smoke review",
        task_intent="Exercise the reviewer CLI end to end.",
        diff_kind="governed_repo_diff",
        ledger_head_hash="0000000000000000000000000000000000000000",
    )


def _smoke_request() -> ReviewerStartRequest:
    return ReviewerStartRequest(
        run_id="reviewer-smoke-run-01",
        reviewer_id="smoke",
        role="security_reviewer",
        packet=_smoke_packet(),
    )


@pytest.mark.skipif(os.getenv("AGOS_REVIEWER_SMOKE") != "1", reason="opt-in real reviewer CLI smoke")
def test_llm_cli_reviewer_runs_real_cli(tmp_path):
    executor = os.getenv("AGOS_REVIEWER_EXECUTOR", "codex_cli")
    adapter = LlmCliReviewerAdapter(
        name="smoke",
        executor=executor,
        command=os.getenv("AGOS_REVIEWER_BIN"),
        role="security_reviewer",
        timeout_seconds=120,
        cwd=tmp_path,
    )

    run = adapter.start(_smoke_request())
    status = adapter.poll(run.run_id, reviewer_id="smoke")

    # Whether the CLI returned findings or failed closed, it must not crash and
    # must reach a terminal state with the run id wired through.
    assert status.is_terminal()
    assert status.run_id == "reviewer-smoke-run-01"
