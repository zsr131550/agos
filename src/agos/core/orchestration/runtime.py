"""Persistence helpers for lightweight orchestration runtime state."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class PersistedNodeState(BaseModel):
    """Serialized state for one orchestration node."""

    node_id: str
    state: str
    attempts: int = 0
    backend: str | None = None
    job_id: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    output_refs: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


def save_node_state(path: Path, state: PersistedNodeState) -> None:
    """Persist a node state snapshot as JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")


def load_node_state(path: Path) -> PersistedNodeState:
    """Load a node state snapshot from JSON."""

    return PersistedNodeState.model_validate_json(path.read_text(encoding="utf-8"))
