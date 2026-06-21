"""Task models for `agos start`."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from agos.core.config import GateConfig


class TaskExecutorConfig(BaseModel):
    adapter: str
    agent: str


class Task(BaseModel):
    id: str
    title: str
    intent: str | None = None
    workflow: str
    gates: list[GateConfig] = Field(default_factory=list)
    executor: TaskExecutorConfig

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(self.model_dump(mode="python"), sort_keys=False),
            encoding="utf-8",
        )

