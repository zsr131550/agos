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
            unwrapped = _unwrap_cli_result_envelope(payload)
            if unwrapped is not None:
                return unwrapped
            return payload
    return None


def _unwrap_cli_result_envelope(payload: dict[str, object]) -> dict[str, object] | None:
    """Unwrap a CLI result envelope to the JSON object inside.

    claude's ``--output-format json`` wraps the model's text in a result
    envelope: ``{"type":"result","result":"<model text>", ...}``. The model text
    is itself JSON (sometimes markdown-fenced). Without unwrapping, callers see
    the envelope and miss the real payload (e.g. the ``findings`` array). Recurse
    into ``result`` so the shared parser yields the inner object for both the
    reviewer and the planner. Returns None when this is not such an envelope or
    the inner text is not JSON, so the caller falls back to the original payload.
    """
    if payload.get("type") != "result" or not isinstance(payload.get("result"), str):
        return None
    return load_json_object_from_text(payload["result"])


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
