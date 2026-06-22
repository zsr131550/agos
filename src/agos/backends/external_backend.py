"""External orchestration backend shim."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from urllib.request import Request, urlopen

from agos.core.orchestration.models import (
    NodeSpec,
    OrchestrationRunSpec,
    OrchestratorRunStatus,
)
from agos.core.orchestration.scheduler import runnable_nodes


@dataclass(frozen=True)
class ExternalRunHandle:
    """Handle returned after a spec is submitted to an external orchestrator."""

    backend: str
    run_id: str
    job_id: str
    payload: dict[str, object]


class ExternalBackend:
    """Serialize orchestration specs for an external runtime."""

    name = "external"

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        token: str | None = None,
        timeout: int = 30,
        request_json=None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/") if endpoint else None
        self.token = token
        self.timeout = timeout
        self._request_json = request_json or _json_request
        self._submitted: dict[str, dict[str, object]] = {}

    def start(self, spec: OrchestrationRunSpec) -> ExternalRunHandle:
        if self.endpoint is not None:
            response = self._request_json(
                "POST",
                f"{self.endpoint}/runs",
                payload=spec.model_dump(mode="json"),
                timeout=self.timeout,
                headers=self._headers(),
            )
            run_id = str(response.get("run_id") or spec.run_id)
            return ExternalRunHandle(
                backend=self.name,
                run_id=run_id,
                job_id=str(response.get("job_id") or run_id),
                payload=deepcopy(response),
            )

        payload = self._submission_payload(spec)
        self._submitted[spec.run_id] = deepcopy(payload)
        return ExternalRunHandle(
            backend=self.name,
            run_id=spec.run_id,
            job_id=spec.run_id,
            payload=deepcopy(payload),
        )

    def run(self, spec: OrchestrationRunSpec) -> ExternalRunHandle:
        return self.start(spec)

    def poll(self, handle: ExternalRunHandle) -> OrchestratorRunStatus:
        if self.endpoint is not None:
            snapshot = self._request_json(
                "GET",
                f"{self.endpoint}/runs/{handle.run_id}",
                timeout=self.timeout,
                headers=self._headers(),
            )
            return _status_from_payload(self.name, handle.run_id, snapshot)

        snapshot = self.collect(handle)
        return _status_from_payload(self.name, handle.run_id, snapshot)

    def cancel(self, handle: ExternalRunHandle) -> OrchestratorRunStatus:
        if self.endpoint is not None:
            snapshot = self._request_json(
                "POST",
                f"{self.endpoint}/runs/{handle.run_id}/cancel",
                timeout=self.timeout,
                headers=self._headers(),
            )
            return _status_from_payload(self.name, handle.run_id, snapshot)

        payload = self.collect(handle)
        payload["state"] = "cancelled"
        self._submitted[handle.run_id] = deepcopy(payload)
        return OrchestratorRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            state="cancelled",
        )

    def collect(self, handle: ExternalRunHandle) -> dict[str, object]:
        if self.endpoint is not None:
            status = self._request_json(
                "GET",
                f"{self.endpoint}/runs/{handle.run_id}",
                timeout=self.timeout,
                headers=self._headers(),
            )
            artifacts = self._request_json(
                "GET",
                f"{self.endpoint}/runs/{handle.run_id}/artifacts",
                timeout=self.timeout,
                headers=self._headers(),
            )
            _state(status.get("state"))
            return {
                "run_id": str(status.get("run_id") or handle.run_id),
                "backend": self.name,
                "state": _state(status.get("state")),
                "waiting_nodes": _string_list(status.get("waiting_nodes", [])),
                "completed_nodes": _string_list(status.get("completed_nodes", [])),
                "failed_nodes": _string_list(status.get("failed_nodes", [])),
                "output_refs": _string_dict(artifacts.get("output_refs", {})),
            }

        try:
            return deepcopy(self._submitted[handle.run_id])
        except KeyError as exc:
            raise ValueError(f"unknown orchestration run handle: {handle.run_id}") from exc

    def _headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    def _submission_payload(self, spec: OrchestrationRunSpec) -> dict[str, object]:
        ready_nodes = runnable_nodes(spec.nodes, {})
        waiting_nodes = [
            node_id
            for node_id in ready_nodes
            if _node_by_id(spec, node_id).kind == "wait_for_manual_input"
        ]
        output_refs = _output_refs_for_nodes(spec, waiting_nodes)
        return {
            "run_id": spec.run_id,
            "backend": self.name,
            "state": "submitted",
            "waiting_nodes": waiting_nodes,
            "completed_nodes": [],
            "failed_nodes": [],
            "output_refs": output_refs,
            "spec": spec.model_dump(mode="json"),
        }


def _node_by_id(spec: OrchestrationRunSpec, node_id: str) -> NodeSpec:
    for node in spec.nodes:
        if node.id == node_id:
            return node
    raise ValueError(f"unknown node in orchestration run: {node_id}")


def _output_refs_for_nodes(
    spec: OrchestrationRunSpec,
    node_ids: list[str],
) -> dict[str, str]:
    output_refs: dict[str, str] = {}
    for node_id in node_ids:
        output_ref = _node_by_id(spec, node_id).metadata.get("output_ref")
        if output_ref:
            output_refs[node_id] = output_ref
    return output_refs


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
        raise RuntimeError("external orchestrator returned non-object JSON")
    return loaded


def _status_from_payload(
    backend: str,
    run_id: str,
    payload: dict[str, object],
) -> OrchestratorRunStatus:
    return OrchestratorRunStatus(
        backend=backend,
        run_id=str(payload.get("run_id") or run_id),
        state=_state(payload.get("state")),
        waiting_nodes=tuple(_string_list(payload.get("waiting_nodes", []))),
        completed_nodes=tuple(_string_list(payload.get("completed_nodes", []))),
        failed_nodes=tuple(_string_list(payload.get("failed_nodes", []))),
        output_refs=_string_dict(payload.get("output_refs", {})),
    )


def _state(value: object) -> str:
    state = str(value or "queued")
    normalized = {
        "queued": "queued",
        "submitted": "queued",
        "running": "running",
        "waiting": "waiting",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(state)
    if normalized is None:
        raise ValueError(f"unsupported external run state: {state!r}")
    return normalized


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value]


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}
