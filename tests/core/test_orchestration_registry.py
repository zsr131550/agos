from __future__ import annotations

from dataclasses import dataclass

import pytest

from agos.core.orchestration.registry import OrchestrationRegistry, RegistryResolutionError


@dataclass(frozen=True)
class _FakeWorkerBackend:
    name: str


def test_missing_backend_lookup_raises_registry_resolution_error():
    registry = OrchestrationRegistry()

    with pytest.raises(RegistryResolutionError, match="missing worker backend: local_worker"):
        registry.resolve_worker("local_worker")


def test_register_worker_allows_resolution_by_name():
    registry = OrchestrationRegistry()
    backend = _FakeWorkerBackend(name="local_worker")

    registry.register_worker(backend)

    assert registry.resolve_worker("local_worker") is backend


def test_register_worker_rejects_duplicate_backend_names():
    registry = OrchestrationRegistry()
    registry.register_worker(_FakeWorkerBackend(name="local_worker"))

    with pytest.raises(RegistryResolutionError, match="duplicate worker backend: local_worker"):
        registry.register_worker(_FakeWorkerBackend(name="local_worker"))
