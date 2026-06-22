"""Persisted orchestration specs and lightweight runtime handles."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


NodeKind = Literal[
    "worker",
    "reviewer",
    "arbiter",
    "wait_for_manual_input",
    "validate_plan",
    "prepare_workspace",
    "worker_submit",
    "candidate_review_subgraph",
]


@dataclass(frozen=True)
class AgentJobHandle:
    """Handle returned when orchestration dispatches backend work."""

    backend: str
    job_id: str
    node_id: str
    run_id: str


OrchestratorRunState = Literal[
    "queued",
    "running",
    "waiting",
    "completed",
    "failed",
    "cancelled",
]


@dataclass(frozen=True)
class OrchestratorRunHandle:
    """Handle returned when an orchestration backend starts a run."""

    backend: str
    run_id: str
    job_id: str | None = None


@dataclass(frozen=True)
class OrchestratorRunStatus:
    """Normalized orchestration backend status snapshot."""

    backend: str
    run_id: str
    state: OrchestratorRunState
    waiting_nodes: tuple[str, ...] = ()
    completed_nodes: tuple[str, ...] = ()
    failed_nodes: tuple[str, ...] = ()
    output_refs: dict[str, str] | None = None


class NodeSpec(BaseModel):
    """One node in an orchestration run graph."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: NodeKind
    backend: str
    adapter: str | None = None
    depends_on: tuple[str, ...] = Field(default_factory=tuple)
    inputs: Mapping[str, str] = Field(default_factory=lambda: MappingProxyType({}))
    policy: Mapping[str, str] = Field(default_factory=lambda: MappingProxyType({}))
    metadata: Mapping[str, str] = Field(default_factory=lambda: MappingProxyType({}))

    @field_validator("id", "backend")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("node id and backend must be non-empty")
        if value != value.strip():
            raise ValueError("node id and backend must not contain leading or trailing whitespace")
        return value

    @field_validator("depends_on")
    @classmethod
    def _unique_dependencies(cls, value: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        value = tuple(value)
        if any(entry != entry.strip() for entry in value):
            raise ValueError("depends_on entries must not contain leading or trailing whitespace")
        if len(set(value)) != len(value):
            raise ValueError("depends_on entries must be unique")
        return value

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if isinstance(value, MappingProxyType):
            return value
        return MappingProxyType(dict(value))

    @field_validator("inputs", "policy")
    @classmethod
    def _freeze_inputs_and_policy(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        if isinstance(value, MappingProxyType):
            return value
        return MappingProxyType(dict(value))

    @field_serializer("inputs", "policy")
    def _serialize_inputs_and_policy(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)


class OrchestrationRunSpec(BaseModel):
    """Serialized orchestration plan for a single task run."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    task_id: str
    kind: str = "orchestration_run"
    backend: str = "native_async"
    entry_nodes: tuple[str, ...] = Field(default_factory=tuple)
    nodes: tuple[NodeSpec, ...]
    limits: Mapping[str, int] = Field(default_factory=lambda: MappingProxyType({}))
    artifacts: Mapping[str, str] = Field(default_factory=lambda: MappingProxyType({}))
    metadata: Mapping[str, str] = Field(default_factory=lambda: MappingProxyType({}))

    @field_validator("run_id", "task_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("run_id and task_id must be non-empty")
        if value != value.strip():
            raise ValueError("run_id and task_id must not contain leading or trailing whitespace")
        return value

    @field_validator("nodes")
    @classmethod
    def _freeze_nodes(cls, value: tuple[NodeSpec, ...] | list[NodeSpec]) -> tuple[NodeSpec, ...]:
        return tuple(value)

    @field_validator("entry_nodes")
    @classmethod
    def _freeze_entry_nodes(cls, value: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        value = tuple(value)
        if any(entry != entry.strip() for entry in value):
            raise ValueError("entry_nodes entries must not contain leading or trailing whitespace")
        if len(set(value)) != len(value):
            raise ValueError("entry_nodes entries must be unique")
        return value

    @field_validator("metadata", "limits", "artifacts")
    @classmethod
    def _freeze_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        if isinstance(value, MappingProxyType):
            return value
        return MappingProxyType(dict(value))

    @field_serializer("metadata", "limits", "artifacts")
    def _serialize_metadata(self, value: Mapping[str, object]) -> dict[str, object]:
        return dict(value)

    @model_validator(mode="after")
    def _validate_nodes(self) -> "OrchestrationRunSpec":
        if not self.nodes:
            raise ValueError("orchestration run requires at least one node")

        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("node ids must be unique")

        if self.entry_nodes:
            node_ids = set(ids)
            for entry_node in self.entry_nodes:
                if entry_node not in node_ids:
                    raise ValueError(f"unknown entry node {entry_node!r}")

        node_ids = set(ids)
        for node in self.nodes:
            for dependency in node.depends_on:
                if dependency not in node_ids:
                    raise ValueError(f"unknown dependency {dependency!r} for node {node.id!r}")
                if dependency == node.id:
                    raise ValueError(f"node {node.id!r} cannot depend on itself")
        self._validate_dag()
        return self

    def _validate_dag(self) -> None:
        by_id = {node.id: node for node in self.nodes}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                raise ValueError(f"dependency cycle detected at node {node_id!r}")

            visiting.add(node_id)
            for dependency_id in by_id[node_id].depends_on:
                visit(dependency_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for node in self.nodes:
            visit(node.id)
