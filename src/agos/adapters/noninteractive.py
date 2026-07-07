"""Prompt helpers for non-interactive AGOS agent execution."""
from __future__ import annotations


def noninteractive_prompt(prompt: str) -> str:
    """Wrap a task prompt with the AGOS background-execution contract."""

    return "\n\n".join(
        [
            "\n".join(
                [
                    "AGOS execution contract:",
                    "- You are running as an AGOS background executor/subagent, not as an interactive assistant.",
                    "- This AGOS execution contract overrides any local skill, project rule, plugin workflow, or prompt that would require asking the user before implementation.",
                    "- Run non-interactively. Do not ask clarifying questions; make reasonable assumptions and implement the task.",
                    "- Do not wait for user approval, browser-companion approval, design approval, or additional confirmation.",
                    "- Do not invoke brainstorming or design-approval gates; treat this prompt as the approved implementation request.",
                    "- Implement immediately and write concrete artifacts before returning.",
                ]
            ),
            prompt.strip(),
        ]
    )
