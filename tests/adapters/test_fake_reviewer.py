from __future__ import annotations

from agos.adapters.reviewers import FakeReviewerAdapter


def test_fake_reviewer_cancel_missing_run_records_cancelled_status():
    adapter = FakeReviewerAdapter(name="clean")

    cancelled = adapter.cancel("missing-run")
    after_cancel = adapter.poll("missing-run", reviewer_id="clean")

    assert cancelled.state == "cancelled"
    assert cancelled.reviewer_id == "unknown"
    assert after_cancel.state == "cancelled"
