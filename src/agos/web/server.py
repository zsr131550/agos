"""Local HTTP server for the AGOS dashboard."""
from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlsplit

from agos.web.api import (
    DashboardApiError,
    agents_payload,
    archive_current_task_payload,
    candidates_payload,
    config_payload,
    continue_archived_task_payload,
    current_run_payload,
    error_payload,
    evidence_payload,
    execution_payload,
    health_payload,
    pause_current_task_payload,
    ledger_payload,
    reviews_payload,
    review_run_payload,
    restart_current_task_payload,
    resume_current_task_payload,
    runs_payload,
    select_agent_option_payload,
    start_run_payload,
    status_payload,
)

PayloadBuilder = Callable[[Path], dict[str, object]]
MAX_REQUEST_BODY_BYTES = 64 * 1024


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
    auth_token: str
    remote_binding: bool
    expose_token_in_index: bool

    def __init__(
        self,
        server_address: tuple[str, int],
        repo_root: Path,
        *,
        auth_token: str,
        remote_binding: bool,
        expose_token_in_index: bool,
    ) -> None:
        super().__init__(server_address, DashboardRequestHandler)
        self.repo_root = Path(repo_root)
        self.auth_token = auth_token
        self.remote_binding = remote_binding
        self.expose_token_in_index = expose_token_in_index


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
            if self.server.remote_binding and not self._has_valid_token():
                self._write_access_error(
                    HTTPStatus.UNAUTHORIZED,
                    "unauthorized",
                    "Dashboard API authentication is required",
                )
                return
            self._serve_api(path, parse_qs(parsed.query, keep_blank_values=True))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path.startswith("/api/") and not self._authorize_mutation():
            return
        if parsed.path == "/api/runs":
            self._serve_start_run()
            return
        if parsed.path == "/api/runs/current/archive":
            self._serve_archive_current_task()
            return
        if parsed.path == "/api/runs/current/pause":
            self._serve_simple_post(pause_current_task_payload)
            return
        if parsed.path == "/api/runs/current/resume":
            self._serve_simple_post(resume_current_task_payload)
            return
        if parsed.path == "/api/runs/current/restart":
            self._serve_simple_post(restart_current_task_payload)
            return
        if parsed.path == "/api/runs/current/agent-options/select":
            self._serve_select_agent_option()
            return
        archive_prefix = "/api/runs/archive/"
        if parsed.path.startswith(archive_prefix) and parsed.path.endswith("/continue"):
            archive_id = parsed.path.removeprefix(archive_prefix).removesuffix("/continue")
            self._serve_continue_archived_task(archive_id)
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
        embedded_token = self.server.auth_token if self.server.expose_token_in_index else ""
        text = index.read_text(encoding="utf-8").replace(
            "__AGOS_DASHBOARD_TOKEN__",
            json.dumps(embedded_token),
        )
        body = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
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

    def _serve_archive_current_task(self) -> None:
        try:
            self._read_json_body()
            result = archive_current_task_payload(self.server.repo_root)
            status = HTTPStatus.OK
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

    def _serve_continue_archived_task(self, archive_id: str) -> None:
        try:
            self._read_json_body()
            result = continue_archived_task_payload(self.server.repo_root, archive_id)
            status = HTTPStatus.OK
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

    def _serve_simple_post(self, builder: PayloadBuilder) -> None:
        try:
            self._read_json_body()
            result = builder(self.server.repo_root)
            status = HTTPStatus.OK
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

    def _serve_select_agent_option(self) -> None:
        try:
            payload = self._read_json_body()
            result = select_agent_option_payload(self.server.repo_root, payload)
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
        if content_length > MAX_REQUEST_BODY_BYTES:
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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorize_mutation(self) -> bool:
        if not self._has_valid_token():
            self._write_access_error(
                HTTPStatus.UNAUTHORIZED,
                "unauthorized",
                "Dashboard mutation authentication is required",
            )
            return False
        if not self._has_same_origin():
            self._write_access_error(
                HTTPStatus.FORBIDDEN,
                "origin_forbidden",
                "Dashboard mutation origin is not allowed",
            )
            return False
        return True

    def _has_valid_token(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not authorization.startswith(prefix):
            return False
        supplied = authorization[len(prefix) :]
        return bool(supplied) and hmac.compare_digest(supplied, self.server.auth_token)

    def _has_same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if not origin or not host or origin == "null":
            return False
        try:
            supplied = urlsplit(origin)
            expected = urlsplit(f"http://{host}")
            supplied_port = supplied.port or 80
            expected_port = expected.port or 80
        except ValueError:
            return False
        return (
            supplied.scheme == "http"
            and supplied.username is None
            and supplied.password is None
            and supplied.hostname is not None
            and expected.hostname is not None
            and supplied.hostname.casefold() == expected.hostname.casefold()
            and supplied_port == expected_port
            and supplied.path in {"", "/"}
            and not supplied.query
            and not supplied.fragment
        )

    def _write_access_error(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
    ) -> None:
        self._write_json(
            {"ok": False, "error": {"code": code, "message": message}},
            status=status,
        )


def create_dashboard_server(
    repo_root: Path,
    *,
    host: str,
    port: int,
    token: str | None = None,
) -> DashboardHTTPServer:
    """Create a local dashboard HTTP server without starting it."""

    loopback = _is_loopback_host(host)
    explicit_token = token.strip() if token is not None else ""
    if not loopback and not explicit_token:
        raise ValueError("non-loopback dashboard binding requires an authentication token")
    auth_token = explicit_token or secrets.token_urlsafe(32)
    return DashboardHTTPServer(
        (host, port),
        Path(repo_root),
        auth_token=auth_token,
        remote_binding=not loopback,
        expose_token_in_index=loopback and not explicit_token,
    )


def serve_dashboard_forever(
    repo_root: Path,
    *,
    host: str,
    port: int,
    open_browser: bool,
    token: str | None = None,
) -> str:
    """Serve the AGOS dashboard until interrupted and close the server on exit."""

    server = create_dashboard_server(repo_root, host=host, port=port, token=token)
    open_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{open_host}:{server.server_port}"
    print(f"AGOS dashboard: {url}", flush=True)
    if open_browser:
        browser_url = url
        if not server.expose_token_in_index:
            browser_url = f"{url}/#token={quote(server.auth_token, safe='')}"
        webbrowser.open(browser_url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return url


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
