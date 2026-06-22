from __future__ import annotations

import pytest

from agos.backends.external_backend import ExternalBackend
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


def test_external_backend_submits_polls_cancels_and_collects():
    calls: list[tuple[str, str, object | None]] = []

    def fake_request(method, url, payload=None, timeout=30, headers=None):
        del timeout
        calls.append((method, url, payload))
        assert headers == {"Authorization": "Bearer secret"}
        if method == "POST" and url.endswith("/runs"):
            return {"run_id": "remote-01", "job_id": "job-01", "state": "running"}
        if method == "GET" and url.endswith("/runs/remote-01"):
            return {
                "run_id": "remote-01",
                "state": "completed",
                "completed_nodes": ["worker-01"],
            }
        if method == "POST" and url.endswith("/runs/remote-01/cancel"):
            return {"run_id": "remote-01", "state": "cancelled"}
        if method == "GET" and url.endswith("/runs/remote-01/artifacts"):
            return {"output_refs": {"worker-01": "remote/artifacts/worker.log"}}
        raise AssertionError((method, url))

    backend = ExternalBackend(
        endpoint="http://orchestrator.local",
        token="secret",
        request_json=fake_request,
    )

    handle = backend.start(_spec())
    status = backend.poll(handle)
    snapshot = backend.collect(handle)
    cancelled = backend.cancel(handle)

    assert handle.run_id == "remote-01"
    assert handle.job_id == "job-01"
    assert status.state == "completed"
    assert snapshot["output_refs"] == {"worker-01": "remote/artifacts/worker.log"}
    assert cancelled.state == "cancelled"
    assert calls[0][0] == "POST"
    assert calls[0][1] == "http://orchestrator.local/runs"


def test_external_backend_rejects_unknown_remote_state():
    def fake_request(method, url, payload=None, timeout=30, headers=None):
        del payload, timeout, headers
        if method == "POST":
            return {"run_id": "remote-01", "job_id": "job-01", "state": "running"}
        return {"run_id": "remote-01", "state": "mystery"}

    backend = ExternalBackend(endpoint="http://orchestrator.local", request_json=fake_request)
    handle = backend.start(_spec())

    with pytest.raises(ValueError, match="unsupported external run state"):
        backend.poll(handle)


def _spec() -> OrchestrationRunSpec:
    return OrchestrationRunSpec(
        run_id="external-run-01",
        task_id="agos-01",
        nodes=[NodeSpec(id="worker-01", kind="worker", backend="external")],
    )
