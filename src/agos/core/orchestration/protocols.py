"""Protocol seams for orchestration backends."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agos.core.orchestration.models import (
    AgentJobHandle,
    NodeSpec,
    OrchestrationRunSpec,
    OrchestratorRunHandle,
    OrchestratorRunStatus,
)


@runtime_checkable
class WorkerBackend(Protocol):
    """Backend capable of running worker nodes."""

    name: str

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle: ...


@runtime_checkable
class ReviewerBackend(Protocol):
    """Backend capable of running reviewer nodes."""

    name: str

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle: ...


@runtime_checkable
class ArbiterBackend(Protocol):
    """Backend capable of running arbiter nodes."""

    name: str

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle: ...


@runtime_checkable
class OrchestrationBackend(Protocol):
    """Backend capable of coordinating a whole orchestration run."""

    name: str

    def start(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle: ...

    def poll(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus: ...

    def cancel(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus: ...

    def collect(self, handle: OrchestratorRunHandle) -> dict[str, Any]: ...

    def run(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle: ...
