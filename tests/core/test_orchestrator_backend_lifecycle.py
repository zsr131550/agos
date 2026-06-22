from __future__ import annotations

from agos.backends.native_async import NativeAsyncBackend
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


def test_native_backend_exposes_start_poll_cancel_collect_lifecycle():
    backend = NativeAsyncBackend()

    handle = backend.start(_spec())
    status = backend.poll(handle)
    cancelled = backend.cancel(handle)
    snapshot = backend.collect(handle)

    assert handle.backend == "native_async"
    assert handle.run_id == "run-01"
    assert status.backend == "native_async"
    assert status.run_id == "run-01"
    assert status.state == "running"
    assert cancelled.state == "cancelled"
    assert snapshot["state"] == "cancelled"


def _spec() -> OrchestrationRunSpec:
    return OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[
            NodeSpec(id="worker-01", kind="worker", backend="fake_worker"),
            NodeSpec(
                id="reviewer-01",
                kind="reviewer",
                backend="fake_reviewer",
                depends_on=["worker-01"],
            ),
        ],
        limits={"max_parallel": 2},
    )
