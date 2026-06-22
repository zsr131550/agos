from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError

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
