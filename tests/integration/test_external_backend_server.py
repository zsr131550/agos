from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from agos.backends.external_backend import ExternalBackend
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


class Handler(BaseHTTPRequestHandler):
    runs: dict[str, dict[str, object]] = {}
    received_payloads: list[dict[str, object]] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if self.path == "/runs":
            self.received_payloads.append(body)
            spec = body["spec"]
            run_id = spec["run_id"]
            assert body["schema_version"] == "agos.orchestration.v1"
            assert body["idempotency_key"] == run_id
            self.runs[run_id] = {"run_id": run_id, "state": "running", "completed_nodes": []}
            self._json({"run_id": run_id, "job_id": f"job-{run_id}", "state": "running"})
            return
        if self.path.endswith("/cancel"):
            run_id = self.path.split("/")[2]
            self.runs[run_id]["state"] = "cancelled"
            self._json({"run_id": run_id, "state": "cancelled"})
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        if self.path.endswith("/artifacts"):
            run_id = self.path.split("/")[2]
            self._json({"run_id": run_id, "output_refs": {"worker-01": "remote/worker.json"}})
            return
        run_id = self.path.split("/")[2]
        self._json({"run_id": run_id, "state": "completed", "completed_nodes": ["worker-01"]})

    def log_message(self, format, *args):
        return

    def _json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_external_backend_talks_to_real_http_server():
    Handler.runs = {}
    Handler.received_payloads = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_address[1]}"
        backend = ExternalBackend(endpoint=endpoint, token="secret")
        spec = OrchestrationRunSpec(
            run_id="external-run-01",
            task_id="agos-01",
            nodes=(NodeSpec(id="worker-01", kind="worker", backend="external"),),
        )

        handle = backend.start(spec)
        status = backend.poll(handle)
        artifacts = backend.collect(handle)

        assert handle.job_id == "job-external-run-01"
        assert status.state == "completed"
        assert artifacts["output_refs"]["worker-01"] == "remote/worker.json"
        assert Handler.received_payloads[0]["spec"]["run_id"] == "external-run-01"
    finally:
        server.shutdown()
        thread.join(timeout=5)
