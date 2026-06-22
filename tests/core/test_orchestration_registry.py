from __future__ import annotations

from dataclasses import dataclass

import pytest

from agos.adapters.reviewers.manual import ManualReviewerAdapter
from agos.backends.native_async import BackendRunHandle, NativeAsyncBackend
from agos.core.orchestration.models import AgentJobHandle, NodeRunStatus, NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.protocols import (
    ArbiterBackend,
    OrchestrationBackend,
    ReviewerBackend,
    WorkerBackend,
)
from agos.core.orchestration.registry import OrchestrationRegistry, RegistryResolutionError



class _NodeLifecycleMixin:
    def poll(self, handle: AgentJobHandle) -> NodeRunStatus:
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state="completed",
        )

    def cancel(self, handle: AgentJobHandle) -> NodeRunStatus:
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state="cancelled",
        )

    def collect(self, handle: AgentJobHandle) -> dict[str, str]:
        return {"run_id": handle.run_id, "node_id": handle.node_id}

@dataclass(frozen=True)
class _FakeWorkerBackend(_NodeLifecycleMixin):
    name: str

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        return AgentJobHandle(
            backend=self.name,
            job_id=f"job-{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )


@dataclass(frozen=True)
class _FakeReviewerBackend(_NodeLifecycleMixin):
    name: str

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        return AgentJobHandle(
            backend=self.name,
            job_id=f"job-{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )


@dataclass(frozen=True)
class _FakeArbiterBackend(_NodeLifecycleMixin):
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

    def start(self, spec: OrchestrationRunSpec) -> BackendRunHandle:
        return BackendRunHandle(backend=self.name, run_id=spec.run_id)

    def poll(self, handle: BackendRunHandle):
        return {"run_id": handle.run_id, "state": "running"}

    def cancel(self, handle: BackendRunHandle):
        return {"run_id": handle.run_id, "state": "cancelled"}

    def collect(self, handle: BackendRunHandle) -> dict[str, str]:
        return {"run_id": handle.run_id}

    def run(self, spec: OrchestrationRunSpec) -> dict[str, str]:
        return {"run_id": spec.run_id}


@dataclass(frozen=True)
class _MalformedBackend:
    name: str


@dataclass(frozen=True)
class _NamelessWorkerBackend:
    start: object


@dataclass(frozen=True)
class _WrongNameTypeWorkerBackend(_NodeLifecycleMixin):
    name: object

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        return AgentJobHandle(
            backend="wrong-name-type",
            job_id=f"job-{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )


@dataclass(frozen=True)
class _NonCallableWorkerStartBackend:
    name: str
    start: object


@dataclass(frozen=True)
class _NonCallableOrchestrationRunBackend:
    name: str
    run: object


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


def test_register_native_async_backend_allows_resolution_through_orchestration_seam():
    registry = OrchestrationRegistry()
    backend = NativeAsyncBackend()
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[NodeSpec(id="wait", kind="wait_for_manual_input", backend="native_async")],
    )

    registry.register_orchestration(backend)

    resolved = registry.resolve_orchestration("native_async")

    assert resolved is backend
    assert resolved.run(spec) == BackendRunHandle(backend="native_async", run_id="run-01")


def test_register_manual_reviewer_adapter_allows_resolution_through_reviewer_seam():
    registry = OrchestrationRegistry()
    adapter = ManualReviewerAdapter()
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[NodeSpec(id="wait", kind="wait_for_manual_input", backend="manual")],
    )
    node = spec.nodes[0]

    registry.register_reviewer(adapter)

    resolved = registry.resolve_reviewer("manual")

    assert resolved is adapter
    assert resolved.start(spec, node) == AgentJobHandle(
        backend="manual",
        job_id="run-01:wait",
        node_id="wait",
        run_id="run-01",
    )


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


def test_register_worker_rejects_backend_without_name():
    registry = OrchestrationRegistry()

    with pytest.raises(RegistryResolutionError, match="invalid worker backend: missing name"):
        registry.register_worker(_NamelessWorkerBackend(start=lambda run, node: None))


@pytest.mark.parametrize("name", ["", 123])
def test_register_worker_rejects_empty_or_non_string_name(name: object):
    registry = OrchestrationRegistry()

    with pytest.raises(RegistryResolutionError, match="invalid worker backend: missing name"):
        registry.register_worker(_WrongNameTypeWorkerBackend(name=name))


def test_register_worker_rejects_non_callable_start():
    registry = OrchestrationRegistry()

    with pytest.raises(RegistryResolutionError, match="invalid worker backend: local_worker"):
        registry.register_worker(_NonCallableWorkerStartBackend(name="local_worker", start="not-callable"))


def test_register_orchestration_rejects_non_callable_run():
    registry = OrchestrationRegistry()

    with pytest.raises(RegistryResolutionError, match="invalid orchestration backend: local_orchestration"):
        registry.register_orchestration(
            _NonCallableOrchestrationRunBackend(name="local_orchestration", run="not-callable")
        )


@pytest.mark.parametrize(
    ("register", "backend", "message"),
    [
        ("register_worker", _FakeWorkerBackend(name="local_worker "), "invalid worker backend: whitespace-padded name"),
        (
            "register_orchestration",
            _FakeOrchestrationBackend(name="local_orchestration "),
            "invalid orchestration backend: whitespace-padded name",
        ),
    ],
)
def test_register_rejects_whitespace_padded_names(register: str, backend: object, message: str):
    registry = OrchestrationRegistry()

    with pytest.raises(RegistryResolutionError, match=message):
        getattr(registry, register)(backend)

