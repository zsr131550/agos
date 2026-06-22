from __future__ import annotations

from dataclasses import dataclass

import pytest

from agos.core.orchestration.models import AgentJobHandle, NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.protocols import (
    ArbiterBackend,
    OrchestrationBackend,
    ReviewerBackend,
    WorkerBackend,
)
from agos.core.orchestration.registry import OrchestrationRegistry, RegistryResolutionError


@dataclass(frozen=True)
class _FakeWorkerBackend:
    name: str

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        return AgentJobHandle(
            backend=self.name,
            job_id=f"job-{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )


@dataclass(frozen=True)
class _FakeReviewerBackend:
    name: str

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        return AgentJobHandle(
            backend=self.name,
            job_id=f"job-{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )


@dataclass(frozen=True)
class _FakeArbiterBackend:
    name: str

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        return AgentJobHandle(
            backend=self.name,
            job_id=f"job-{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )


@dataclass(frozen=True)
class _FakeOrchestrationBackend:
    name: str

    def run(self, spec: OrchestrationRunSpec) -> dict[str, str]:
        return {"run_id": spec.run_id}


@dataclass(frozen=True)
class _MalformedBackend:
    name: str


def test_missing_backend_lookup_raises_registry_resolution_error():
    registry = OrchestrationRegistry()

    with pytest.raises(RegistryResolutionError, match="missing worker backend: local_worker"):
        registry.resolve_worker("local_worker")


def test_register_worker_allows_resolution_by_name():
    registry = OrchestrationRegistry()
    backend: WorkerBackend = _FakeWorkerBackend(name="local_worker")

    registry.register_worker(backend)

    assert registry.resolve_worker("local_worker") is backend


def test_register_worker_rejects_duplicate_backend_names():
    registry = OrchestrationRegistry()
    registry.register_worker(_FakeWorkerBackend(name="local_worker"))

    with pytest.raises(RegistryResolutionError, match="duplicate worker backend: local_worker"):
        registry.register_worker(_FakeWorkerBackend(name="local_worker"))


def test_register_all_backend_types_allow_resolution_by_name():
    registry = OrchestrationRegistry()
    orchestration: OrchestrationBackend = _FakeOrchestrationBackend(name="local_orchestration")
    worker: WorkerBackend = _FakeWorkerBackend(name="local_worker")
    reviewer: ReviewerBackend = _FakeReviewerBackend(name="local_reviewer")
    arbiter: ArbiterBackend = _FakeArbiterBackend(name="local_arbiter")

    registry.register_orchestration(orchestration)
    registry.register_worker(worker)
    registry.register_reviewer(reviewer)
    registry.register_arbiter(arbiter)

    assert registry.resolve_orchestration("local_orchestration") is orchestration
    assert registry.resolve_worker("local_worker") is worker
    assert registry.resolve_reviewer("local_reviewer") is reviewer
    assert registry.resolve_arbiter("local_arbiter") is arbiter


@pytest.mark.parametrize(
    ("register", "backend", "message"),
    [
        ("register_orchestration", _MalformedBackend(name="local_orchestration"), "invalid orchestration backend: local_orchestration"),
        ("register_worker", _MalformedBackend(name="local_worker"), "invalid worker backend: local_worker"),
        ("register_reviewer", _MalformedBackend(name="local_reviewer"), "invalid reviewer backend: local_reviewer"),
        ("register_arbiter", _MalformedBackend(name="local_arbiter"), "invalid arbiter backend: local_arbiter"),
    ],
)
def test_register_rejects_malformed_backends(register: str, backend: _MalformedBackend, message: str):
    registry = OrchestrationRegistry()

    with pytest.raises(RegistryResolutionError, match=message):
        getattr(registry, register)(backend)
