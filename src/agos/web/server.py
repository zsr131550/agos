"""Local HTTP server for the AGOS dashboard."""
from __future__ import annotations

import json
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from agos.web.api import (
    DashboardApiError,
    agents_payload,
    candidates_payload,
    config_payload,
    current_run_payload,
    error_payload,
    evidence_payload,
    execution_payload,
    health_payload,
    ledger_payload,
    reviews_payload,
    review_run_payload,
    runs_payload,
    start_run_payload,
    status_payload,
)

PayloadBuilder = Callable[[Path], dict[str, object]]


_API_ROUTES: dict[str, PayloadBuilder] = {
    "/api/health": health_payload,
    "/api/agents": agents_payload,
    "/api/config": config_payload,
    "/api/status": status_payload,
    "/api/runs": runs_payload,
    "/api/runs/current": current_run_payload,
    "/api/runs/current/ledger": ledger_payload,
    "/api/runs/current/execution": execution_payload,
    "/api/runs/current/candidates": candidates_payload,
    "/api/runs/current/reviews": reviews_payload,
}


class DashboardHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying the repository root used by dashboard handlers."""

    allow_reuse_address = True
    daemon_threads = True
    repo_root: Path

    def __init__(self, server_address: tuple[str, int], repo_root: Path) -> None:
        super().__init__(server_address, DashboardRequestHandler)
        self.repo_root = Path(repo_root)


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Request handler for dashboard static assets and JSON APIs."""

    server: DashboardHTTPServer
    server_version = "AGOSDashboardHTTP"
    sys_version = ""

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            self._serve_index()
            return
        if path.startswith("/api/"):
            self._serve_api(path, parse_qs(parsed.query, keep_blank_values=True))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/api/runs":
            self._serve_start_run()
            return
        if parsed.path == "/api/reviews/run":
            self._serve_review_run()
            return
        if parsed.path.startswith("/api/"):
            self._write_json(
                {"ok": False, "error": {"code": "not_found", "message": parsed.path}},
                status=HTTPStatus.NOT_FOUND,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Suppress default stderr request logging for the local dashboard."""

    def _serve_index(self) -> None:
        index = resources.files("agos.web").joinpath("static/index.html")
        body = index.read_text(encoding="utf-8").encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_api(self, path: str, query: dict[str, list[str]]) -> None:
        try:
            if path == "/api/runs/current/evidence":
                ref = query.get("ref", [""])[0]
                payload = evidence_payload(self.server.repo_root, ref)
                status = HTTPStatus.OK
            else:
                builder = _API_ROUTES.get(path)
                if builder is None:
                    payload = {"ok": False, "error": {"code": "not_found", "message": path}}
                    status = HTTPStatus.NOT_FOUND
                else:
                    payload = builder(self.server.repo_root)
                    if path == "/api/health":
                        payload = {**payload, "service": "agos-dashboard"}
                    status = HTTPStatus.OK
        except DashboardApiError as exc:
            payload = error_payload(exc)
            status = HTTPStatus.BAD_REQUEST
        except Exception:
            payload = {
                "ok": False,
                "error": {"code": "internal_error", "message": "Internal dashboard server error"},
            }
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._write_json(payload, status=status)

    def _serve_start_run(self) -> None:
        try:
            payload = self._read_json_body()
            result = start_run_payload(self.server.repo_root, payload)
            status = HTTPStatus.CREATED
        except DashboardApiError as exc:
            result = error_payload(exc)
            status = HTTPStatus.BAD_REQUEST
        except Exception:
            result = {
                "ok": False,
                "error": {"code": "internal_error", "message": "Internal dashboard server error"},
            }
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._write_json(result, status=status)

    def _serve_review_run(self) -> None:
        try:
            payload = self._read_json_body()
            result = review_run_payload(self.server.repo_root, payload)
            status = HTTPStatus.CREATED
        except DashboardApiError as exc:
            result = error_payload(exc)
            status = HTTPStatus.BAD_REQUEST
        except Exception:
            result = {
                "ok": False,
                "error": {"code": "internal_error", "message": "Internal dashboard server error"},
            }
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self._write_json(result, status=status)

    def _read_json_body(self) -> dict[str, object]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise DashboardApiError("invalid_request", "invalid Content-Length") from exc
        if content_length <= 0:
            raise DashboardApiError("invalid_request", "JSON body is required")
        if content_length > 64 * 1024:
            raise DashboardApiError("invalid_request", "JSON body is too large")
        raw = self.rfile.read(content_length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DashboardApiError("invalid_request", "invalid JSON body") from exc
        if not isinstance(payload, dict):
            raise DashboardApiError("invalid_request", "JSON body must be an object")
        return payload

    def _write_json(
        self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def create_dashboard_server(repo_root: Path, *, host: str, port: int) -> DashboardHTTPServer:
    """Create a local dashboard HTTP server without starting it."""

    return DashboardHTTPServer((host, port), Path(repo_root))


def serve_dashboard_forever(
    repo_root: Path, *, host: str, port: int, open_browser: bool
) -> str:
    """Serve the AGOS dashboard until interrupted and close the server on exit."""

    server = create_dashboard_server(repo_root, host=host, port=port)
    open_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{open_host}:{server.server_port}"
    print(f"AGOS dashboard: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return url
