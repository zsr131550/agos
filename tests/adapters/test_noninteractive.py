from __future__ import annotations

from agos.adapters.noninteractive import noninteractive_prompt


def test_noninteractive_prompt_leads_with_concrete_task_before_contract():
    prompt = noninteractive_prompt("Create docs/real-auto-loop.md now.")

    assert prompt.startswith("AGOS task request:\nCreate docs/real-auto-loop.md now.")
    assert prompt.index("Create docs/real-auto-loop.md now.") < prompt.index(
        "AGOS execution contract:"
    )
    assert "Do not ask clarifying questions" in prompt
