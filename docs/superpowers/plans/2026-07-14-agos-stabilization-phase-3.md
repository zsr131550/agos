# AGOS Stabilization Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route CLI and Dashboard task creation through one compatible `TaskExecutionService`, add deterministic offline candidate execution, and stop requiring `outputs/<task-id>` for source-code tasks.

**Architecture:** Add a thin application service above the existing legacy executor adapters and `ExecutionService` candidate pipeline. Persist explicit mode/output-contract metadata only for new tasks while interpreting missing fields as v0.1 legacy behavior. Keep provider construction in the CLI registry boundary, and add a synchronous argv-based worktree worker for provider-free automation and end-to-end verification.

**Tech Stack:** Python 3.11+, Pydantic v2, Typer, standard-library subprocess/HTTP/filesystem APIs, Git worktrees, pytest.

## Global Constraints

- Preserve every current CLI command, option, Dashboard request field, and readable `.agos` model.
- Existing configs without `task_execution` resolve to `mode: legacy` and `output_contract: legacy`.
- Existing task YAML without new fields remains readable and retains output-directory enforcement.
- `candidate` mode never bypasses candidate tests, review, accepted decision, or guarded apply.
- Required tests and the deterministic command worker use no model-provider credentials or network calls.
- Provider adapters remain optional integrations outside `agos.core`.
- Command workers use structured argv with `shell=False`; never interpret a command through a shell.
- Maintain the 90 percent coverage threshold.

---

### Task 1: Compatible Execution Models

**Files:**
- Create: `src/agos/core/task_execution.py`
- Modify: `src/agos/core/config.py`
- Modify: `src/agos/core/task.py`
- Modify: `tests/core/test_config.py`
- Modify: `tests/core/test_task.py`
- Create: `tests/core/test_task_execution.py`

**Interfaces:**
- Produces: `ExecutionMode`, `OutputContract`, `TaskExecutionConfig`, `ExecutorSelection`, `TaskExecutionRequest`, and `TaskExecutionResult`.
- Persists optional `Task.execution_mode` and `Task.output_contract`.
- Produces `effective_task_mode(task)` and `task_requires_output_directory(task)`.

- [ ] **Step 1: Add failing compatibility tests**

```python
def test_old_config_defaults_to_legacy_execution():
    config = AGOSConfig.model_validate({"executor": {"agent": "Lambda"}})
    assert config.task_execution.mode == "legacy"
    assert config.task_execution.output_contract == "legacy"


def test_old_task_keeps_legacy_output_contract(old_task):
    assert old_task.execution_mode is None
    assert old_task.output_contract is None
    assert effective_task_mode(old_task) == "legacy"
    assert task_requires_output_directory(old_task) is True


def test_source_code_task_does_not_require_output_directory(task):
    task = task.model_copy(update={"output_contract": "source_code"})
    assert task_requires_output_directory(task) is False
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/core/test_config.py tests/core/test_task.py \
  tests/core/test_task_execution.py -q
```

Expected: FAIL because execution models and task fields do not exist.

- [ ] **Step 3: Implement legacy-safe models**

```python
ExecutionMode = Literal["legacy", "candidate"]
OutputContract = Literal["legacy", "source_code", "standalone"]
TaskExecutionState = Literal["running", "completed", "blocked", "failed", "stuck"]


class TaskExecutionConfig(BaseModel):
    mode: ExecutionMode = "legacy"
    output_contract: OutputContract = "legacy"


class ExecutorSelection(BaseModel):
    adapter: str
    agent: str
    command: str | None = None
    worker_adapter: str | None = None


class TaskExecutionRequest(BaseModel):
    title: str
    intent: str = ""
    workflow: str | None = None
    gate_overrides: list[str] = Field(default_factory=list)
    mode: ExecutionMode | None = None
    executor_selection: ExecutorSelection | None = None
    apply: bool = True


class TaskExecutionResult(BaseModel):
    task_id: str
    mode: ExecutionMode
    run_id: str
    state: TaskExecutionState
    issue_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    applied_candidate_ids: list[str] = Field(default_factory=list)
    blocked_stage: str | None = None
    blocked_reason: str | None = None
    compatibility_warnings: list[str] = Field(default_factory=list)
```

