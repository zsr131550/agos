from __future__ import annotations

import subprocess
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

from agos.core.execution_worker import WorkerStartRequest, WorkerWorkspaceHandle


def test_codex_worker_adapter_starts_cli_with_workspace_and_prompt(monkeypatch, tmp_path):
    from agos.adapters.workers.codex_cli import CodexWorkerAdapter
    import agos.adapters.workers.codex_cli as codex_module

    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0
        stdout = '{"run_id": "codex-run-01"}'
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        assert kwargs["cwd"] == tmp_path
        return FakeProc()

    monkeypatch.setattr(codex_module, "run_command", fake_run)

    run = CodexWorkerAdapter(command="codex").start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Implement README change",
            workspace_path=str(tmp_path),
        )
    )

    assert run.backend == "codex"
    assert run.run_id == "codex-run-01"
    assert calls[0][:2] == ["codex", "exec"]
    assert "--json" in calls[0]
    assert "Implement README change" in calls[0]


def test_codex_worker_adapter_poll_and_cancel(monkeypatch):
    from agos.adapters.workers.codex_cli import CodexWorkerAdapter
    import agos.adapters.workers.codex_cli as codex_module

    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0
        stdout = '{"state": "completed", "detail": "ok"}'
        stderr = ""

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr(codex_module, "run_command", fake_run)
    adapter = CodexWorkerAdapter(command="codex")

    status = adapter.poll("codex-run-01", subtask_id="subtask-01")
    adapter.cancel("codex-run-01")

    assert status.state == "completed"
    assert calls[0] == ["codex", "status", "codex-run-01", "--json"]
    assert calls[1] == ["codex", "cancel", "codex-run-01", "--json"]


def test_multica_worker_adapter_wraps_existing_executor(monkeypatch):
    from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
    import agos.adapters.workers.multica_worker as worker_module

    calls: list[list[str]] = []

    class FakeProc:
        def __init__(self, stdout: str) -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(args)
        if args[1:3] == ["issue", "create"]:
            return FakeProc('{"identifier": "MUL-1"}')
        if args[1:3] == ["issue", "runs"]:
            return FakeProc('{"runs": [{"id": "multica-run-01", "status": "done"}]}')
        raise AssertionError(args)

    monkeypatch.setattr(worker_module, "run_command", fake_run)
    adapter = MulticaWorkerAdapter(multica_bin="multica", agent="Lambda")

    run = adapter.start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do the work",
            workspace_path="C:/workspace",
        )
    )
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)

    assert run.backend == "multica"
    assert run.run_id == "multica-run-01"
    assert status.state == "completed"
    assert "--assignee" in calls[0]


def test_openhands_worker_adapter_posts_and_polls(monkeypatch, tmp_path):
    from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
    import agos.adapters.workers.openhands as openhands_module

    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def fake_request(method: str, url: str, payload=None, timeout=30, headers=None):
        del timeout, headers
        requests.append((method, url, payload))
        if method == "POST" and url.endswith("/runs"):
            return {"run_id": "openhands-run-01"}
        if method == "GET" and url.endswith("/runs/openhands-run-01"):
            return {"state": "completed", "detail": "ok", "output_refs": ["workers/log.json"]}
        if method == "POST" and url.endswith("/runs/openhands-run-01/cancel"):
            return {"state": "cancelled"}
        raise HTTPError(url, 404, "not found", hdrs=None, fp=None)

    monkeypatch.setattr(openhands_module, "_json_request", fake_request)
    adapter = OpenHandsWorkerAdapter(endpoint="http://openhands.local", token="secret")

    run = adapter.start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do the work",
            workspace_path=str(tmp_path),
        )
    )
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)
    adapter.cancel(run.run_id)

    assert run.run_id == "openhands-run-01"
    assert status.output_refs == ["workers/log.json"]
    assert requests[0][2]["workspace_path"] == str(tmp_path)


def test_worker_adapters_export_candidate_uses_workspace_handle(tmp_path):
    from agos.adapters.workers.local_worktree import LocalWorktreeWorkerAdapter

    class FakeManager:
        def capture_patch(self, workspace: Path) -> bytes:
            assert workspace == tmp_path
            return b"diff --git a/README.md b/README.md\n"

    adapter = LocalWorktreeWorkerAdapter(FakeManager())
    export = adapter.export_candidate(
        WorkerWorkspaceHandle(
            subtask_id="subtask-01",
            metadata={"workspace_path": str(tmp_path)},
        )
    )

    assert export["patch_bytes"].startswith(b"diff --git")


