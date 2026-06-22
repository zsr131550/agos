"""Persisted orchestration specs and lightweight runtime handles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


NodeKind = Literal["worker", "reviewer", "arbiter"]


@dataclass(frozen=True)
class AgentJobHandle:
    """Handle returned when orchestration dispatches backend work."""

    backend: str
    job_id: str
    node_id: str
    run_id: str


class NodeSpec(BaseModel):
    """One node in an orchestration run graph."""

    id: str
    kind: NodeKind
    backend: str
    depends_on: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("id", "backend")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("node id and backend must be non-empty")
        return value

    @field_validator("depends_on")
    @classmethod
    def _unique_dependencies(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("depends_on entries must be unique")
        return value


class OrchestrationRunSpec(BaseModel):
    """Serialized orchestration plan for a single task run."""

    run_id: str
    task_id: str
    nodes: list[NodeSpec]
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("run_id", "task_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("run_id and task_id must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_nodes(self) -> "OrchestrationRunSpec":
        if not self.nodes:
            raise ValueError("orchestration run requires at least one node")

        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("node ids must be unique")

        node_ids = set(ids)
        for node in self.nodes:
            for dependency in node.depends_on:
                if dependency not in node_ids:
                    raise ValueError(f"unknown dependency {dependency!r} for node {node.id!r}")
                if dependency == node.id:
                    raise ValueError(f"node {node.id!r} cannot depend on itself")
        return self