Add `task_execution: TaskExecutionConfig = Field(default_factory=TaskExecutionConfig)` to `AGOSConfig`. Add optional task fields so old YAML remains distinguishable from new explicit metadata. Interpret missing task fields as legacy through helpers rather than rewriting archives.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest tests/core/test_config.py tests/core/test_task.py \
  tests/core/test_task_execution.py -q
git add src/agos/core/task_execution.py src/agos/core/config.py src/agos/core/task.py \
  tests/core/test_config.py tests/core/test_task.py tests/core/test_task_execution.py
git commit -m "feat: define compatible task execution modes"
```

---

### Task 2: Correct the Output Contract

**Files:**
- Modify: `src/agos/adapters/local_cli_executor.py`
- Modify: `src/agos/web/api.py`
- Modify: `tests/adapters/test_local_cli_executor.py`
- Modify: `tests/web/test_api.py`

**Interfaces:**
- Consumes `Task.output_contract` and `task_requires_output_directory(task)`.
- Source-code completion requires a non-empty, valid governed repository change made during the run.
- Legacy and standalone behavior remains unchanged.

- [ ] **Step 1: Add failing contract tests**

```python
def test_source_code_executor_accepts_repo_edit_without_outputs(monkeypatch, tmp_repo):
    task = _task(output_contract="source_code")
    adapter = _executor_that_changes_readme(monkeypatch, tmp_repo)
    run = adapter.start(task)
    assert adapter.status(run.run_id).state == "completed"
    assert not (tmp_repo / "outputs" / task.id).exists()


def test_source_code_executor_rejects_no_repo_change(monkeypatch, tmp_repo):
    status = _successful_process_without_changes(monkeypatch, tmp_repo, output_contract="source_code")
    assert status.state == "failed"
    assert "without changing governed source files" in status.detail


def test_old_task_still_requires_outputs_directory(monkeypatch, tmp_repo):
    monkeypatch.setattr(
        "agos.adapters.local_cli_executor.run_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "answered only", ""),
    )
    adapter = _TestExecutor(evidence_dir=tmp_repo / ".agos/evidence", cwd=tmp_repo)
    run = adapter.start(_task())
    assert adapter.status(run.run_id).state == "failed"
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/adapters/test_local_cli_executor.py \
  tests/web/test_api.py -q
```

- [ ] **Step 3: Implement contract-specific completion**

For `legacy` and `standalone`, retain current output directory, retry, prompt, and status behavior. For `source_code`, capture a repository-change fingerprint before dispatch (excluding `.agos/`), require a different post-run fingerprint plus `git diff --check`, retry once with a source-specific directive, and never create `outputs/<task-id>`. Update Dashboard business-output detection to use the persisted task contract, candidate/patch evidence, and governed Git changes.

- [ ] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest tests/adapters/test_local_cli_executor.py \
  tests/cli/test_start.py tests/cli/test_checkpoint.py tests/web/test_api.py -q
git add src/agos/adapters/local_cli_executor.py src/agos/web/api.py \
  tests/adapters/test_local_cli_executor.py tests/web/test_api.py
git commit -m "fix: distinguish source and standalone outputs"
```

---

### Task 3: Deterministic Offline Command Worker

**Files:**
- Create: `src/agos/adapters/workers/command.py`
- Modify: `src/agos/adapters/workers/__init__.py`
- Modify: `src/agos/core/config.py`
- Modify: `src/agos/cli/worker_registry.py`
- Create: `tests/adapters/test_command_worker.py`
- Modify: `tests/cli/test_worker_registry.py`

**Interfaces:**
- Config: `workers.<name>.type: command` with required non-empty `argv: list[str]`.
- Produces `CommandWorkerAdapter(name, argv, workspace_manager, timeout_seconds, env)`.
- Runs explicit argv in the isolated worktree with `shell=False` and `stdin=DEVNULL`.

- [ ] **Step 1: Add failing adapter tests**

