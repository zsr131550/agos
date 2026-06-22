"""Built-in reviewer adapters."""
from agos.adapters.reviewers.fake import FakeReviewerAdapter
from agos.adapters.reviewers.manual import ManualReviewRequest, ManualReviewerAdapter

__all__ = ["FakeReviewerAdapter", "ManualReviewRequest", "ManualReviewerAdapter"]
