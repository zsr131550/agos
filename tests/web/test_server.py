from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from types import SimpleNamespace
from urllib.parse import urlsplit

import agos.web.server as dashboard_server
from agos.web.server import DashboardHTTPServer, create_dashboard_server
import pytest
import yaml


_DEFAULT_HEADER = object()
_SERVER_TOKENS: dict[int, str] = {}


@contextmanager
def running_dashboard_server(
    tmp_repo,
    *,
    host: str = "127.0.0.1",
    token: str | None = None,
) -> Iterator[DashboardHTTPServer]:
    server = create_dashboard_server(tmp_repo, host=host, port=0, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _SERVER_TOKENS[server.server_port] = server.auth_token
    try:
        yield server
    finally:
        _SERVER_TOKENS.pop(server.server_port, None)
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


def _security_headers(
    url: str,
    *,
    token: object = _DEFAULT_HEADER,
    origin: object = _DEFAULT_HEADER,
) -> dict[str, str]:
    parsed = urlsplit(url)
    port = parsed.port or 80
    resolved_token = _SERVER_TOKENS.get(port) if token is _DEFAULT_HEADER else token
    resolved_origin = f"{parsed.scheme}://{parsed.netloc}" if origin is _DEFAULT_HEADER else origin
    headers: dict[str, str] = {}
    if isinstance(resolved_token, str):
        headers["Authorization"] = f"Bearer {resolved_token}"
    if isinstance(resolved_origin, str):
        headers["Origin"] = resolved_origin
    return headers


def post_json(url: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **_security_headers(url)},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body)


def post_raw(
    server: DashboardHTTPServer,
    path: str,
    body: bytes = b"",
    *,
    content_length: str | None = None,
    token: object = _DEFAULT_HEADER,
    origin: object = _DEFAULT_HEADER,
) -> tuple[int, dict[str, object] | str]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Content-Type", "application/json")
        url = f"http://127.0.0.1:{server.server_port}{path}"
        for name, value in _security_headers(url, token=token, origin=origin).items():
            connection.putheader(name, value)
        connection.putheader(
            "Content-Length",
            content_length if content_length is not None else str(len(body)),
        )
        connection.endheaders()
        if body:
            connection.send(body)
        response = connection.getresponse()
        payload = response.read().decode("utf-8")
        try:
            parsed: dict[str, object] | str = json.loads(payload)
        except json.JSONDecodeError:
            parsed = payload
        return response.status, parsed
    finally:
        connection.close()


def read_json_request_error(request: urllib.request.Request) -> tuple[int, dict[str, object]]:
    for name, value in _security_headers(request.full_url).items():
        if not request.has_header(name):
            request.add_header(name, value)
    try:
        urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)
    raise AssertionError("expected HTTPError")


def write_dashboard_config(tmp_repo) -> None:
    agos_dir = tmp_repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "tasks" / "current").mkdir(parents=True)
    config = {
        "executor": {"name": "multica", "agent": "Lambda"},
        "default_workflow": "feature",
        "workers": {
            "codex_local": {"type": "codex_cli", "command": "codex"},
        },
        "reviewers": {
            "tests": {"type": "fake", "role": "test_reviewer"},
        },
        "allow_fake_reviewer": True,
        "workflows": {
            "feature": {
                "gates": [
                    {"id": "tests_pass", "stage": ["pre-commit"], "command": "pytest -q"},
                    {"id": "lint_clean", "stage": ["pre-push"], "command": "ruff check"},
                ]
            },
            "docs_only": {"gates": []},
        },
    }
    (agos_dir / "agos.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def test_dashboard_server_serves_static_index(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8")
        assert "AGOS 控制台" in body


