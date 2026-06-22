"""Registries for orchestration backend seams."""
from __future__ import annotations

from dataclasses import dataclass, field

from agos.core.orchestration.protocols import (
    ArbiterBackend,
    OrchestrationBackend,
    ReviewerBackend,
    WorkerBackend,
)


class RegistryResolutionError(LookupError):
    """Raised when a requested backend has not been registered."""


@dataclass
class OrchestrationRegistry:
    """In-memory registry for orchestration backend implementations."""

    orchestration_backends: dict[str, OrchestrationBackend] = field(default_factory=dict)
    worker_backends: dict[str, WorkerBackend] = field(default_factory=dict)
    reviewer_backends: dict[str, ReviewerBackend] = field(default_factory=dict)
    arbiter_backends: dict[str, ArbiterBackend] = field(default_factory=dict)

    def register_orchestration(self, backend: OrchestrationBackend) -> None:
        self.orchestration_backends[backend.name] = backend

    def register_worker(self, backend: WorkerBackend) -> None:
        self.worker_backends[backend.name] = backend

    def register_reviewer(self, backend: ReviewerBackend) -> None:
        self.reviewer_backends[backend.name] = backend

    def register_arbiter(self, backend: ArbiterBackend) -> None:
        self.arbiter_backends[backend.name] = backend

    def resolve_orchestration(self, name: str) -> OrchestrationBackend:
        return self._resolve(name, self.orchestration_backends, "orchestration backend")

    def resolve_worker(self, name: str) -> WorkerBackend:
        return self._resolve(name, self.worker_backends, "worker backend")

    def resolve_reviewer(self, name: str) -> ReviewerBackend:
        return self._resolve(name, self.reviewer_backends, "reviewer backend")

    def resolve_arbiter(self, name: str) -> ArbiterBackend:
        return self._resolve(name, self.arbiter_backends, "arbiter backend")

    def _resolve(self, name: str, registry: dict[str, object], kind: str):
        try:
            return registry[name]
        except KeyError as exc:
            raise RegistryResolutionError(f"missing {kind}: {name}") from exc
