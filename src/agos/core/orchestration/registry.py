"""Registries for orchestration backend seams."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

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
        self._register(
            backend,
            self.orchestration_backends,
            "orchestration backend",
            OrchestrationBackend,
            "run",
        )

    def register_worker(self, backend: WorkerBackend) -> None:
        self._register(
            backend,
            self.worker_backends,
            "worker backend",
            WorkerBackend,
            "start",
        )

    def register_reviewer(self, backend: ReviewerBackend) -> None:
        self._register(
            backend,
            self.reviewer_backends,
            "reviewer backend",
            ReviewerBackend,
            "start",
        )

    def register_arbiter(self, backend: ArbiterBackend) -> None:
        self._register(
            backend,
            self.arbiter_backends,
            "arbiter backend",
            ArbiterBackend,
            "start",
        )

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

    def _register(
        self,
        backend: object,
        registry: dict[str, object],
        kind: str,
        protocol: type[object],
        entrypoint: Literal["run", "start"],
    ) -> None:
        name = self._validated_name(backend, kind)
        if not self._has_callable_entrypoint(backend, entrypoint):
            raise RegistryResolutionError(f"invalid {kind}: {name}")
        if not isinstance(backend, protocol):
            raise RegistryResolutionError(f"invalid {kind}: {name}")
        if name in registry:
            raise RegistryResolutionError(f"duplicate {kind}: {name}")
        registry[name] = backend

    def _validated_name(self, backend: object, kind: str) -> str:
        name = getattr(backend, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise RegistryResolutionError(f"invalid {kind}: missing name")
        if name != name.strip():
            raise RegistryResolutionError(f"invalid {kind}: whitespace-padded name")
        return name

    def _has_callable_entrypoint(self, backend: object, entrypoint: Literal["run", "start"]) -> bool:
        return callable(getattr(backend, entrypoint, None))