def test_dashboard_server_get_unknown_non_api_returns_404(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/missing", timeout=5)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            status = exc.code
        else:  # pragma: no cover - assertion guard
            raise AssertionError("expected HTTPError")

    assert status == 404
    assert "Error response" in body


def test_dashboard_server_serves_health_json(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/health"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["service"] == "agos-dashboard"


def test_non_loopback_bind_requires_explicit_token(tmp_repo) -> None:
    with pytest.raises(ValueError, match="token"):
        create_dashboard_server(tmp_repo, host="0.0.0.0", port=0)


def test_dashboard_mutation_rejects_missing_and_wrong_token(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        missing_status, missing = post_raw(
            server,
            "/api/not-a-route",
            b"{}",
            token=None,
        )
        wrong_status, wrong = post_raw(
            server,
            "/api/not-a-route",
            b"{}",
            token="wrong-token",
        )

    assert missing_status == 401
    assert wrong_status == 401
    assert missing["error"]["code"] == "unauthorized"
    assert wrong["error"]["code"] == "unauthorized"
    assert server.auth_token not in json.dumps([missing, wrong])


def test_dashboard_mutation_rejects_missing_and_cross_origin(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        missing_status, missing = post_raw(
            server,
            "/api/not-a-route",
            b"{}",
            origin=None,
        )
        cross_status, cross = post_raw(
            server,
            "/api/not-a-route",
            b"{}",
            origin="https://attacker.invalid",
        )

    assert missing_status == 403
    assert cross_status == 403
    assert missing["error"]["code"] == "origin_forbidden"
    assert cross["error"]["code"] == "origin_forbidden"


def test_dashboard_mutation_accepts_valid_token_and_same_origin(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_raw(server, "/api/not-a-route", b"{}")

    assert status == 404
    assert payload == {
        "ok": False,
        "error": {"code": "not_found", "message": "/api/not-a-route"},
    }


def test_remote_dashboard_get_api_requires_token(tmp_repo) -> None:
    with running_dashboard_server(
        tmp_repo,
        host="0.0.0.0",
        token="remote-dashboard-token",
    ) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/health"
        status, payload = read_json_error(url)
        request = urllib.request.Request(
            url,
            headers={"Authorization": "Bearer remote-dashboard-token"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            authorized = json.loads(response.read().decode("utf-8"))

    assert status == 401
    assert payload["error"]["code"] == "unauthorized"
    assert authorized["ok"] is True


def test_loopback_index_bootstraps_generated_token_but_remote_index_does_not(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as local_server:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{local_server.server_port}/",
            timeout=5,
        ) as response:
            local_body = response.read().decode("utf-8")
        assert local_server.auth_token in local_body
        assert "__AGOS_DASHBOARD_TOKEN__" not in local_body

    with running_dashboard_server(
        tmp_repo,
        host="0.0.0.0",
        token="remote-dashboard-token",
    ) as remote_server:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{remote_server.server_port}/",
            timeout=5,
        ) as response:
            remote_body = response.read().decode("utf-8")
        assert "remote-dashboard-token" not in remote_body


def test_dashboard_server_serves_agents_json(tmp_repo, monkeypatch) -> None:
    monkeypatch.setattr(
        "agos.web.api.shutil.which",
        lambda command: "/test/bin/codex" if command == "codex" else None,
    )
    write_dashboard_config(tmp_repo)
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/agents"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

    assert payload["ok"] is True
    task_agent_ids = [agent["id"] for agent in payload["task_agents"]]
    assert task_agent_ids[:2] == ["executor:multica:Lambda", "worker:codex_local"]
    assert "local:codex_cli:codex" in task_agent_ids
    review_agent_ids = [agent["id"] for agent in payload["review_agents"]]
    assert "reviewer:tests" in review_agent_ids
    assert "local:reviewer:codex_cli" in review_agent_ids


def test_dashboard_server_serves_evidence_json(tmp_repo) -> None:
    write_dashboard_config(tmp_repo)
    evidence = tmp_repo / ".agos" / "tasks" / "current" / "evidence" / "gates" / "tests.log"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_bytes(b"ok\n")

    with running_dashboard_server(tmp_repo) as server:
        url = (
            f"http://127.0.0.1:{server.server_port}"
            "/api/runs/current/evidence?ref=evidence/gates/tests.log"
        )
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

    assert payload["ok"] is True
    assert payload["text"] == "ok\n"
    assert payload["ref"] == "evidence/gates/tests.log"


def test_dashboard_server_serves_empty_runs_when_no_active_task(tmp_repo) -> None:
    write_dashboard_config(tmp_repo)
    current = tmp_repo / ".agos" / "tasks" / "current"
    if current.exists():
        import shutil

        shutil.rmtree(current)

    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/runs"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

    assert payload["ok"] is True
    assert payload["current_run_id"] is None
    assert payload["runs"] == []


def test_dashboard_server_unknown_api_returns_404_json(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/nope"
        status, payload = read_json_error(url)
    assert status == 404
    assert payload == {"ok": False, "error": {"code": "not_found", "message": "/api/nope"}}


def test_dashboard_server_post_unknown_api_returns_404_json(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/not-a-route",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 404
    assert payload == {"ok": False, "error": {"code": "not_found", "message": "/api/not-a-route"}}


def test_dashboard_server_post_unknown_non_api_returns_404(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_raw(server, "/not-api", b"{}")

    assert status == 404
    assert isinstance(payload, str)
    assert "Error response" in payload


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


def test_dashboard_server_get_evidence_internal_error_is_redacted(tmp_repo, monkeypatch) -> None:
    def broken_evidence(repo_root, ref):
        raise RuntimeError(f"secret evidence path {repo_root} {ref}")

    monkeypatch.setattr(dashboard_server, "evidence_payload", broken_evidence)

    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/runs/current/evidence?ref=secret.txt"
        status, payload = read_json_error(url)

    assert status == 500
    assert payload == {
        "ok": False,
        "error": {"code": "internal_error", "message": "Internal dashboard server error"},
    }


def test_dashboard_server_post_runs_starts_task(tmp_repo, monkeypatch) -> None:
    write_dashboard_config(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.executor_registry.MulticaAdapter.start",
        lambda self, task: SimpleNamespace(adapter="multica", run_id="task-web-1", issue_id="AGO-WEB-1"),
    )
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/runs"
        status, payload = post_json(
            url,
            {
                "title": "Ship dashboard input",
                "intent": "Create tasks from the local dashboard",
                "workflow": "feature",
                "gates": ["tests_pass"],
            },
        )

    assert status == 201
    assert payload["ok"] is True
    assert payload["run"]["title"] == "Ship dashboard input"
    assert payload["run"]["workflow"] == "feature"
    assert payload["run"]["executor_run"]["run_id"] == "task-web-1"
    assert payload["issue_id"] == "AGO-WEB-1"
    assert payload["execution_result"]["mode"] == "legacy"

    task_data = yaml.safe_load(
        (tmp_repo / ".agos" / "tasks" / "current" / "task.yaml").read_text(encoding="utf-8")
    )
    assert task_data["title"] == "Ship dashboard input"
    assert task_data["intent"] == "Create tasks from the local dashboard"
    assert task_data["gates"] == ["tests_pass"]


def test_dashboard_server_rejects_unknown_execution_mode_without_task(tmp_repo) -> None:
    write_dashboard_config(tmp_repo)

    with running_dashboard_server(tmp_repo) as server:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/runs",
            data=json.dumps({"title": "Invalid mode", "mode": "unknown"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        status, payload = read_json_request_error(request)

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert "mode" in payload["error"]["message"]
    assert not (tmp_repo / ".agos" / "tasks" / "current" / "task.yaml").exists()


def test_dashboard_server_post_runs_can_replace_active_task(tmp_repo, monkeypatch) -> None:
    write_dashboard_config(tmp_repo)
    current = tmp_repo / ".agos" / "tasks" / "current"
    (current / "task.yaml").write_text(
        "id: agos-old\ntitle: Old task\nworkflow: feature\ngates: []\nexecutor:\n  adapter: multica\n  agent: Lambda\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agos.cli.executor_registry.MulticaAdapter.start",
        lambda self, task: SimpleNamespace(adapter="multica", run_id="task-web-2", issue_id="AGO-WEB-2"),
    )

    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_json(
            f"http://127.0.0.1:{server.server_port}/api/runs",
            {"title": "New task", "replace_active": True},
        )

    assert status == 201
    assert payload["ok"] is True
    assert payload["run"]["title"] == "New task"
    assert (tmp_repo / ".agos" / "tasks" / "archive").is_dir()


def test_dashboard_server_post_archive_current_task(tmp_repo) -> None:
    write_dashboard_config(tmp_repo)
    current = tmp_repo / ".agos" / "tasks" / "current"
    (current / "task.yaml").write_text(
        "id: agos-old\ntitle: Old task\nworkflow: feature\ngates: []\nexecutor:\n  adapter: multica\n  agent: Lambda\n",
        encoding="utf-8",
    )

    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_json(
            f"http://127.0.0.1:{server.server_port}/api/runs/current/archive",
            {},
        )

    assert status == 200
    assert payload["ok"] is True
    assert payload["archived_task_id"] == "agos-old"
    assert not current.exists()
    assert (tmp_repo / ".agos" / "tasks" / "archive").is_dir()


def test_dashboard_server_post_continue_archived_task(tmp_repo) -> None:
    write_dashboard_config(tmp_repo)
    current = tmp_repo / ".agos" / "tasks" / "current"
    (current / "task.yaml").write_text(
        "id: agos-old\ntitle: Old task\nworkflow: feature\ngates: []\nexecutor:\n  adapter: multica\n  agent: Lambda\n",
        encoding="utf-8",
    )

    with running_dashboard_server(tmp_repo) as server:
        _status, archived = post_json(
            f"http://127.0.0.1:{server.server_port}/api/runs/current/archive",
            {},
        )
        archive_id = archived["archive_id"]
        status, payload = post_json(
            f"http://127.0.0.1:{server.server_port}/api/runs/archive/{archive_id}/continue",
            {},
        )

    assert status == 200
    assert payload["ok"] is True
    assert payload["run"]["id"] == "agos-old"
    assert (current / "task.yaml").is_file()


def test_dashboard_server_post_current_lifecycle_actions(tmp_repo, monkeypatch) -> None:
    write_dashboard_config(tmp_repo)
    from agos.core.adapter import ExecutorRun
    from agos.core.ledger import Ledger
    from agos.core.repo import repo_paths
    from agos.core.status import TaskStatus, load_status, save_status
    from agos.core.task import ExecutorBinding, Task, save_task

    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-life-web",
        title="Lifecycle task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-life-web", issue_id=None),
            ledger_head_hash=started["hash"],
        ),
        paths,
    )
    dispatches: list[Task] = []

    def fake_start(self, task: Task) -> ExecutorRun:
        dispatches.append(task)
        return ExecutorRun(adapter="multica", run_id=f"run-life-web-{len(dispatches)}", issue_id=None)

    monkeypatch.setattr("agos.cli.executor_registry.MulticaAdapter.start", fake_start)

    with running_dashboard_server(tmp_repo) as server:
        base = f"http://127.0.0.1:{server.server_port}"
        status_pause, paused = post_json(f"{base}/api/runs/current/pause", {})
        status_resume, resumed = post_json(f"{base}/api/runs/current/resume", {})
        status_restart, restarted = post_json(f"{base}/api/runs/current/restart", {})

    assert status_pause == status_resume == status_restart == 200
    assert paused["run"]["phase"] == "blocked"
    assert resumed["run"]["phase"] == "executing"
    assert resumed["run"]["executor_run"]["run_id"] == "run-life-web-1"
    assert restarted["run"]["phase"] == "executing"
    assert restarted["run"]["executor_run"]["run_id"] == "run-life-web-2"
    assert [task.id for task in dispatches] == ["agos-life-web", "agos-life-web"]
    assert load_status(paths).phase == "executing"


def test_dashboard_server_serializes_concurrent_lifecycle_posts(tmp_repo, monkeypatch) -> None:
    write_dashboard_config(tmp_repo)
    from agos.core.adapter import ExecutorRun
    from agos.core.ledger import Ledger
    from agos.core.repo import repo_paths
    from agos.core.status import TaskStatus, load_status, save_status
    from agos.core.task import ExecutorBinding, Task, save_task

    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-life-concurrent",
        title="Concurrent lifecycle task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-life-concurrent", issue_id=None),
            ledger_head_hash=started["hash"],
        ),
        paths,
    )
    dispatch_count = 0
    dispatch_lock = threading.Lock()

    def fake_start(self, task: Task) -> ExecutorRun:
        nonlocal dispatch_count
        del self, task
        with dispatch_lock:
            dispatch_count += 1
            run_id = f"run-life-concurrent-{dispatch_count}"
        return ExecutorRun(adapter="multica", run_id=run_id, issue_id=None)

    monkeypatch.setattr("agos.cli.executor_registry.MulticaAdapter.start", fake_start)

    def post_action(base: str, action: str) -> tuple[int, dict[str, object]]:
        return post_json(f"{base}/api/runs/current/{action}", {})

    actions = ["pause", "resume", "restart"] * 8
    with running_dashboard_server(tmp_repo) as server:
        base = f"http://127.0.0.1:{server.server_port}"
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda action: post_action(base, action), actions))

        with urllib.request.urlopen(f"{base}/api/runs/current/ledger", timeout=5) as response:
            ledger_payload = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(f"{base}/api/runs/current", timeout=5) as response:
            current_payload = json.loads(response.read().decode("utf-8"))

    assert all(status == 200 for status, _payload in results)
    assert all(payload["ok"] is True for _status, payload in results)
    assert ledger_payload["verified"] is True
    assert ledger_payload["error"] is None
    assert dispatch_count == actions.count("resume") + actions.count("restart")
    assert ledger_payload["count"] == 1 + actions.count("pause") + (
        actions.count("resume") + actions.count("restart")
    ) * 2
    Ledger(paths.ledger).verify_chain()
    assert load_status(paths).ledger_head_hash == Ledger(paths.ledger).head_hash()
    assert current_payload["ok"] is True


def test_dashboard_server_post_runs_requires_title(tmp_repo) -> None:
    write_dashboard_config(tmp_repo)
    with running_dashboard_server(tmp_repo) as server:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/runs",
            data=json.dumps({"intent": "missing title"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        status, payload = read_json_request_error(request)

    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == "title is required"
    assert not (tmp_repo / ".agos" / "tasks" / "current" / "task.yaml").exists()


def test_dashboard_server_post_rejects_invalid_content_length(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_raw(server, "/api/runs", content_length="not-an-int")

    assert status == 400
    assert isinstance(payload, dict)
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == "invalid Content-Length"


def test_dashboard_server_post_rejects_empty_body(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_raw(server, "/api/runs")

    assert status == 400
    assert isinstance(payload, dict)
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == "JSON body is required"


def test_dashboard_server_post_rejects_oversized_body_without_reading(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_raw(server, "/api/runs", content_length=str(64 * 1024 + 1))

    assert status == 400
    assert isinstance(payload, dict)
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == "JSON body is too large"


def test_dashboard_server_post_rejects_invalid_json_body(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_raw(server, "/api/runs", b"{")

    assert status == 400
    assert isinstance(payload, dict)
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == "invalid JSON body"


def test_dashboard_server_post_rejects_non_object_json_body(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_raw(server, "/api/runs", b"[]")

    assert status == 400
    assert isinstance(payload, dict)
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == "JSON body must be an object"


def test_dashboard_server_post_runs_internal_error_is_redacted(tmp_repo, monkeypatch) -> None:
    def broken_start(repo_root, payload):
        raise RuntimeError("secret start failure")

    monkeypatch.setattr(dashboard_server, "start_run_payload", broken_start)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs",
                data=json.dumps({"title": "Start"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 500
    assert payload == {
        "ok": False,
        "error": {"code": "internal_error", "message": "Internal dashboard server error"},
    }


def test_dashboard_server_archive_business_error_returns_400(tmp_repo, monkeypatch) -> None:
    def unavailable_archive(repo_root):
        raise dashboard_server.DashboardApiError("not_initialized", "archive unavailable")

    monkeypatch.setattr(dashboard_server, "archive_current_task_payload", unavailable_archive)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs/current/archive",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 400
    assert payload["error"]["code"] == "not_initialized"


def test_dashboard_server_archive_internal_error_is_redacted(tmp_repo, monkeypatch) -> None:
    def broken_archive(repo_root):
        raise RuntimeError("secret archive failure")

    monkeypatch.setattr(dashboard_server, "archive_current_task_payload", broken_archive)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs/current/archive",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 500
    assert payload["error"]["message"] == "Internal dashboard server error"


def test_dashboard_server_continue_internal_error_is_redacted(tmp_repo, monkeypatch) -> None:
    def broken_continue(repo_root, archive_id):
        raise RuntimeError(f"secret continue failure {archive_id}")

    monkeypatch.setattr(dashboard_server, "continue_archived_task_payload", broken_continue)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs/archive/arch-1/continue",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 500
    assert payload["error"]["message"] == "Internal dashboard server error"


def test_dashboard_server_continue_business_error_returns_400(tmp_repo, monkeypatch) -> None:
    def unavailable_continue(repo_root, archive_id):
        raise dashboard_server.DashboardApiError("not_found", f"missing {archive_id}")

    monkeypatch.setattr(dashboard_server, "continue_archived_task_payload", unavailable_continue)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs/archive/arch-1/continue",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 400
    assert payload["error"]["code"] == "not_found"


def test_dashboard_server_simple_post_internal_error_is_redacted(tmp_repo, monkeypatch) -> None:
    def broken_pause(repo_root):
        raise RuntimeError("secret pause failure")

    monkeypatch.setattr(dashboard_server, "pause_current_task_payload", broken_pause)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs/current/pause",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 500
    assert payload["error"]["message"] == "Internal dashboard server error"


def test_dashboard_server_simple_post_business_error_returns_400(tmp_repo, monkeypatch) -> None:
    def unavailable_pause(repo_root):
        raise dashboard_server.DashboardApiError("not_initialized", "pause unavailable")

    monkeypatch.setattr(dashboard_server, "pause_current_task_payload", unavailable_pause)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs/current/pause",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 400
    assert payload["error"]["code"] == "not_initialized"


def test_dashboard_server_post_review_run_uses_selected_reviewer(tmp_repo) -> None:
    write_dashboard_config(tmp_repo)
    # Build a minimal active task for the review packet.
    from agos.core.adapter import ExecutorRun
    from agos.core.ledger import Ledger
    from agos.core.repo import repo_paths
    from agos.core.status import TaskStatus, save_status
    from agos.core.task import ExecutorBinding, Task, save_task

    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-review-web",
        title="Review from dashboard",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-review-web", issue_id=None),
            ledger_head_hash=started["hash"],
        ),
        paths,
    )

    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_json(
            f"http://127.0.0.1:{server.server_port}/api/reviews/run",
            {"reviewer": "reviewer:tests"},
        )

    assert status == 201
    assert payload["ok"] is True
    assert payload["review_run"]["reviewers"] == ["tests"]


def test_dashboard_server_review_run_internal_error_is_redacted(tmp_repo, monkeypatch) -> None:
    def broken_review(repo_root, payload):
        raise RuntimeError("secret review failure")

    monkeypatch.setattr(dashboard_server, "review_run_payload", broken_review)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/reviews/run",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 500
    assert payload["error"]["message"] == "Internal dashboard server error"


def test_dashboard_server_review_run_business_error_returns_400(tmp_repo, monkeypatch) -> None:
    def unavailable_review(repo_root, payload):
        raise dashboard_server.DashboardApiError("invalid_request", "review unavailable")

    monkeypatch.setattr(dashboard_server, "review_run_payload", unavailable_review)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/reviews/run",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


def test_dashboard_server_post_select_agent_option_dispatches_executor(
    tmp_repo,
    monkeypatch,
) -> None:
    write_dashboard_config(tmp_repo)
    from agos.core.adapter import ExecutorRun
    from agos.core.ledger import Ledger
    from agos.core.repo import repo_paths
    from agos.core.status import TaskStatus, save_status
    from agos.core.task import ExecutorBinding, Task, save_task

    paths = repo_paths(tmp_repo)
    task = Task(
        id="agos-option-web",
        title="Continue selected option",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    ledger.append(
        {
            "type": "executor_completed",
            "run_id": "run-option-source",
            "state": "completed",
            "detail": json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "方案 A：Implement the selected dashboard path",
                    },
                },
                ensure_ascii=False,
            ),
        }
    )
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-option-source", issue_id=None),
            ledger_head_hash=started["hash"],
        ),
        paths,
    )
    monkeypatch.setattr(
        "agos.cli.executor_registry.MulticaAdapter.start",
        lambda self, task: SimpleNamespace(adapter="multica", run_id="run-option-followup", issue_id="AGO-2"),
    )

    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_json(
            f"http://127.0.0.1:{server.server_port}/api/runs/current/agent-options/select",
            {"option_id": "option-1"},
        )

    assert status == 201
    assert payload["ok"] is True
    assert payload["run_id"] == "run-option-followup"
    assert payload["selected_option"]["id"] == "option-1"


def test_dashboard_server_select_agent_option_internal_error_is_redacted(
    tmp_repo,
    monkeypatch,
) -> None:
    def broken_select(repo_root, payload):
        raise RuntimeError("secret option failure")

    monkeypatch.setattr(dashboard_server, "select_agent_option_payload", broken_select)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs/current/agent-options/select",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 500
    assert payload["error"]["message"] == "Internal dashboard server error"


def test_dashboard_server_select_agent_option_business_error_returns_400(
    tmp_repo,
    monkeypatch,
) -> None:
    def unavailable_select(repo_root, payload):
        raise dashboard_server.DashboardApiError("invalid_request", "option unavailable")

    monkeypatch.setattr(dashboard_server, "select_agent_option_payload", unavailable_select)

    with running_dashboard_server(tmp_repo) as server:
        status, payload = read_json_request_error(
            urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/api/runs/current/agent-options/select",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )

    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


def test_serve_dashboard_forever_opens_browser_and_closes_server(
    tmp_repo,
    monkeypatch,
    capsys,
) -> None:
    calls = {}

    class FakeServer:
        server_port = 43210
        auth_token = "remote-dashboard-token"
        expose_token_in_index = False

        def serve_forever(self):
            calls["served"] = True
            raise KeyboardInterrupt

        def server_close(self):
            calls["closed"] = True

    def fake_create(repo_root, *, host: str, port: int, token: str | None):
        calls["repo_root"] = repo_root
        calls["host"] = host
        calls["port"] = port
        calls["token"] = token
        return FakeServer()

    monkeypatch.setattr(dashboard_server, "create_dashboard_server", fake_create)
    monkeypatch.setattr(dashboard_server.webbrowser, "open", lambda url: calls.setdefault("url", url))

    try:
        dashboard_server.serve_dashboard_forever(
            tmp_repo,
            host="0.0.0.0",
            port=8788,
            open_browser=True,
            token="remote-dashboard-token",
        )
    except KeyboardInterrupt:
        pass

    assert calls == {
        "repo_root": tmp_repo,
        "host": "0.0.0.0",
        "port": 8788,
        "token": "remote-dashboard-token",
        "url": "http://127.0.0.1:43210/#token=remote-dashboard-token",
        "served": True,
        "closed": True,
    }
    output = capsys.readouterr().out
    assert "AGOS dashboard: http://127.0.0.1:43210" in output
    assert "remote-dashboard-token" not in output


def test_serve_dashboard_forever_returns_url_when_server_stops(
    tmp_repo,
    monkeypatch,
) -> None:
    class FakeServer:
        server_port = 43211
        auth_token = "generated-dashboard-token"
        expose_token_in_index = True

        def serve_forever(self):
            return None

        def server_close(self):
            return None

    monkeypatch.setattr(
        dashboard_server,
        "create_dashboard_server",
        lambda repo_root, *, host, port, token: FakeServer(),
    )

    url = dashboard_server.serve_dashboard_forever(
        tmp_repo,
        host="localhost",
        port=8788,
        open_browser=False,
        token=None,
    )

    assert url == "http://localhost:43211"
