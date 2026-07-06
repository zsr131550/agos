from __future__ import annotations

from contextlib import contextmanager
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from types import SimpleNamespace

import agos.web.server as dashboard_server
from agos.web.server import DashboardHTTPServer, create_dashboard_server
import yaml


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


def post_json(url: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body)


def read_json_request_error(request: urllib.request.Request) -> tuple[int, dict[str, object]]:
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


def test_dashboard_server_serves_health_json(tmp_repo) -> None:
    with running_dashboard_server(tmp_repo) as server:
        url = f"http://127.0.0.1:{server.server_port}/api/health"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["service"] == "agos-dashboard"


def test_dashboard_server_serves_agents_json(tmp_repo) -> None:
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

    task_data = yaml.safe_load(
        (tmp_repo / ".agos" / "tasks" / "current" / "task.yaml").read_text(encoding="utf-8")
    )
    assert task_data["title"] == "Ship dashboard input"
    assert task_data["intent"] == "Create tasks from the local dashboard"
    assert task_data["gates"] == ["tests_pass"]


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
