from __future__ import annotations

import os
from pathlib import Path

import pytest

from agos.adapters.workers.codex_cli import CodexWorkerAdapter
from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
from agos.core.execution_worker import WorkerStartRequest


def _request(tmp_path: Path) -> WorkerStartRequest:
    return WorkerStartRequest(
        run_id="agos-smoke-run",
        subtask_id="agos-smoke-subtask",
        prompt="Return a JSON status for an AGOS adapter smoke test.",
        workspace_path=str(tmp_path),
        metadata={"smoke": "true"},
    )


@pytest.mark.skipif(os.getenv("AGOS_CODEX_WORKER_SMOKE") != "1", reason="opt-in real Codex worker smoke")
def test_codex_worker_smoke(tmp_path):
    adapter = CodexWorkerAdapter(
        command=os.getenv("AGOS_CODEX_BIN", "codex"),
        timeout_seconds=120,
    )
    run = adapter.start(_request(tmp_path))
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)
    assert status.backend == adapter.name
    assert status.run_id == run.run_id


@pytest.mark.skipif(os.getenv("AGOS_MULTICA_WORKER_SMOKE") != "1", reason="opt-in real Multica worker smoke")
def test_multica_worker_smoke(tmp_path):
    adapter = MulticaWorkerAdapter(
        multica_bin=os.getenv("AGOS_MULTICA_BIN", "multica"),
        agent=os.getenv("AGOS_MULTICA_AGENT", "Lambda"),
        timeout_seconds=120,
    )
    run = adapter.start(_request(tmp_path))
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)
    assert status.backend == adapter.name
    assert status.run_id == run.run_id


@pytest.mark.skipif(os.getenv("AGOS_OPENHANDS_WORKER_SMOKE") != "1", reason="opt-in real OpenHands worker smoke")
def test_openhands_worker_smoke(tmp_path):
    endpoint = os.environ["AGOS_OPENHANDS_ENDPOINT"]
    adapter = OpenHandsWorkerAdapter(
        endpoint=endpoint,
        token=os.getenv("AGOS_OPENHANDS_TOKEN"),
        timeout=120,
    )
    run = adapter.start(_request(tmp_path))
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)
    assert status.backend == adapter.name
    assert status.run_id == run.run_id
