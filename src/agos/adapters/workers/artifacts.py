"""Artifact collection helpers for worker adapters."""
from __future__ import annotations

from pathlib import Path


def collect_artifact_refs(workspace_path: str | None, artifact_globs: tuple[str, ...]) -> list[str]:
    if not workspace_path or not artifact_globs:
        return []
    workspace = Path(workspace_path)
    refs: list[str] = []
    for pattern in artifact_globs:
        for path in sorted(workspace.glob(pattern)):
            if not path.is_file():
                continue
            try:
                refs.append(path.relative_to(workspace).as_posix())
            except ValueError:
                refs.append(str(path))
    return refs


def merge_output_refs(primary: list[str], extra: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for ref in [*primary, *extra]:
        if ref in seen:
            continue
        seen.add(ref)
        merged.append(ref)
    return merged
