"""OpenHands HTTP execution worker adapter."""
from __future__ import annotations

from urllib.request import urlopen

from agos.adapters.workers.artifacts import collect_artifact_refs, merge_output_refs
from agos.adapters.workers.transport import (
    json_http_request,
    metadata_from_payload,
    output_refs_from_payload,
)
from agos.core.execution_worker import (
    WorkerHealth,
    WorkerHealthCheck,
    WorkerRun,
    WorkerRunStatus,
    WorkerStartRequest,
)


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
        self._workspaces_by_run_id: dict[str, str] = {}

    def health(self) -> WorkerHealth:
        try:
            payload = _json_request(
                "GET",
                f"{self.endpoint}/health",
                timeout=self.timeout,
                headers=self._headers(),
            )
        except Exception as exc:
            check = WorkerHealthCheck(name="endpoint_health", state="failed", detail=str(exc))
        else:
            detail = payload.get("status") or payload.get("state") or "ok"
            check = WorkerHealthCheck(name="endpoint_health", state="passed", detail=str(detail))
        return WorkerHealth(
            name=self.name,
            adapter="openhands",
            checks=[check],
            metadata={
                "endpoint": self.endpoint,
                "timeout_seconds": str(self.timeout_seconds),
                "poll_interval_seconds": str(self.poll_interval_seconds),
                "artifact_globs": ",".join(self.artifact_globs),
                "token_configured": str(self.token is not None).lower(),
                "env_keys": ",".join(sorted(self.env)),
            },
        )

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
                "env": dict(self.env),
            },
            timeout=self.timeout,
            headers=self._headers(),
        )
        run_id = str(payload.get("run_id") or payload.get("id") or request.run_id)
        self._subtasks_by_run_id[run_id] = request.subtask_id
        self._workspaces_by_run_id[run_id] = request.workspace_path
        return WorkerRun(
            backend=self.name,
            run_id=run_id,
            subtask_id=request.subtask_id,
            state=_state(payload.get("state"), default="running"),
            metadata=metadata_from_payload(payload),
        )

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus:
        payload = _json_request(
            "GET",
            f"{self.endpoint}/runs/{run_id}",
            timeout=self.timeout,
            headers=self._headers(),
        )
        self._subtasks_by_run_id[run_id] = subtask_id
        return _status(
            self.name,
            run_id,
            subtask_id,
            payload,
            collect_artifact_refs(self._workspaces_by_run_id.get(run_id), self.artifact_globs),
        )

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
        return _status(
            self.name,
            run_id,
            subtask_id,
            payload,
            collect_artifact_refs(self._workspaces_by_run_id.get(run_id), self.artifact_globs),
        )

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
    return json_http_request(
        "OpenHands",
        method,
        url,
        payload=payload,
        timeout=timeout,
        headers=headers,
        opener=urlopen,
    )


def _state(value: object, *, default: str) -> str:
    return STATE_MAP.get(str(value or default), default)


def _status(
    backend: str,
    run_id: str,
    subtask_id: str,
    payload: dict[str, object],
    artifact_refs: list[str] | None = None,
) -> WorkerRunStatus:
    return WorkerRunStatus(
        backend=backend,
        run_id=run_id,
        subtask_id=subtask_id,
        state=_state(payload.get("state"), default="running"),
        detail=str(payload["detail"]) if payload.get("detail") is not None else None,
        output_refs=merge_output_refs(output_refs_from_payload(payload), artifact_refs or []),
    )