def test_fake_worker_adapter_lifecycle():
    from agos.adapters.workers.fake import FakeWorkerAdapter

    adapter = FakeWorkerAdapter()
    prepared = adapter.prepare(
        type(
            "Assignment",
            (),
            {
                "subtask": type(
                    "Subtask",
                    (),
                    {"id": "subtask-01"},
                )()
            },
        )()
    )
    run = adapter.start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do work",
            workspace_path=prepared.binding.path,
        )
    )

    assert adapter.poll(run.run_id, subtask_id="subtask-01").state == "completed"
    adapter.cancel(run.run_id)
    assert adapter.poll(run.run_id, subtask_id="subtask-01").state == "cancelled"


def test_codex_worker_adapter_passes_timeout_and_env(monkeypatch, tmp_path):
    from agos.adapters.workers.codex_cli import CodexWorkerAdapter
    import agos.adapters.workers.codex_cli as codex_module

    observed: list[dict[str, object]] = []

    class FakeProc:
        returncode = 0
        stdout = '{"run_id": "codex-run-01"}'
        stderr = ""

    def fake_run(args, **kwargs):
        del args
        observed.append(kwargs)
        return FakeProc()

    monkeypatch.setattr(codex_module, "run_command", fake_run)

    CodexWorkerAdapter(
        command="codex",
        timeout_seconds=99,
        env={"AGOS_WORKER_MODE": "production"},
    ).start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Implement README change",
            workspace_path=str(tmp_path),
        )
    )

    assert observed[0]["timeout"] == 99
    assert observed[0]["env"]["AGOS_WORKER_MODE"] == "production"


def test_multica_worker_adapter_passes_timeout_and_env(monkeypatch):
    from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
    import agos.adapters.workers.multica_worker as worker_module

    observed: list[dict[str, object]] = []

    class FakeProc:
        def __init__(self, stdout: str) -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(args, **kwargs):
        observed.append(kwargs)
        if args[1:3] == ["issue", "create"]:
            return FakeProc('{"identifier": "MUL-1"}')
        if args[1:3] == ["issue", "runs"]:
            return FakeProc('{"runs": [{"id": "multica-run-01", "status": "done"}]}')
        raise AssertionError(args)

    monkeypatch.setattr(worker_module, "run_command", fake_run)

    MulticaWorkerAdapter(
        multica_bin="multica",
        agent="Lambda",
        timeout_seconds=88,
        env={"AGOS_WORKER_MODE": "production"},
    ).start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do the work",
            workspace_path="C:/workspace",
        )
    )

    assert observed[0]["timeout"] == 88
    assert observed[0]["env"]["AGOS_WORKER_MODE"] == "production"
    assert observed[1]["timeout"] == 88


def test_openhands_worker_adapter_passes_configured_timeout(monkeypatch, tmp_path):
    from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
    import agos.adapters.workers.openhands as openhands_module

    observed: list[int] = []

    def fake_request(method: str, url: str, payload=None, timeout=30, headers=None):
        del method, url, payload, headers
        observed.append(timeout)
        return {"run_id": "openhands-run-01"}

    monkeypatch.setattr(openhands_module, "_json_request", fake_request)

    OpenHandsWorkerAdapter(endpoint="http://openhands.local", timeout=77).start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do the work",
            workspace_path=str(tmp_path),
        )
    )

    assert observed == [77]


def test_codex_worker_adapter_collects_configured_artifacts(monkeypatch, tmp_path):
    from agos.adapters.workers.codex_cli import CodexWorkerAdapter
    import agos.adapters.workers.codex_cli as codex_module

    artifact_dir = tmp_path / ".agos-worker"
    artifact_dir.mkdir()
    (artifact_dir / "result.json").write_text("{}", encoding="utf-8")

    class FakeProc:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(args, **kwargs):
        del kwargs
        if args[1] == "exec":
            return FakeProc('{"run_id": "codex-run-01"}')
        if args[1] == "status":
            return FakeProc('{"state": "completed"}')
        raise AssertionError(args)

    monkeypatch.setattr(codex_module, "run_command", fake_run)
    adapter = CodexWorkerAdapter(command="codex", artifact_globs=[".agos-worker/*.json"])

    run = adapter.start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do the work",
            workspace_path=str(tmp_path),
        )
    )
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)

    assert status.output_refs == [".agos-worker/result.json"]