```python
def test_command_worker_requires_nonempty_argv():
    with pytest.raises(ValueError, match="argv"):
        WorkerConfig(type="command", argv=[])


def test_command_worker_runs_in_isolated_workspace(tmp_repo):
    adapter, request = _command_worker(tmp_repo, [sys.executable, "edit.py"])
    run = adapter.start(request)
    assert run.state == "completed"
    assert Path(request.workspace_path, "README.md").read_text() == "# offline\n"
    assert adapter.export_candidate(_handle(request))["patch_bytes"].strip()


def test_command_worker_never_uses_shell(monkeypatch, tmp_repo):
    captured = _capture_run_command(monkeypatch)
    _start_command_worker(tmp_repo)
    assert captured.get("shell") in {None, False}
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/adapters/test_command_worker.py \
  tests/cli/test_worker_registry.py -q
```

- [ ] **Step 3: Implement and register**

Use `LocalWorktreeWorkerAdapter`'s prepare/export pattern. Cache synchronous terminal status by run ID. Map return code zero to `completed`; nonzero, timeout, and OSError to `failed`. Health checks only local executable availability. Register only explicit `type: command`; do not make it a hidden fallback.

- [ ] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest tests/adapters/test_command_worker.py \
  tests/adapters/test_worker_adapters.py tests/cli/test_worker_registry.py -q
git add src/agos/adapters/workers/command.py src/agos/adapters/workers/__init__.py \
  src/agos/core/config.py src/agos/cli/worker_registry.py \
  tests/adapters/test_command_worker.py tests/cli/test_worker_registry.py
git commit -m "feat: add offline command worker"
```

---

### Task 4: Unified TaskExecutionService

**Files:**
- Create: `src/agos/core/task_execution_service.py`
- Modify: `src/agos/core/execution_pipeline.py`
- Modify: `src/agos/core/execution_service.py`
- Create: `tests/core/test_task_execution_service.py`
- Modify: `tests/core/test_execution_pipeline.py`

**Interfaces:**
- Produces `TaskExecutionService.start(request) -> TaskExecutionResult`.
- Constructor receives a legacy executor factory, candidate runner, and side-effect-free readiness checker.
- Persists `task_execution_started`, a mode-specific terminal event, status, and `execution/task-execution.json`.
- Extends `run_auto_execution(service, *, apply=False, resume_run_id: str | None = None)` idempotently.

- [ ] **Step 1: Add failing service tests**

```python
def test_readiness_failure_publishes_no_task(tmp_repo):
    service = _service(tmp_repo, readiness=["automatic reviewer missing"])
    with pytest.raises(TaskExecutionError, match="automatic reviewer missing"):
        service.start(TaskExecutionRequest(title="Change", mode="candidate"))
    assert not repo_paths(tmp_repo).task_yaml.exists()


def test_legacy_start_returns_normalized_result(tmp_repo):
    result = _service(tmp_repo).start(TaskExecutionRequest(title="Legacy", mode="legacy"))
    assert result.run_id == "legacy-run-1"
    assert _event_types(tmp_repo)[:4] == [
        "task_started", "gates_locked", "task_execution_started", "executor_dispatched"
    ]


def test_candidate_start_applies_guarded_candidate(tmp_repo):
    result = _candidate_service(tmp_repo).start(
        TaskExecutionRequest(title="Change README", mode="candidate")
    )
    assert result.state == "completed"
    assert result.candidate_ids == result.applied_candidate_ids
    assert Path(tmp_repo, "README.md").read_text() == "# offline\n"
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/core/test_task_execution_service.py \
  tests/core/test_execution_pipeline.py -q
```

- [ ] **Step 3: Implement validation, staging, and dispatch**

Validate active-task absence, config, workflow, gate selection, requested mode, and candidate readiness before staging. Stage `task.yaml`, ledger, and status, then atomically rename. Legacy retains pre-publication adapter dispatch. Candidate publishes initial state, invokes `run_auto_execution(service, apply=True)`, appends a terminal event, and normalizes status/result. Preserve a published candidate task and exact blocked stage/reason on failures.

Reject an empty export before candidate metadata:

```python
if not patch_bytes.strip():
    raise ValueError(f"worker produced an empty candidate patch: {subtask_id}")
