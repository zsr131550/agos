"""Tolerant JSON-object extraction from CLI text output.

Shared by core (LLM planner) and adapters (LLM reviewer) so both reuse one
parse strategy. Kept in core so the planner does not need to import adapters.
"""
from __future__ import annotations

import json


def load_json_object_from_text(text: str) -> dict[str, object] | None:
    """Best-effort parse of a JSON object from CLI text output."""

    stripped = text.strip()
    if not stripped:
        return None

    for candidate in _json_candidates(stripped):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _json_candidates(text: str) -> list[str]:
    candidates = [text]
    sliced = _json_object_slice(text)
    if sliced is not None and sliced not in candidates:
        candidates.append(sliced)
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line and line not in candidates:
            candidates.append(line)
    return candidates


def _json_object_slice(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]