def test_codex_worker_health_reports_command_availability(monkeypatch):
    from agos.adapters.workers.codex_cli import CodexWorkerAdapter
    import agos.adapters.workers.codex_cli as codex_module

    monkeypatch.setattr(codex_module.shutil, "which", lambda command: f"C:/bin/{command}.exe")

    health = CodexWorkerAdapter(
        command="codex",
        timeout_seconds=120,
        poll_interval_seconds=2,
        artifact_globs=[".agos-worker/*.json"],
    ).health()

    assert health.name == "codex"
    assert health.adapter == "codex_cli"
    assert health.state == "healthy"
    assert health.metadata["timeout_seconds"] == "120"
    assert health.metadata["artifact_globs"] == ".agos-worker/*.json"


def test_codex_worker_health_reports_missing_command(monkeypatch):
    from agos.adapters.workers.codex_cli import CodexWorkerAdapter
    import agos.adapters.workers.codex_cli as codex_module

    monkeypatch.setattr(codex_module.shutil, "which", lambda _command: None)

    health = CodexWorkerAdapter(command="missing-codex").health()

    assert health.state == "unhealthy"
    assert health.checks[0].name == "command_available"
    assert health.checks[0].state == "failed"


def test_multica_worker_adapter_collects_configured_artifacts(monkeypatch, tmp_path):
    from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
    import agos.adapters.workers.multica_worker as worker_module

    artifact_dir = tmp_path / ".agos-worker"
    artifact_dir.mkdir()
    (artifact_dir / "result.json").write_text("{}", encoding="utf-8")

    class FakeProc:
        def __init__(self, stdout: str) -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(args, **kwargs):
        del kwargs
        if args[1:3] == ["issue", "create"]:
            return FakeProc('{"identifier": "MUL-1"}')
        if args[1:3] == ["issue", "runs"]:
            return FakeProc('{"runs": [{"id": "multica-run-01", "status": "done"}]}')
        raise AssertionError(args)

    monkeypatch.setattr(worker_module, "run_command", fake_run)
    adapter = MulticaWorkerAdapter(
        multica_bin="multica",
        agent="Lambda",
        artifact_globs=[".agos-worker/*.json"],
    )

    run = adapter.start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do the work",
            workspace_path=str(tmp_path),
        )
    )
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)

    assert status.output_refs == [".agos-worker/result.json"]


def test_multica_worker_health_checks_daemon_and_workspace(monkeypatch):
    from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
    import agos.adapters.workers.multica_worker as worker_module

    calls: list[list[str]] = []

    class FakeProc:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(args, **kwargs):
        del kwargs
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr(worker_module, "run_command", fake_run)

    health = MulticaWorkerAdapter(multica_bin="multica", agent="Lambda").health()

    assert health.state == "healthy"
    assert calls == [
        ["multica", "daemon", "status"],
        ["multica", "workspace", "list", "--output", "json"],
    ]


def test_multica_worker_health_reports_failed_check(monkeypatch):
    from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
    import agos.adapters.workers.multica_worker as worker_module

    class FakeProc:
        returncode = 1
        stdout = ""
        stderr = "daemon down"

    monkeypatch.setattr(worker_module, "run_command", lambda *_args, **_kwargs: FakeProc())

    health = MulticaWorkerAdapter(multica_bin="multica", agent="Lambda").health()

    assert health.state == "unhealthy"
    assert health.checks[0].name == "daemon_status"
    assert health.checks[0].state == "failed"
    assert "daemon down" in health.checks[0].detail