```

- [ ] **Step 4: Add idempotent candidate resume**

When `resume_run_id` is supplied, load the stored plan and tick its existing runtime. Reuse candidates already associated with completed subtasks. Already applied candidates populate normalized IDs without another review/decision/apply; only newly completed subtasks continue through the guarded path.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m pytest tests/core/test_task_execution_service.py \
  tests/core/test_execution_pipeline.py tests/core/test_execution_service.py -q
git add src/agos/core/task_execution_service.py src/agos/core/execution_pipeline.py \
  src/agos/core/execution_service.py tests/core/test_task_execution_service.py \
  tests/core/test_execution_pipeline.py
git commit -m "feat: unify task execution entrypoint"
```

---

### Task 5: CLI, Init, and Compatibility Wiring

**Files:**
- Create: `src/agos/cli/task_execution_registry.py`
- Modify: `src/agos/cli/cmd_start.py`
- Modify: `src/agos/cli/cmd_init.py`
- Modify: `tests/cli/test_start.py`
- Modify: `tests/cli/test_init.py`
- Modify: `tests/cli/test_run_auto.py`

**Interfaces:**
- CLI: `agos start --mode legacy|candidate [--json]`.
- Omitted mode uses config; old config output stays the legacy issue/run ID.
- `agos run auto` remains registered as compatibility/advanced control.

- [ ] **Step 1: Add failing CLI tests**

```python
def test_start_help_exposes_mode_and_json():
    result = runner.invoke(app, ["start", "--help"])
    assert "--mode" in result.stdout
    assert "--json" in result.stdout


def test_start_without_mode_preserves_old_stdout(monkeypatch, tmp_repo):
    _write_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.executor_registry.MulticaAdapter.start",
        lambda self, task: ExecutorRun(adapter="multica", run_id="run-77", issue_id="AGO-77"),
    )
    result = runner.invoke(app, ["start", "--title", "Compatible"])
    assert result.stdout.strip() == "AGO-77"


def test_start_candidate_json_is_normalized(monkeypatch, tmp_repo):
    _write_candidate_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr(
        "agos.cli.cmd_start.build_task_execution_service",
        lambda _root: _FakeTaskExecutionService(candidate_result()),
    )
    result = runner.invoke(app, ["start", "--title", "Candidate", "--json"])
    payload = json.loads(result.stdout)
    assert payload["mode"] == "candidate"
    assert payload["candidate_ids"] == payload["applied_candidate_ids"]
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/cli/test_start.py tests/cli/test_init.py \
  tests/cli/test_run_auto.py -q
```

- [ ] **Step 3: Wire the service at the CLI boundary**

Construct legacy/provider adapters, workers, planner, and reviewers in `task_execution_registry.py`. `cmd_start` calls the service, prints the old legacy ID for old human calls, the candidate run ID for candidate human calls, and full normalized JSON for `--json`. Emit compatibility warnings to stderr without changing successful exit codes. Keep a `start_task()` legacy wrapper for import compatibility.

- [ ] **Step 4: Make new init choose a ready mode**

For selected Codex/Claude adapters, configure one required local CLI reviewer and write `candidate/source_code`. When automatic worker/reviewer readiness is structurally missing (for example Multica-only), write `legacy/legacy` and print the reason. Never contact a provider merely to choose the mode.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m pytest tests/cli/test_start.py tests/cli/test_init.py \
  tests/cli/test_run_auto.py tests/cli/test_execute_plan_runtime.py -q
git add src/agos/cli/task_execution_registry.py src/agos/cli/cmd_start.py \
  src/agos/cli/cmd_init.py tests/cli/test_start.py tests/cli/test_init.py \
  tests/cli/test_run_auto.py
