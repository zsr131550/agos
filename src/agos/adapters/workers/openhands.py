"""OpenHands HTTP execution worker adapter."""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

from agos.core.execution_worker import WorkerRun, WorkerRunStatus, WorkerStartRequest


STATE_MAP = {
    "queued": "queued",
    "pending": "queued",
    "running": "running",
    "in_progress": "running",
    "done": "completed",
    "completed": "completed",
    "blocked": "blocked",
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
}


class OpenHandsWorkerAdapter:
    """Submit AGOS worker runs to an OpenHands-compatible HTTP endpoint."""

    def __init__(
        self,
        *,
        endpoint: str,
        token: str | None = None,
        name: str = "openhands",
        timeout: int = 30,
        timeout_seconds: int | None = None,
        poll_interval_seconds: int = 1,
        artifact_globs: tuple[str, ...] | list[str] = (),
        env: dict[str, str] | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.name = name
        self.timeout_seconds = timeout if timeout_seconds is None else timeout_seconds
        self.timeout = self.timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.artifact_globs = tuple(artifact_globs)
        self.env = dict(env or {})
        self._subtasks_by_run_id: dict[str, str] = {}

    def start(self, request: WorkerStartRequest) -> WorkerRun:
        payload = _json_request(
            "POST",
            f"{self.endpoint}/runs",
            payload={
                "run_id": request.run_id,
                "subtask_id": request.subtask_id,
                "prompt": request.prompt,
                "workspace_path": request.workspace_path,
                "metadata": request.metadata,
            },
            timeout=self.timeout,
            headers=self._headers(),
        )
        run_id = str(payload.get("run_id") or payload.get("id") or request.run_id)
        self._subtasks_by_run_id[run_id] = request.subtask_id
        return WorkerRun(
            backend=self.name,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state=_state(payload.get("state"), default="running"),
            metadata=_metadata(payload),
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        payload = _json_request(
            "GET",
            f"{self.endpoint}/runs/{run_id}",
            timeout=self.timeout,
            headers=self._headers(),
        )
        self._subtasks_by_run_id[run_id] = subtask_id
        return _status(self.name, run_id, subtask_id, payload)

    def cancel(self, run_id: str) -> WorkerRunStatus:
        payload = _json_request(
            "POST",
            f"{self.endpoint}/runs/{run_id}/cancel",
            timeout=self.timeout,
            headers=self._headers(),
        )
        subtask_id = self._subtasks_by_run_id.get(run_id, str(payload.get("subtask_id", "unknown")))
        if "state" not in payload:
            payload["state"] = "cancelled"
        return _status(self.name, run_id, subtask_id, payload)

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}


def _json_request(
    method: str,
    url: str,
    payload=None,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=timeout) as response:
        data = response.read().decode("utf-8")
    if not data.strip():
        return {}
    loaded = json.loads(data)
    if not isinstance(loaded, dict):
        raise RuntimeError("OpenHands endpoint returned non-object JSON")
    return loaded


def _state(value: object, *, default: str) -> str:
    return STATE_MAP.get(str(value or default), default)


def _status(
    backend: str,
    run_id: str,
    subtask_id: str,
    payload: dict[str, object],
) -> WorkerRunStatus:
    output_refs = payload.get("output_refs", [])
    if not isinstance(output_refs, list):
        output_refs = []
    return WorkerRunStatus(
        backend=backend,
        run_id=run_id,
        subtask_id=subtask_id,
        state=_state(payload.get("state"), default="running"),
        detail=str(payload["detail"]) if payload.get("detail") is not None else None,
        output_refs=[str(ref) for ref in output_refs],
    )


def _metadata(payload: dict[str, object]) -> dict[str, str]:
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    return {str(key): str(value) for key, value in metadata.items()}

