"""Fake worker adapter for tests."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeWorkerHandle:
    subtask_id: str
    workspace_path: str
    patch_bytes: bytes = b""


class FakeWorkerAdapter:
    name = "fake"

    def __init__(self, patch_bytes: bytes = b"") -> None:
        self.patch_bytes = patch_bytes

    def prepare(self, assignment):
        return assignment

    def export_candidate(self, handle):
        patch_bytes = getattr(handle, "patch_bytes", self.patch_bytes)
        return {"patch_bytes": patch_bytes}