def test_openhands_worker_adapter_collects_configured_artifacts(monkeypatch, tmp_path):
    from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
    import agos.adapters.workers.openhands as openhands_module

    artifact_dir = tmp_path / ".agos-worker"
    artifact_dir.mkdir()
    (artifact_dir / "result.json").write_text("{}", encoding="utf-8")

    def fake_request(method: str, url: str, payload=None, timeout=30, headers=None):
        del payload, timeout, headers
        if method == "POST" and url.endswith("/runs"):
            return {"run_id": "openhands-run-01"}
        if method == "GET" and url.endswith("/runs/openhands-run-01"):
            return {"state": "completed"}
        raise HTTPError(url, 404, "not found", hdrs=None, fp=None)

    monkeypatch.setattr(openhands_module, "_json_request", fake_request)
    adapter = OpenHandsWorkerAdapter(
        endpoint="http://openhands.local",
        artifact_globs=[".agos-worker/*.json"],
    )

    run = adapter.start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do the work",
            workspace_path=str(tmp_path),
        )
    )
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)

    assert status.output_refs == [".agos-worker/result.json"]


def test_openhands_worker_health_calls_health_endpoint(monkeypatch):
    from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
    import agos.adapters.workers.openhands as openhands_module

    requests: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, payload=None, timeout=30, headers=None):
        del payload, timeout, headers
        requests.append((method, url))
        return {"status": "ok"}

    monkeypatch.setattr(openhands_module, "_json_request", fake_request)

    health = OpenHandsWorkerAdapter(endpoint="http://openhands.local").health()

    assert health.state == "healthy"
    assert requests == [("GET", "http://openhands.local/health")]


def test_openhands_worker_health_reports_endpoint_failure(monkeypatch):
    from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
    import agos.adapters.workers.openhands as openhands_module

    def fake_request(method: str, url: str, payload=None, timeout=30, headers=None):
        del method, url, payload, timeout, headers
        raise RuntimeError("connection refused")

    monkeypatch.setattr(openhands_module, "_json_request", fake_request)

    health = OpenHandsWorkerAdapter(endpoint="http://openhands.local").health()

    assert health.state == "unhealthy"
    assert health.checks[0].name == "endpoint_health"
    assert health.checks[0].state == "failed"
    assert "connection refused" in health.checks[0].detail


def test_codex_worker_process_error_includes_stdout_fallback(monkeypatch, tmp_path):
    from agos.adapters.workers.codex_cli import CodexWorkerAdapter
    import agos.adapters.workers.codex_cli as codex_module

    class FakeProc:
        returncode = 2
        stdout = "stdout failure"
        stderr = ""

    monkeypatch.setattr(codex_module, "run_command", lambda *_args, **_kwargs: FakeProc())

    try:
        CodexWorkerAdapter(command="codex").start(
            WorkerStartRequest(
                run_id="execution-run-01",
                subtask_id="subtask-01",
                prompt="Do the work",
                workspace_path=str(tmp_path),
            )
        )
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected process failure")

    assert "codex exec" in message
    assert "exit 2" in message
    assert "stdout failure" in message


def test_codex_worker_process_timeout_has_action_and_timeout(monkeypatch, tmp_path):
    from agos.adapters.workers.codex_cli import CodexWorkerAdapter
    import agos.adapters.workers.codex_cli as codex_module

    def fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["codex", "exec"], timeout=9)

    monkeypatch.setattr(codex_module, "run_command", fake_run)

    try:
        CodexWorkerAdapter(command="codex", timeout_seconds=9).start(
            WorkerStartRequest(
                run_id="execution-run-01",
                subtask_id="subtask-01",
                prompt="Do the work",
                workspace_path=str(tmp_path),
            )
        )
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected timeout failure")

    assert "codex exec" in message
    assert "timed out after 9 seconds" in message


def test_multica_worker_invalid_json_names_action(monkeypatch):
    from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
    import agos.adapters.workers.multica_worker as worker_module

    class FakeProc:
        returncode = 0
        stdout = "{not-json"
        stderr = ""

    monkeypatch.setattr(worker_module, "run_command", lambda *_args, **_kwargs: FakeProc())

    try:
        MulticaWorkerAdapter(multica_bin="multica", agent="Lambda").start(
            WorkerStartRequest(
                run_id="execution-run-01",
                subtask_id="subtask-01",
                prompt="Do the work",
                workspace_path="C:/workspace",
            )
        )
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected JSON failure")

    assert "multica issue create" in message
    assert "invalid JSON" in message


