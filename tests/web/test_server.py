from __future__ import annotations

from contextlib import contextmanager
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import agos.web.server as dashboard_server
from agos.web.server import DashboardHTTPServer, create_dashboard_server


@contextmanager
def running_dashboard_server(tmp_repo) -> Iterator[DashboardHTTPServer]:
    server = create_dashboard_server(tmp_repo, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def read_json_error(url: str) -> tuple[int, dict[str, object]]:
    try:
        urllib.request.urlopen(url, timeout=5)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)
    raise AssertionError("expected HTTPError")


def test_dashboard_server_serves_static_index(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8")
        assert "AGOS 控制台" in body


def test_dashboard_server_serves_health_json(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/health"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["service"] == "agos-dashboard"


def test_dashboard_server_unknown_api_returns_404_json(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/nope"
        status, payload = read_json_error(url)
    assert status == 404
    assert payload == {"ok": False, "error": {"code": "not_found", "message": "/api/nope"}}


def test_dashboard_server_api_business_error_returns_400_json(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/config"
        status, payload = read_json_error(url)
    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_initialized"


def test_dashboard_server_internal_error_is_redacted(tmp_repo, monkeypatch) -> None:
    def broken_health(repo_root):
        raise RuntimeError("secret path C:/Users/ZR/private")

    monkeypatch.setitem(dashboard_server._API_ROUTES, "/api/health", broken_health)
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/health"
        status, payload = read_json_error(url)
    body = json.dumps(payload, ensure_ascii=False)
    assert status == 500
    assert payload == {
        "ok": False,
        "error": {"code": "internal_error", "message": "Internal dashboard server error"},
    }
    assert "secret path" not in body
    assert "C:/Users" not in body
