"""Safe evidence reference resolution for the web dashboard."""
from __future__ import annotations

from pathlib import Path, PureWindowsPath
from typing import Any

from agos.core.repo import AgosPaths


class EvidenceResolutionError(ValueError):
    """Raised when an evidence reference is invalid or unsafe."""


_TASK_RELATIVE_ROOTS = {
    "ledger.jsonl",
    "status.json",
    "task.yaml",
    "proof.json",
    "proof.md",
    "execution",
    "evidence",
    "reviews",
    "orchestration",
}

_TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".patch",
    ".txt",
    ".yaml",
    ".yml",
}


def resolve_evidence_ref(paths: AgosPaths, ref: str) -> Path:
    """Resolve a dashboard evidence reference without allowing path escapes."""

    parts = _safe_ref_parts(ref)
    root = parts[0]

    if root in _TASK_RELATIVE_ROOTS:
        base = paths.current_task
        candidate = base.joinpath(*parts)
    elif len(parts) > 1:
        base = paths.evidence
        candidate = base.joinpath(*parts)
    else:
        raise EvidenceResolutionError(f"unknown evidence reference root: {root}")

    resolved = candidate.resolve()
    base_resolved = base.resolve()
    if not _is_relative_to(resolved, base_resolved):
        raise EvidenceResolutionError("evidence reference escapes allowed root")
    if not resolved.is_file():
        raise EvidenceResolutionError(f"evidence reference does not exist: {ref}")
    return resolved


def read_evidence_text(
    paths: AgosPaths,
    ref: str,
    max_bytes: int = 262144,
) -> dict[str, Any]:
    """Read a safe text evidence file, truncating after ``max_bytes`` bytes."""

    resolved = resolve_evidence_ref(paths, ref)
    if resolved.suffix.lower() not in _TEXT_SUFFIXES:
        raise EvidenceResolutionError(f"unsupported evidence text suffix: {resolved.suffix}")
    if max_bytes < 0:
        raise EvidenceResolutionError("max_bytes must be non-negative")

    size_bytes = resolved.stat().st_size
    with resolved.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]

    return {
        "ref": ref,
        "path": resolved.relative_to(paths.current_task.resolve()).as_posix(),
        "text": data.decode("utf-8", errors="replace"),
        "truncated": truncated,
        "size_bytes": size_bytes,
    }


def _safe_ref_parts(ref: str) -> tuple[str, ...]:
    if not ref:
        raise EvidenceResolutionError("empty evidence reference")
    if Path(ref).is_absolute() or PureWindowsPath(ref).is_absolute() or PureWindowsPath(ref).drive:
        raise EvidenceResolutionError("absolute evidence references are not allowed")

    normalized = ref.replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part)
    if not parts:
        raise EvidenceResolutionError("empty evidence reference")
    if any(part in {".", ".."} for part in parts):
        raise EvidenceResolutionError("path traversal is not allowed in evidence references")
    return parts


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True
