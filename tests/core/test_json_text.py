from __future__ import annotations

from agos.core.json_text import load_json_object_from_text


def test_load_json_object_from_text_returns_plain_payload_unchanged():
    payload = {"findings": [], "meta": 1}
    assert load_json_object_from_text(json_text(payload)) == payload


def test_load_json_object_from_text_returns_none_for_empty():
    assert load_json_object_from_text("   ") is None


def test_load_json_object_from_text_unwraps_claude_result_envelope():
    # claude's --output-format json wraps the model text in a result envelope.
    inner = '{"findings": [{"id": "f1"}]}'
    envelope = {"type": "result", "subtype": "success", "result": inner, "usage": {}}

    assert load_json_object_from_text(json_text(envelope)) == {
        "findings": [{"id": "f1"}]
    }


def test_load_json_object_from_text_unwraps_envelope_with_markdown_fence():
    # The model often wraps its JSON in a ```json fenced block.
    inner = "```json\n{\"findings\": []}\n```"
    envelope = {"type": "result", "result": inner}

    assert load_json_object_from_text(json_text(envelope)) == {"findings": []}


def test_load_json_object_from_text_unwraps_full_envelope_with_metadata():
    # Real claude output carries usage/session metadata alongside `result`.
    inner = '{"plan": {"steps": []}}'
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": inner,
        "duration_ms": 8004,
        "session_id": "abc",
        "usage": {"input_tokens": 100},
    }

    assert load_json_object_from_text(json_text(envelope)) == {"plan": {"steps": []}}


def test_load_json_object_from_text_returns_envelope_when_inner_unparseable():
    # If the inner `result` is not JSON, fall back to the envelope rather than
    # None (no regression for outputs the caller already handled).
    envelope = {"type": "result", "result": "not json at all"}
    assert load_json_object_from_text(json_text(envelope)) == envelope


def test_load_json_object_from_text_does_not_unwrap_non_result_envelope():
    # A payload that merely has a `result` string but is not a claude result
    # envelope must be returned verbatim.
    payload = {"result": "ok", "findings": []}
    assert load_json_object_from_text(json_text(payload)) == payload


def json_text(obj: object) -> str:
    import json

    return json.dumps(obj)