def test_multica_worker_poll_merges_remote_and_local_output_refs(monkeypatch, tmp_path):
    from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
    import agos.adapters.workers.multica_worker as worker_module

    artifact_dir = tmp_path / ".agos-worker"
    artifact_dir.mkdir()
    (artifact_dir / "result.json").write_text("{}", encoding="utf-8")

    class FakeProc:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(args, **kwargs):
        del kwargs
        if args[1:3] == ["issue", "create"]:
            return FakeProc('{"identifier": "MUL-1"}')
        if args[1:3] == ["issue", "runs"]:
            return FakeProc(
                '{"runs": [{"id": "multica-run-01", "status": "done", '
                '"output_refs": ["remote/result.json"]}]}'
            )
        raise AssertionError(args)

    monkeypatch.setattr(worker_module, "run_command", fake_run)
    adapter = MulticaWorkerAdapter(
        multica_bin="multica",
        agent="Lambda",
        artifact_globs=[".agos-worker/*.json"],
    )
    run = adapter.start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do the work",
            workspace_path=str(tmp_path),
        )
    )

    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)

    assert status.output_refs == ["remote/result.json", ".agos-worker/result.json"]


def test_multica_worker_cancel_collects_artifacts(monkeypatch, tmp_path):
    from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
    import agos.adapters.workers.multica_worker as worker_module

    artifact_dir = tmp_path / ".agos-worker"
    artifact_dir.mkdir()
    (artifact_dir / "cancel.json").write_text("{}", encoding="utf-8")

    class FakeProc:
        returncode = 0
        stdout = '{"state": "cancelled", "output_refs": ["remote/cancel.json"]}'
        stderr = ""

    monkeypatch.setattr(worker_module, "run_command", lambda *_args, **_kwargs: FakeProc())
    adapter = MulticaWorkerAdapter(
        multica_bin="multica",
        agent="Lambda",
        artifact_globs=[".agos-worker/*.json"],
    )
    adapter._subtask_by_run_id["multica-run-01"] = "subtask-01"
    adapter._workspaces_by_run_id["multica-run-01"] = str(tmp_path)

    status = adapter.cancel("multica-run-01")

    assert status.state == "cancelled"
    assert status.output_refs == ["remote/cancel.json", ".agos-worker/cancel.json"]


def test_openhands_json_request_wraps_http_errors(monkeypatch):
    import agos.adapters.workers.openhands as openhands_module

    def fake_urlopen(_request, timeout=30):
        del timeout
        raise HTTPError(
            "http://openhands.local/runs",
            503,
            "Service Unavailable",
            hdrs=None,
            fp=BytesIO(b"backend down"),
        )

    monkeypatch.setattr(openhands_module, "urlopen", fake_urlopen)

    try:
        openhands_module._json_request("POST", "http://openhands.local/runs", timeout=12)
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected HTTP failure")

    assert "OpenHands POST http://openhands.local/runs failed" in message
    assert "HTTP 503 Service Unavailable" in message
    assert "backend down" in message


def test_openhands_json_request_wraps_url_and_json_errors(monkeypatch):
    import agos.adapters.workers.openhands as openhands_module

    def fake_urlopen_url_error(_request, timeout=30):
        del timeout
        raise URLError("connection refused")

    monkeypatch.setattr(openhands_module, "urlopen", fake_urlopen_url_error)
    try:
        openhands_module._json_request("GET", "http://openhands.local/health")
    except RuntimeError as exc:
        url_message = str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected URL failure")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{not-json"

    monkeypatch.setattr(openhands_module, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    try:
        openhands_module._json_request("GET", "http://openhands.local/health")
    except RuntimeError as exc:
        json_message = str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected JSON failure")

    assert "connection refused" in url_message
    assert "invalid JSON" in json_message


def test_openhands_worker_start_sends_configured_env(monkeypatch, tmp_path):
    from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
    import agos.adapters.workers.openhands as openhands_module

    payloads: list[dict[str, object]] = []

    def fake_request(method: str, url: str, payload=None, timeout=30, headers=None):
        del method, url, timeout, headers
        payloads.append(payload)
        return {"run_id": "openhands-run-01"}

    monkeypatch.setattr(openhands_module, "_json_request", fake_request)

    OpenHandsWorkerAdapter(
        endpoint="http://openhands.local",
        env={"AGOS_WORKER_MODE": "production"},
    ).start(
        WorkerStartRequest(
            run_id="execution-run-01",
            subtask_id="subtask-01",
            prompt="Do the work",
            workspace_path=str(tmp_path),
        )
    )

    assert payloads[0]["env"] == {"AGOS_WORKER_MODE": "production"}
