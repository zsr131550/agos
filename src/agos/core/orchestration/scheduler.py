"""Helpers for selecting runnable orchestration nodes."""
from __future__ import annotations

from collections.abc import Mapping

from agos.core.orchestration.models import NodeSpec
from agos.core.orchestration.runtime import PersistedNodeState


_BLOCKING_STATES = {"waiting", "running", "failed"}


def runnable_nodes(
    nodes: tuple[NodeSpec, ...] | list[NodeSpec],
    states: Mapping[str, PersistedNodeState],
) -> tuple[str, ...]:
    """Return node ids that are ready to run in spec order."""

    ready: list[str] = []
    for node in nodes:
        current = states.get(node.id)
        if current is not None:
            if current.state in _BLOCKING_STATES or current.state == "completed":
                continue

        if any(states.get(dep) is None or states[dep].state != "completed" for dep in node.depends_on):
            continue

        ready.append(node.id)
    return tuple(ready)
