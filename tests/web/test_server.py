from __future__ import annotations

import json
import threading
import urllib.request

from agos.web.server import create_dashboard_server


def test_dashboard_server_serves_static_index(tmp_repo) -> None:
    server = create_dashboard_server(tmp_repo, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8")
        assert "AGOS 控制台" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_server_serves_health_json(tmp_repo) -> None:
    server = create_dashboard_server(tmp_repo, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/health"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["service"] == "agos-dashboard"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