git commit -m "feat: expose compatible task execution modes"
```

---

### Task 6: Unified Dashboard Start and Lifecycle

**Files:**
- Modify: `src/agos/web/api.py`
- Modify: `src/agos/web/static/index.html`
- Modify: `tests/web/test_api.py`
- Modify: `tests/web/test_server.py`
- Modify: `tests/web/test_static_resources.py`

**Interfaces:**
- POST `/api/runs` accepts optional `mode` without changing existing fields.
- Response retains `run_id`, `issue_id`, `run`, and `current`, and adds `execution_result`.
- Candidate resume/restart uses candidate runtime resume, never legacy redispatch.

- [ ] **Step 1: Add failing Dashboard tests**

```python
def test_dashboard_start_passes_candidate_mode_to_service(monkeypatch, tmp_repo):
    captured = _capture_execution_request(monkeypatch)
    payload = start_run_payload(tmp_repo, {"title": "Change", "mode": "candidate"})
    assert captured.mode == "candidate"
    assert payload["execution_result"]["mode"] == "candidate"


def test_dashboard_rejects_unknown_mode_without_state(tmp_repo):
    with pytest.raises(DashboardApiError, match="mode"):
        start_run_payload(tmp_repo, {"title": "Change", "mode": "unknown"})
    assert not repo_paths(tmp_repo).task_yaml.exists()


def test_candidate_resume_never_dispatches_legacy_executor(monkeypatch, candidate_repo):
    monkeypatch.setattr("agos.cli.executor_registry.MulticaAdapter.start", _fail)
    assert resume_current_task_payload(candidate_repo)["execution_result"]["mode"] == "candidate"
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/web/test_api.py tests/web/test_server.py \
  tests/web/test_static_resources.py -q
```

- [ ] **Step 3: Replace Dashboard dispatch**

Preserve agent, gates, and `replace_active`; validate optional mode; call the same registry-built service. Old/legacy lifecycle retains existing behavior. Candidate pause/cancel and resume/restart use persisted normalized run state; return a structured business error if a terminal governed run cannot safely restart. Add a compact mode selector to the existing form without redesigning the Dashboard.

- [ ] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest tests/web/test_api.py tests/web/test_server.py \
  tests/web/test_static_resources.py tests/cli/test_start.py -q
git add src/agos/web/api.py src/agos/web/static/index.html tests/web/test_api.py \
  tests/web/test_server.py tests/web/test_static_resources.py
git commit -m "feat: unify dashboard task execution"
```

---

### Task 7: Offline E2E, Documentation, and Verification

**Files:**
- Create: `tests/integration/test_offline_task_execution.py`
- Create: `docs/execution-modes.md`
- Modify: `README.md`
- Modify only on regression: source/tests from Tasks 1-6.

**Interfaces:**
- Proves task creation -> command worker -> real patch -> gates -> deterministic review -> accepted decision -> guarded apply -> merge-gate.
- Documents mode/output migration and offline boundaries.

- [ ] **Step 1: Add the real offline E2E test**

Create a committed temporary Git repository configured with `candidate/source_code`, a `command` worker using `sys.executable -c` to change `README.md`, a fake required reviewer with `allow_fake_reviewer: true`, deterministic gates, and disabled planner. Invoke `agos start --mode candidate --json`. Assert a non-empty `worker_export` patch, passing gates, completed review, accepted decision, applied candidate, changed root file, valid ledger, and `verify_merge_gate(paths).decision == "pass"`.

- [ ] **Step 2: Run provider-free E2E**

```bash
PATH="$PWD/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" PYTHONPATH="$PWD/src" \
.venv/bin/python -m pytest tests/integration/test_offline_task_execution.py \
  tests/cli/test_start.py tests/web/test_server.py -q
```

- [ ] **Step 3: Document operation and migration**

Document omitted config as `legacy/legacy`, one-run `--mode` overrides, guarded candidate apply, the retained `agos run auto`, `source_code` versus `standalone`, explicit local argv command workers, and the development-only fake reviewer boundary.

- [ ] **Step 4: Run full verification**

```bash
.venv/bin/python -m ruff check src tests
.venv/bin/python -m compileall -q src tests
git diff --check
PATH="$PWD/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
PYTHONPATH="/Users/zhangrui/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages:$PWD/src" \
.venv/bin/python -m pytest --cov=agos --cov-report=term-missing -q
PYTHONPATH="/Users/zhangrui/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages:$PWD/src" \
.venv/bin/python -m build --no-isolation
```

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/execution-modes.md tests/integration/test_offline_task_execution.py
git commit -m "docs: explain unified offline execution modes"
```
