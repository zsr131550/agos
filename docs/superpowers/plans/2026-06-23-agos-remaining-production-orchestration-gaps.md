# AGOS Remaining Production Orchestration Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining AGOS production orchestration gaps: real worker adapter readiness, production-grade multi-worker scheduling and recovery, real LangGraph/external backend integration, richer merge arbitration, and an end-to-end multi-agent runtime path.

**Architecture:** Preserve AGOS as the governance layer. Core modules define normalized lifecycle models, runtime state machines, merge decisions, and evidence contracts; concrete worker/reviewer/orchestrator integrations remain behind adapters and CLI registries. The native runtime is the reference implementation; LangGraph and external backends must execute the same graph semantics without becoming required dependencies.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, pytest, stdlib `subprocess`/`urllib`, optional `langgraph`, filesystem JSON state, deterministic fake adapters, opt-in integration tests for Codex/Multica/OpenHands/external orchestrators.

---

## Current State As Of 2026-06-23

Implemented on branch `codex/execution-orchestration`:

- `ExecutionWorkerAdapter` lifecycle models and mock-tested Codex/Multica/OpenHands boundaries.
- `ReviewerAdapter` plus parallel reviewer orchestration.
- Unified `OrchestrationBackend` lifecycle: `start/poll/cancel/collect/run`.
- Recoverable execution runtime skeleton with persisted attempts.
- Optional LangGraph backend that compiles and invokes a graph when dependency is installed.
- External HTTP backend with normalized POST/GET/cancel/artifact calls.

Still incomplete:

- Codex/Multica/OpenHands adapters are not yet production-ready worker connections: no health checks, opt-in real smoke tests, consistent timeout/env handling, or artifact import contract.
- `GraphRuntime` starts nodes but does not fully poll, collect, retry, cancel through node backends, or wire worker/reviewer/arbiter results into one graph lifecycle.
- `ExecutionRuntime` has basic retry/cancel state but not production failure recovery, timeout, backoff, duplicate-start protection, or run-level status persistence.
- LangGraph backend still uses placeholder node actions instead of dispatching actual normalized worker/reviewer/arbiter nodes.
- External backend has an HTTP boundary but lacks local fake-server integration tests, idempotency/error contract, and documented remote orchestrator schema.
- Merge arbiter is moving toward bundle decisions, but automatic multi-candidate stack merge and conflict evidence are not complete.

## File Structure

- Modify: `src/agos/core/config.py`
  - Add worker runtime fields: `timeout_seconds`, `poll_interval_seconds`, `env`, `artifact_globs`.
  - Add orchestration runtime fields: `backend`, `max_parallel`, `max_retries`, `worker_timeout_seconds`, `retry_backoff_seconds`.
- Modify: `src/agos/core/execution_worker.py`
  - Add optional health/artifact models while preserving existing `WorkerRunStatus.output_refs`.
- Modify: `src/agos/adapters/workers/codex_cli.py`
  - Harden command execution, timeout/env handling, JSON validation, and opt-in smoke support.
- Modify: `src/agos/adapters/workers/multica_worker.py`
  - Harden issue/run lifecycle, cancellation target, status mapping, and opt-in smoke support.
- Modify: `src/agos/adapters/workers/openhands.py`
  - Harden HTTP errors, timeout handling, artifact refs, and opt-in smoke support.
- Modify: `src/agos/cli/worker_registry.py`
  - Wire new worker config fields into concrete adapters.
- Modify: `src/agos/core/execution_runtime.py`
  - Add duplicate-start protection, retry backoff, timeout, run status persistence, and terminal reason tracking.
- Modify: `src/agos/core/orchestration/models.py`
  - Add node run status/handle models for polling worker/reviewer/arbiter nodes.
- Modify: `src/agos/core/orchestration/protocols.py`
  - Extend node backends from start-only to lifecycle-capable backends.
- Modify: `src/agos/core/orchestration/graph_runtime.py`
  - Poll running nodes, collect output refs, retry recoverable failures, cancel through backends, and persist graph status.
- Create: `src/agos/core/orchestration/node_backends.py`
  - Adapter wrappers that expose worker/reviewer/arbiter adapters as graph node backends.
- Modify: `src/agos/backends/langgraph_backend.py`
  - Dispatch real node actions through normalized node backend lifecycle instead of placeholder `visited_nodes` actions.
- Modify: `src/agos/backends/external_backend.py`
  - Add versioned payload contract, idempotency key, HTTP error normalization, and fake-server integration coverage.
- Modify: `src/agos/core/arbiters.py`
  - Add ordered patch stack strategy and conflict evidence decisions.
- Modify: `src/agos/core/execution.py`
  - Add merge preview/stack evidence models.
- Modify: `src/agos/core/execution_store.py`
  - Persist merge previews, stack apply attempts, and conflict evidence.
- Modify: `src/agos/core/execution_service.py`
  - Apply candidate bundles/stacks through one guarded path and run stack dry-runs in temporary workspaces.
- Modify: `src/agos/cli/cmd_candidate.py`
  - Expose `candidate merge preview`, `candidate merge decide`, and `candidate merge apply`.
- Modify: `src/agos/cli/cmd_execute_plan.py`
  - Use configured orchestration runtime policy and print machine-readable status with `--json`.
- Modify: `README.md`
  - Document production worker config, reviewer config, orchestration config, external backend contract, and merge strategies.
- Modify: `docs/superpowers/specs/2026-06-22-agos-multi-agent-orchestration-design.md`
  - Update lifecycle interfaces and runtime commitments.
- Tests:
  - `tests/core/test_execution_runtime_recovery.py`
  - `tests/core/test_graph_runtime_lifecycle.py`
  - `tests/core/test_orchestration_node_backends.py`
  - `tests/core/test_external_backend_http.py`
  - `tests/integration/test_external_backend_server.py`
  - `tests/integration/test_langgraph_backend.py`
  - `tests/integration/test_worker_adapters_opt_in.py`
  - `tests/integration/test_multi_agent_runtime.py`
  - `tests/core/test_merge_arbiter.py`
  - `tests/cli/test_candidate_merge.py`

---

## Implementation Status As Of 2026-06-23

Tasks 0-8 have landed on `codex/execution-orchestration`:

| Task | Status |
|---|---|
| Task 0: merge bundle baseline | Implemented in `c17e993`. |
| Task 1: production worker adapter config | Implemented in `d8eee4c`. |
| Task 2: ExecutionRuntime recovery policy | Implemented in `9e5c3bd`. |
| Task 3: lifecycle-capable graph runtime | Implemented in `e070306`. |
| Task 4: worker/reviewer/arbiter node wrappers | Implemented in `9c8960b`. |
| Task 5: LangGraph normalized dispatch | Implemented in `41b5f81`. |
| Task 6: external backend remote contract | Implemented in `c4e8dfd`. |
| Task 7: ordered patch stack merge strategy | Implemented in `3cbe207`. |
| Task 8: end-to-end runtime wiring | Implemented in `4a8863a`. |

Task 9 is the final documentation and verification pass.

## Task 0: Close The In-Flight Merge Bundle Work

**Files:**
- Modify: `src/agos/core/execution.py`
- Modify: `src/agos/core/arbiters.py`
- Modify: `src/agos/core/execution_store.py`
- Modify: `src/agos/core/execution_service.py`
- Modify: `src/agos/cli/cmd_candidate.py`
- Test: `tests/core/test_merge_arbiter.py`
- Test: `tests/cli/test_candidate_merge.py`

- [ ] **Step 1: Confirm the working tree contains only expected Task 7 files**

Run:

```powershell
git status --short
```

Expected output contains only:

```text
 M src/agos/cli/cmd_candidate.py
 M src/agos/core/arbiters.py
 M src/agos/core/execution.py
 M src/agos/core/execution_service.py
 M src/agos/core/execution_store.py
?? tests/cli/test_candidate_merge.py
?? tests/core/test_merge_arbiter.py
?? docs/superpowers/plans/2026-06-23-agos-remaining-production-orchestration-gaps.md
```

- [ ] **Step 2: Run merge tests with workspace-local temp**

Run:

```powershell
New-Item -ItemType Directory -Force E:\AGOS_V2\.tmp | Out-Null
$env:TMP='E:\AGOS_V2\.tmp'
$env:TEMP='E:\AGOS_V2\.tmp'
python -m pytest tests/core/test_merge_arbiter.py tests/cli/test_candidate_merge.py -q
```

Expected: both tests pass. A pytest cache warning is acceptable if it only references cache write permissions and the test result is passing.

- [ ] **Step 3: Run candidate regressions**

Run:

```powershell
$env:TMP='E:\AGOS_V2\.tmp'
$env:TEMP='E:\AGOS_V2\.tmp'
python -m pytest tests/core/test_execution_service.py tests/cli/test_candidate.py -q
python -m ruff check src tests
```

Expected: tests and ruff pass.

- [ ] **Step 4: Commit the merge bundle baseline**

Run:

```powershell
git add src/agos/core/execution.py src/agos/core/arbiters.py src/agos/core/execution_store.py src/agos/core/execution_service.py src/agos/cli/cmd_candidate.py tests/core/test_merge_arbiter.py tests/cli/test_candidate_merge.py
git commit -m "feat: add merge arbiter bundle strategies"
```

Expected: commit succeeds and leaves only this plan file uncommitted if the plan is not committed with Task 0.

---

## Task 1: Production-Ready Worker Adapter Contract

**Files:**
- Modify: `src/agos/core/config.py`
- Modify: `src/agos/core/execution_worker.py`
- Modify: `src/agos/adapters/workers/codex_cli.py`
- Modify: `src/agos/adapters/workers/multica_worker.py`
- Modify: `src/agos/adapters/workers/openhands.py`
- Modify: `src/agos/cli/worker_registry.py`
- Test: `tests/adapters/test_worker_adapters.py`
- Test: `tests/cli/test_worker_registry.py`
- Create: `tests/integration/test_worker_adapters_opt_in.py`

- [ ] **Step 1: Write config tests for production worker fields**

Add this test to `tests/cli/test_worker_registry.py`:

```python
def test_worker_registry_passes_runtime_fields(tmp_path):
    repo = tmp_path
    agos_dir = repo / ".agos"
    agos_dir.mkdir()
    (agos_dir / "agos.yaml").write_text(
        """
executor:
  name: multica
  agent: Lambda
workers:
  codex-prod:
    type: codex_cli
    command: codex
    timeout_seconds: 120
    poll_interval_seconds: 2
    artifact_globs:
      - .agos-worker/*.json
    env:
      AGOS_WORKER_MODE: production
workflows:
  feature:
    gates: []
""",
        encoding="utf-8",
    )

    service = ExecutionService(repo_paths(repo))
    register_configured_worker_adapters(service)

    adapter = service._worker_adapters["codex-prod"]
    assert adapter.timeout_seconds == 120
    assert adapter.poll_interval_seconds == 2
    assert adapter.artifact_globs == (".agos-worker/*.json",)
    assert adapter.env == {"AGOS_WORKER_MODE": "production"}
```

- [ ] **Step 2: Run the new registry test and verify RED**

Run:

```powershell
python -m pytest tests/cli/test_worker_registry.py::test_worker_registry_passes_runtime_fields -q
```

Expected: fail because `WorkerConfig` and adapters do not expose the new runtime fields.

- [ ] **Step 3: Add worker config fields**

Modify `WorkerConfig` in `src/agos/core/config.py`:

```python
class WorkerConfig(BaseModel):
    """One configured execution worker adapter."""

    type: str
    command: str | None = None
    agent: str | None = None
    endpoint: str | None = None
    token: str | None = None
    timeout_seconds: int = Field(default=30, ge=1)
    poll_interval_seconds: int = Field(default=1, ge=1)
    artifact_globs: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
```

- [ ] **Step 4: Add adapter constructor fields**

Each production worker adapter constructor must accept and store:

```python
timeout_seconds: int = 30
poll_interval_seconds: int = 1
artifact_globs: tuple[str, ...] | list[str] = ()
env: dict[str, str] | None = None
```

Concrete storage shape:

```python
self.timeout_seconds = timeout_seconds
self.poll_interval_seconds = poll_interval_seconds
self.artifact_globs = tuple(artifact_globs)
self.env = dict(env or {})
```

For command adapters, pass environment to `run_command`:

```python
env={**os.environ, **self.env}
```

For HTTP adapters, use `timeout=self.timeout_seconds`.

- [ ] **Step 5: Wire config fields in the worker registry**

In `src/agos/cli/worker_registry.py`, every adapter construction must pass:

```python
timeout_seconds=worker.timeout_seconds,
poll_interval_seconds=worker.poll_interval_seconds,
artifact_globs=worker.artifact_globs,
env=worker.env,
```

For `OpenHandsWorkerAdapter`, pass `timeout=worker.timeout_seconds` if the constructor keeps the existing `timeout` parameter name.

- [ ] **Step 6: Add opt-in integration tests**

Create `tests/integration/test_worker_adapters_opt_in.py`:

```python
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agos.adapters.workers.codex_cli import CodexWorkerAdapter
from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
from agos.core.execution_worker import WorkerStartRequest


def _request(tmp_path: Path) -> WorkerStartRequest:
    return WorkerStartRequest(
        run_id="agos-smoke-run",
        subtask_id="agos-smoke-subtask",
        prompt="Return a JSON status for an AGOS adapter smoke test.",
        workspace_path=str(tmp_path),
        metadata={"smoke": "true"},
    )


@pytest.mark.skipif(os.getenv("AGOS_CODEX_WORKER_SMOKE") != "1", reason="opt-in real Codex worker smoke")
def test_codex_worker_smoke(tmp_path):
    adapter = CodexWorkerAdapter(command=os.getenv("AGOS_CODEX_BIN", "codex"), timeout_seconds=120)
    run = adapter.start(_request(tmp_path))
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)
    assert status.backend == adapter.name
    assert status.run_id == run.run_id


@pytest.mark.skipif(os.getenv("AGOS_MULTICA_WORKER_SMOKE") != "1", reason="opt-in real Multica worker smoke")
def test_multica_worker_smoke(tmp_path):
    adapter = MulticaWorkerAdapter(
        multica_bin=os.getenv("AGOS_MULTICA_BIN", "multica"),
        agent=os.getenv("AGOS_MULTICA_AGENT", "Lambda"),
        timeout_seconds=120,
    )
    run = adapter.start(_request(tmp_path))
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)
    assert status.backend == adapter.name
    assert status.run_id == run.run_id


@pytest.mark.skipif(os.getenv("AGOS_OPENHANDS_WORKER_SMOKE") != "1", reason="opt-in real OpenHands worker smoke")
def test_openhands_worker_smoke(tmp_path):
    endpoint = os.environ["AGOS_OPENHANDS_ENDPOINT"]
    adapter = OpenHandsWorkerAdapter(
        endpoint=endpoint,
        token=os.getenv("AGOS_OPENHANDS_TOKEN"),
        timeout=120,
    )
    run = adapter.start(_request(tmp_path))
    status = adapter.poll(run.run_id, subtask_id=run.subtask_id)
    assert status.backend == adapter.name
    assert status.run_id == run.run_id
```

- [ ] **Step 7: Run worker adapter verification**

Run:

```powershell
python -m pytest tests/adapters/test_worker_adapters.py tests/cli/test_worker_registry.py tests/integration/test_worker_adapters_opt_in.py -q
python -m ruff check src/agos/adapters/workers.py src/agos/adapters tests/adapters tests/cli tests/integration
```

Expected: unit tests pass; opt-in tests are skipped unless the corresponding `AGOS_*_SMOKE=1` environment variables are set.

- [ ] **Step 8: Commit Task 1**

Run:

```powershell
git add src/agos/core/config.py src/agos/core/execution_worker.py src/agos/adapters/workers src/agos/cli/worker_registry.py tests/adapters/test_worker_adapters.py tests/cli/test_worker_registry.py tests/integration/test_worker_adapters_opt_in.py
git commit -m "feat: harden production worker adapter configuration"
```

---

## Task 2: Production Failure Recovery In ExecutionRuntime

**Files:**
- Modify: `src/agos/core/execution_runtime.py`
- Modify: `src/agos/core/execution_service.py`
- Modify: `src/agos/core/config.py`
- Test: `tests/core/test_execution_runtime_recovery.py`
- Test: `tests/cli/test_execute_plan_runtime.py`

- [ ] **Step 1: Write retry, timeout, and duplicate-start tests**

Create `tests/core/test_execution_runtime_recovery.py`:

```python
from __future__ import annotations

from agos.core.execution import ExecutionPlan, ExecutionSubtask, SubtaskWorker
from agos.core.execution_runtime import ExecutionRuntime
from agos.core.execution_worker import WorkerRun, WorkerRunStatus


class FlakyWorker:
    name = "flaky"

    def __init__(self) -> None:
        self.starts = 0
        self.polls = 0

    def start(self, request):
        self.starts += 1
        return WorkerRun(
            backend=self.name,
            run_id=f"{request.run_id}:worker:{self.starts}",
            subtask_id=request.subtask_id,
            state="running",
        )

    def poll(self, run_id: str, *, subtask_id: str):
        self.polls += 1
        state = "failed" if self.polls == 1 else "completed"
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state=state,
            detail=state,
        )

    def cancel(self, run_id: str):
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id="subtask-a",
            state="cancelled",
        )


def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        id="plan-01",
        task_id="agos-01",
        max_parallel=1,
        subtasks=[
            ExecutionSubtask(
                id="subtask-a",
                title="A",
                write_scope=["README.md"],
                worker=SubtaskWorker(adapter="flaky"),
            )
        ],
    )


def test_runtime_retries_failed_attempt_once(tmp_path):
    worker = FlakyWorker()
    runtime = ExecutionRuntime(
        state_dir=tmp_path,
        worker_adapters={"flaky": worker},
        max_retries=1,
        retry_backoff_seconds=0,
    )

    first = runtime.tick(_plan(), run_id="run-01")
    second = runtime.tick(_plan(), run_id="run-01")
    third = runtime.tick(_plan(), run_id="run-01")

    assert first.running_subtasks == ("subtask-a",)
    assert second.failed_subtasks == ("subtask-a",)
    assert third.running_subtasks == ("subtask-a",)
    assert worker.starts == 2


def test_runtime_resume_does_not_duplicate_running_attempt(tmp_path):
    worker = FlakyWorker()
    runtime = ExecutionRuntime(
        state_dir=tmp_path,
        worker_adapters={"flaky": worker},
        max_retries=1,
    )

    runtime.tick(_plan(), run_id="run-01")
    runtime.status(_plan(), run_id="run-01")

    assert worker.starts == 1
```

- [ ] **Step 2: Run runtime recovery tests and verify RED**

Run:

```powershell
python -m pytest tests/core/test_execution_runtime_recovery.py -q
```

Expected: at least the retry test fails because current retry handling never restarts failed attempts correctly.

- [ ] **Step 3: Extend `WorkerAttempt` state**

Modify `WorkerAttempt` in `src/agos/core/execution_runtime.py`:

```python
class WorkerAttempt(BaseModel):
    subtask_id: str
    adapter: str
    worker_run_id: str
    state: WorkerRunState
    attempts: int = 1
    detail: str | None = None
    output_refs: list[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    retry_after: str | None = None
    terminal_reason: str | None = None
```

- [ ] **Step 4: Add runtime policy constructor fields**

Update `ExecutionRuntime.__init__`:

```python
def __init__(
    self,
    *,
    state_dir: Path,
    worker_adapters: dict[str, ExecutionWorkerAdapter],
    workspace_paths: dict[str, str] | None = None,
    max_retries: int = 0,
    retry_backoff_seconds: int = 0,
    worker_timeout_seconds: int | None = None,
) -> None:
    self.state_dir = state_dir
    self.worker_adapters = dict(worker_adapters)
    self.workspace_paths = dict(workspace_paths or {})
    self.max_retries = max_retries
    self.retry_backoff_seconds = retry_backoff_seconds
    self.worker_timeout_seconds = worker_timeout_seconds
```

- [ ] **Step 5: Fix retry eligibility**

Replace the failed-attempt readiness check with this behavior:

```python
def _can_retry(attempt: WorkerAttempt, max_retries: int) -> bool:
    return attempt.state == "failed" and attempt.attempts <= max_retries
```

In `_ready_subtasks`, allow a subtask back into ready state only when `_can_retry(previous, max_retries)` is true.

- [ ] **Step 6: Persist run-level snapshots**

After every `tick`, `status`, and `cancel`, write:

```text
<state_dir>/<run_id>/status.json
```

with:

```python
{
    "run_id": snapshot.run_id,
    "running_subtasks": list(snapshot.running_subtasks),
    "completed_subtasks": list(snapshot.completed_subtasks),
    "failed_subtasks": list(snapshot.failed_subtasks),
    "cancelled_subtasks": list(snapshot.cancelled_subtasks),
}
```

- [ ] **Step 7: Wire config policy into `ExecutionService`**

In `ExecutionService._execution_runtime`, load `.agos/agos.yaml` and pass:

```python
max_retries=config.orchestration.max_retries,
retry_backoff_seconds=config.orchestration.retry_backoff_seconds,
worker_timeout_seconds=config.orchestration.worker_timeout_seconds,
```

Add this config model:

```python
class OrchestrationConfig(BaseModel):
    backend: str = "native_async"
    max_parallel: int = Field(default=1, ge=1)
    max_retries: int = Field(default=0, ge=0)
    worker_timeout_seconds: int | None = Field(default=None, ge=1)
    retry_backoff_seconds: int = Field(default=0, ge=0)
```

Add to `AGOSConfig`:

```python
orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
```

- [ ] **Step 8: Run runtime verification**

Run:

```powershell
python -m pytest tests/core/test_execution_runtime_recovery.py tests/core/test_execution_runtime.py tests/cli/test_execute_plan_runtime.py -q
python -m ruff check src tests
```

Expected: pass.

- [ ] **Step 9: Commit Task 2**

Run:

```powershell
git add src/agos/core/execution_runtime.py src/agos/core/execution_service.py src/agos/core/config.py tests/core/test_execution_runtime_recovery.py tests/cli/test_execute_plan_runtime.py
git commit -m "feat: add recoverable execution runtime policy"
```

---

## Task 3: Lifecycle-Capable Graph Runtime

**Files:**
- Modify: `src/agos/core/orchestration/models.py`
- Modify: `src/agos/core/orchestration/protocols.py`
- Modify: `src/agos/core/orchestration/runtime.py`
- Modify: `src/agos/core/orchestration/graph_runtime.py`
- Create: `tests/core/test_graph_runtime_lifecycle.py`

- [ ] **Step 1: Write graph lifecycle tests**

Create `tests/core/test_graph_runtime_lifecycle.py`:

```python
from __future__ import annotations

from agos.core.orchestration.graph_runtime import GraphRuntime, RuntimePolicy
from agos.core.orchestration.models import AgentJobHandle, NodeRunStatus, NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.registry import OrchestrationRegistry


class PollingBackend:
    name = "backend"

    def __init__(self) -> None:
        self.started: list[str] = []
        self.polls: dict[str, int] = {}
        self.cancelled: list[str] = []

    def start(self, run, node):
        self.started.append(node.id)
        return AgentJobHandle(backend=self.name, job_id=f"job-{node.id}", node_id=node.id, run_id=run.run_id)

    def poll(self, handle):
        count = self.polls.get(handle.node_id, 0) + 1
        self.polls[handle.node_id] = count
        state = "completed" if count >= 1 else "running"
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state=state,
            output_refs={handle.node_id: f"evidence/{handle.node_id}.json"},
        )

    def cancel(self, handle):
        self.cancelled.append(handle.node_id)
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state="cancelled",
        )

    def collect(self, handle):
        return {"output_refs": {handle.node_id: f"evidence/{handle.node_id}.json"}}


def _spec() -> OrchestrationRunSpec:
    return OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=(
            NodeSpec(id="worker-a", kind="worker", backend="backend"),
            NodeSpec(id="review-a", kind="reviewer", backend="backend", depends_on=("worker-a",)),
            NodeSpec(id="arbiter", kind="arbiter", backend="backend", depends_on=("review-a",)),
        ),
    )


def test_graph_runtime_polls_and_unblocks_dependent_nodes(tmp_path):
    backend = PollingBackend()
    registry = OrchestrationRegistry()
    registry.register_worker(backend)
    registry.register_reviewer(backend)
    registry.register_arbiter(backend)
    runtime = GraphRuntime(registry=registry, state_dir=tmp_path, policy=RuntimePolicy(max_parallel=2))

    first = runtime.tick(_spec())
    second = runtime.tick(_spec())
    third = runtime.tick(_spec())

    assert first.running_nodes == ("worker-a",)
    assert second.running_nodes == ("review-a",)
    assert third.running_nodes == ("arbiter",)
    assert backend.started == ["worker-a", "review-a", "arbiter"]
```

- [ ] **Step 2: Run graph lifecycle tests and verify RED**

Run:

```powershell
python -m pytest tests/core/test_graph_runtime_lifecycle.py -q
```

Expected: fail because node backends do not yet expose `poll/cancel/collect` and `GraphRuntime.tick()` does not poll running nodes.

- [ ] **Step 3: Add `NodeRunStatus`**

Modify `src/agos/core/orchestration/models.py`:

```python
NodeRunState = Literal["queued", "running", "waiting", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class NodeRunStatus:
    backend: str
    run_id: str
    node_id: str
    job_id: str
    state: NodeRunState
    detail: str | None = None
    output_refs: dict[str, str] | None = None
```

- [ ] **Step 4: Extend node backend protocols**

Modify `src/agos/core/orchestration/protocols.py` so `WorkerBackend`, `ReviewerBackend`, and `ArbiterBackend` require:

```python
def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle: ...
def poll(self, handle: AgentJobHandle) -> NodeRunStatus: ...
def cancel(self, handle: AgentJobHandle) -> NodeRunStatus: ...
def collect(self, handle: AgentJobHandle) -> dict[str, Any]: ...
```

- [ ] **Step 5: Persist enough state to poll**

Ensure `PersistedNodeState` already has:

```python
backend: str | None
job_id: str | None
output_refs: dict[str, str]
```

If `output_refs` is absent or typed differently, normalize it to:

```python
output_refs: dict[str, str] = Field(default_factory=dict)
```

- [ ] **Step 6: Poll running nodes before scheduling new nodes**

At the start of `GraphRuntime.tick()`, for every persisted state where `state == "running"`:

```python
handle = AgentJobHandle(
    backend=state.backend or node.backend,
    job_id=state.job_id or "",
    node_id=node.id,
    run_id=spec.run_id,
)
status = self._backend_for(node).poll(handle)
```

Persist status:

```python
PersistedNodeState(
    node_id=node.id,
    state=status.state,
    attempts=state.attempts,
    backend=status.backend,
    job_id=status.job_id,
    started_at=state.started_at,
    updated_at=utc_now_iso(),
    output_refs=status.output_refs or {},
    error=status.detail if status.state == "failed" else None,
)
```

- [ ] **Step 7: Delegate cancellation to backends**

In `GraphRuntime.cancel()`, call:

```python
status = self._backend_for(node).cancel(handle)
```

Persist the returned `status.state` and `status.output_refs`.

- [ ] **Step 8: Run graph runtime verification**

Run:

```powershell
python -m pytest tests/core/test_graph_runtime_lifecycle.py tests/core/test_graph_runtime.py tests/core/test_orchestration_registry.py -q
python -m ruff check src tests
```

Expected: pass.

- [ ] **Step 9: Commit Task 3**

Run:

```powershell
git add src/agos/core/orchestration/models.py src/agos/core/orchestration/protocols.py src/agos/core/orchestration/runtime.py src/agos/core/orchestration/graph_runtime.py tests/core/test_graph_runtime_lifecycle.py
git commit -m "feat: poll lifecycle nodes in graph runtime"
```

---

## Task 4: Worker, Reviewer, And Arbiter Node Backend Wrappers

**Files:**
- Create: `src/agos/core/orchestration/node_backends.py`
- Modify: `src/agos/core/orchestration/registry.py`
- Modify: `src/agos/core/review_orchestrator.py`
- Test: `tests/core/test_orchestration_node_backends.py`

- [ ] **Step 1: Write wrapper tests**

Create `tests/core/test_orchestration_node_backends.py`:

```python
from __future__ import annotations

from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.node_backends import WorkerNodeBackend
from agos.core.execution_worker import WorkerRun, WorkerRunStatus


class FakeExecutionWorker:
    name = "fake-worker"

    def start(self, request):
        return WorkerRun(
            backend=self.name,
            run_id=request.run_id,
            subtask_id=request.subtask_id,
            state="running",
        )

    def poll(self, run_id: str, *, subtask_id: str):
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state="completed",
            output_refs=["evidence/worker.json"],
        )

    def cancel(self, run_id: str):
        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id="subtask-a",
            state="cancelled",
        )


def test_worker_node_backend_maps_worker_lifecycle():
    backend = WorkerNodeBackend(FakeExecutionWorker())
    spec = OrchestrationRunSpec(
        run_id="graph-run",
        task_id="agos-01",
        nodes=(NodeSpec(id="worker-a", kind="worker", backend="fake-worker", metadata={"workspace_path": "C:/w"}),),
    )

    handle = backend.start(spec, spec.nodes[0])
    status = backend.poll(handle)

    assert handle.job_id == "graph-run:worker-a"
    assert status.state == "completed"
    assert status.output_refs == {"worker-a": "evidence/worker.json"}
```

- [ ] **Step 2: Run wrapper tests and verify RED**

Run:

```powershell
python -m pytest tests/core/test_orchestration_node_backends.py -q
```

Expected: fail because `node_backends.py` does not exist.

- [ ] **Step 3: Implement `WorkerNodeBackend`**

Create `src/agos/core/orchestration/node_backends.py`:

```python
from __future__ import annotations

from agos.core.execution_worker import ExecutionWorkerAdapter, WorkerStartRequest
from agos.core.orchestration.models import AgentJobHandle, NodeRunStatus, NodeSpec, OrchestrationRunSpec


class WorkerNodeBackend:
    def __init__(self, adapter: ExecutionWorkerAdapter) -> None:
        self.adapter = adapter
        self.name = adapter.name

    def start(self, run: OrchestrationRunSpec, node: NodeSpec) -> AgentJobHandle:
        subtask_id = node.metadata.get("subtask_id", node.id)
        worker_run_id = f"{run.run_id}:{node.id}"
        worker_run = self.adapter.start(
            WorkerStartRequest(
                run_id=worker_run_id,
                subtask_id=subtask_id,
                prompt=node.inputs.get("prompt", node.metadata.get("prompt", "")),
                workspace_path=node.metadata.get("workspace_path", ""),
                metadata={"orchestration_run_id": run.run_id, "node_id": node.id},
            )
        )
        return AgentJobHandle(
            backend=self.name,
            job_id=worker_run.run_id,
            node_id=node.id,
            run_id=run.run_id,
        )

    def poll(self, handle: AgentJobHandle) -> NodeRunStatus:
        status = self.adapter.poll(handle.job_id, subtask_id=handle.node_id)
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state=status.state,
            detail=status.detail,
            output_refs={handle.node_id: status.output_refs[-1]} if status.output_refs else {},
        )

    def cancel(self, handle: AgentJobHandle) -> NodeRunStatus:
        status = self.adapter.cancel(handle.job_id)
        return NodeRunStatus(
            backend=self.name,
            run_id=handle.run_id,
            node_id=handle.node_id,
            job_id=handle.job_id,
            state=status.state,
            detail=status.detail,
        )

    def collect(self, handle: AgentJobHandle) -> dict[str, object]:
        return {"run_id": handle.run_id, "node_id": handle.node_id, "job_id": handle.job_id}
```

- [ ] **Step 4: Add reviewer and arbiter wrappers**

Add:

```python
class ReviewerNodeBackend:
    ...

class ArbiterNodeBackend:
    ...
```

Reviewer behavior:

```text
start -> ReviewerAdapter.start()
poll -> ReviewerAdapter.poll()
cancel -> ReviewerAdapter.cancel()
completed findings output -> output_refs {node_id: raw_ref} when raw_ref exists
```

Arbiter behavior:

```text
start -> deterministic local completed AgentJobHandle
poll -> completed NodeRunStatus
cancel -> cancelled NodeRunStatus
collect -> decision refs from node metadata
```

- [ ] **Step 5: Run wrapper verification**

Run:

```powershell
python -m pytest tests/core/test_orchestration_node_backends.py tests/core/test_graph_runtime_lifecycle.py -q
python -m ruff check src tests
```

Expected: pass.

- [ ] **Step 6: Commit Task 4**

Run:

```powershell
git add src/agos/core/orchestration/node_backends.py src/agos/core/orchestration/registry.py src/agos/core/review_orchestrator.py tests/core/test_orchestration_node_backends.py
git commit -m "feat: wrap agents as graph node backends"
```

---

## Task 5: Real LangGraph Backend Dispatch

**Files:**
- Modify: `src/agos/backends/langgraph_backend.py`
- Modify: `tests/integration/test_langgraph_backend.py`
- Test: `tests/core/test_backend_parity.py`

- [ ] **Step 1: Add a LangGraph dispatch test**

Add to `tests/integration/test_langgraph_backend.py`:

```python
def test_langgraph_backend_dispatches_node_actions_when_available():
    if not LangGraphBackend.is_available():
        pytest.skip("langgraph is not installed")

    calls: list[str] = []

    def dispatch(node, state):
        calls.append(node.id)
        return {"visited_nodes": [node.id], "output_refs": {node.id: f"evidence/{node.id}.json"}}

    backend = LangGraphBackend(node_dispatch=dispatch)
    handle = backend.start(_spec())
    snapshot = backend.collect(handle)

    assert calls == ["worker-01", "reviewer-01", "arbiter-01"]
    assert snapshot["output_refs"]["arbiter-01"] == "evidence/arbiter-01.json"
```

- [ ] **Step 2: Run LangGraph dispatch test and verify RED**

Run:

```powershell
python -m pytest tests/integration/test_langgraph_backend.py::test_langgraph_backend_dispatches_node_actions_when_available -q
```

Expected: skip if `langgraph` is not installed; fail if installed because `LangGraphBackend` does not accept `node_dispatch`.

- [ ] **Step 3: Add `node_dispatch` injection**

Modify `LangGraphBackend.__init__`:

```python
def __init__(self, *, graph_module: LangGraphModule | None = None, node_dispatch=None) -> None:
    self._native = NativeAsyncBackend()
    self._graph_module = graph_module
    self._node_dispatch = node_dispatch or _default_node_dispatch
    self._compiled_runs: dict[str, LangGraphCompiledRun] = {}
    self._completed_runs: dict[str, dict[str, Any]] = {}
```

Add:

```python
def _default_node_dispatch(node: NodeSpec, state: dict[str, Any]) -> dict[str, Any]:
    del state
    return {"visited_nodes": [node.id], "output_refs": {node.id: node.metadata.get("output_ref", "")}}
```

- [ ] **Step 4: Make node actions call dispatch**

Change `_node_action`:

```python
def _node_action(node: NodeSpec, dispatch):
    def action(state: dict[str, Any]) -> dict[str, Any]:
        return dispatch(node, state)

    return action
```

Change `_compile_run` to pass the dispatch function.

- [ ] **Step 5: Merge output refs during collect**

When `graph.invoke()` returns `{"output_refs": {...}}`, store it in `_completed_runs` and expose it from `collect()`.

- [ ] **Step 6: Run LangGraph verification**

Run:

```powershell
python -m pytest tests/integration/test_langgraph_backend.py tests/core/test_backend_parity.py tests/core/test_orchestrator_backend_lifecycle.py -q
python -m ruff check src tests
```

Expected: pass, with LangGraph-specific tests skipped only when the optional dependency is absent.

- [ ] **Step 7: Commit Task 5**

Run:

```powershell
git add src/agos/backends/langgraph_backend.py tests/integration/test_langgraph_backend.py tests/core/test_backend_parity.py
git commit -m "feat: dispatch normalized nodes through langgraph backend"
```

---

## Task 6: External Backend Remote Contract

**Files:**
- Modify: `src/agos/backends/external_backend.py`
- Modify: `README.md`
- Test: `tests/core/test_external_backend_http.py`
- Create: `tests/integration/test_external_backend_server.py`

- [ ] **Step 1: Write fake-server integration test**

Create `tests/integration/test_external_backend_server.py`:

```python
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from agos.backends.external_backend import ExternalBackend
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


class Handler(BaseHTTPRequestHandler):
    runs: dict[str, dict[str, object]] = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if self.path == "/runs":
            run_id = body["run_id"]
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
    finally:
        server.shutdown()
        thread.join(timeout=5)
```

- [ ] **Step 2: Run external fake-server test and verify current behavior**

Run:

```powershell
python -m pytest tests/integration/test_external_backend_server.py -q
```

Expected: pass if the current HTTP path is already correct; if it fails, fix before continuing.

- [ ] **Step 3: Add version and idempotency payload assertions**

Update `ExternalBackend.start()` POST payload to include:

```python
{
    "schema_version": "agos.orchestration.v1",
    "idempotency_key": spec.run_id,
    "spec": spec.model_dump(mode="json"),
}
```

If backwards compatibility tests expect the raw spec at top level, include both:

```python
payload = {
    **spec.model_dump(mode="json"),
    "schema_version": "agos.orchestration.v1",
    "idempotency_key": spec.run_id,
    "spec": spec.model_dump(mode="json"),
}
```

- [ ] **Step 4: Normalize HTTP errors**

Wrap `_json_request` errors and raise:

```python
RuntimeError(f"external orchestrator {method} {url} failed: {message}")
```

The message must include HTTP status code for `HTTPError` and timeout text for `TimeoutError`.

- [ ] **Step 5: Document remote contract**

Add to `README.md`:

```markdown
### External Orchestrator Backend

AGOS sends a versioned orchestration payload to a remote backend:

`POST /runs`

```json
{
  "schema_version": "agos.orchestration.v1",
  "idempotency_key": "execution-run-01",
  "spec": {
    "run_id": "execution-run-01",
    "task_id": "agos-01",
    "backend": "external",
    "nodes": []
  }
}
```

Required remote endpoints:

- `POST /runs`
- `GET /runs/{run_id}`
- `POST /runs/{run_id}/cancel`
- `GET /runs/{run_id}/artifacts`
```

- [ ] **Step 6: Run external verification**

Run:

```powershell
python -m pytest tests/core/test_external_backend_http.py tests/integration/test_external_backend_server.py tests/core/test_backend_parity.py -q
python -m ruff check src tests
```

Expected: pass.

- [ ] **Step 7: Commit Task 6**

Run:

```powershell
git add src/agos/backends/external_backend.py README.md tests/core/test_external_backend_http.py tests/integration/test_external_backend_server.py
git commit -m "feat: document external orchestrator HTTP contract"
```

---

## Task 7: Ordered Patch Stack Merge Strategy

**Files:**
- Modify: `src/agos/core/execution.py`
- Modify: `src/agos/core/arbiters.py`
- Modify: `src/agos/core/execution_store.py`
- Modify: `src/agos/core/execution_service.py`
- Modify: `src/agos/cli/cmd_candidate.py`
- Test: `tests/core/test_merge_arbiter.py`
- Test: `tests/cli/test_candidate_merge.py`

- [ ] **Step 1: Add ordered stack arbiter tests**

Add to `tests/core/test_merge_arbiter.py`:

```python
def test_merge_arbiter_selects_ordered_patch_stack_for_serial_candidates():
    decision = CandidateMergeArbiter().decide_bundle(
        [
            _candidate("candidate-a", ("README.md",), score=2),
            _candidate("candidate-b", ("README.md",), score=1),
        ],
        dirty_paths=(),
        dependency_order=("candidate-a", "candidate-b"),
    )

    assert decision.strategy == "ordered_patch_stack"
    assert decision.candidate_ids == ("candidate-a", "candidate-b")


def test_merge_arbiter_rejects_overlapping_candidates_without_dependency_order():
    decision = CandidateMergeArbiter().decide_bundle(
        [
            _candidate("candidate-a", ("README.md",), score=2),
            _candidate("candidate-b", ("README.md",), score=1),
        ],
        dirty_paths=(),
    )

    assert decision.strategy == "manual_merge_required"
    assert decision.conflict_candidate_ids == ("candidate-a", "candidate-b")
```

- [ ] **Step 2: Run ordered stack tests and verify RED**

Run:

```powershell
python -m pytest tests/core/test_merge_arbiter.py::test_merge_arbiter_selects_ordered_patch_stack_for_serial_candidates -q
```

Expected: fail because `decide_bundle()` does not accept `dependency_order`.

- [ ] **Step 3: Extend bundle decision API**

Change `CandidateMergeArbiter.decide_bundle()` signature:

```python
def decide_bundle(
    self,
    candidates: list[MergeCandidateSnapshot],
    *,
    dirty_paths: Iterable[str],
    dependency_order: Iterable[str] = (),
) -> CandidateBundleMergeDecision:
```

When candidates overlap and every overlapping candidate appears in `dependency_order`, return:

```python
CandidateBundleMergeDecision(
    strategy="ordered_patch_stack",
    candidate_ids=tuple(candidate_id for candidate_id in dependency_order if candidate_id in eligible_by_id),
    reason="Eligible overlapping candidates have an explicit dependency order and require stack dry-run.",
    evidence_refs=tuple(eligible_by_id[candidate_id].patch_ref for candidate_id in ordered_ids),
)
```

- [ ] **Step 4: Add merge preview model**

Add to `src/agos/core/execution.py`:

```python
class CandidateMergePreview(BaseModel):
    id: str
    decision_id: str
    strategy: MergeStrategy
    candidate_ids: list[str] = Field(default_factory=list)
    state: Literal["passed", "failed"]
    evidence_refs: list[str] = Field(default_factory=list)
    conflict_evidence_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
```

- [ ] **Step 5: Persist merge previews**

Add to `ExecutionStore`:

```python
def write_merge_preview(self, preview: CandidateMergePreview) -> str:
    return self._write_model(
        self.execution_dir / "merge_previews" / f"{preview.id}.json",
        preview,
        f"execution/merge_previews/{preview.id}.json",
    )


def read_merge_preview(self, preview_id: str) -> CandidateMergePreview:
    path = self.execution_dir / "merge_previews" / f"{preview_id}.json"
    return CandidateMergePreview.model_validate_json(path.read_text(encoding="utf-8"))
```

- [ ] **Step 6: Implement stack dry-run before apply**

In `ExecutionService.apply_candidate_bundle()`:

```text
single_candidate -> existing single candidate guarded apply path
non_overlapping_bundle -> validate all candidates, then apply in candidate_ids order
ordered_patch_stack -> create temporary merge workspace, apply each patch sequentially with git apply --check and git apply, run active-task gates, then apply the same sequence to governed repo
manual_merge_required -> raise ValueError
```

The temporary stack preview must write:

```text
.agos/tasks/current/evidence/execution/merge-preview-<id>.log
```

and persist `CandidateMergePreview(state="passed")` before governed repo mutation.

- [ ] **Step 7: Add CLI stack test**

Add to `tests/cli/test_candidate_merge.py`:

```python
def test_candidate_merge_apply_ordered_patch_stack(cli_repo):
    # Use the existing fixture style from this file.
    # Create candidate-a and candidate-b that both touch README.md on non-conflicting lines.
    # Accept both candidates.
    # Run: agos candidate merge decide --ordered candidate-a candidate-b
    # Assert stdout contains "ordered_patch_stack".
    # Run: agos candidate merge apply <bundle-id>
    # Assert README.md contains both changes and both candidate records are "applied".
```

Replace the fixture names with the actual fixtures already used in `tests/cli/test_candidate_merge.py`; keep this as one concrete test in that file, not a separate helper-only test.

- [ ] **Step 8: Run merge strategy verification**

Run:

```powershell
$env:TMP='E:\AGOS_V2\.tmp'
$env:TEMP='E:\AGOS_V2\.tmp'
python -m pytest tests/core/test_merge_arbiter.py tests/cli/test_candidate_merge.py -q
python -m pytest tests/core/test_execution_service.py tests/cli/test_candidate.py -q
python -m ruff check src tests
```

Expected: pass.

- [ ] **Step 9: Commit Task 7**

Run:

```powershell
git add src/agos/core/execution.py src/agos/core/arbiters.py src/agos/core/execution_store.py src/agos/core/execution_service.py src/agos/cli/cmd_candidate.py tests/core/test_merge_arbiter.py tests/cli/test_candidate_merge.py
git commit -m "feat: add ordered patch stack merge strategy"
```

---

## Task 8: End-To-End Multi-Agent Runtime Wiring

**Files:**
- Modify: `src/agos/cli/cmd_execute_plan.py`
- Modify: `src/agos/cli/main.py`
- Modify: `src/agos/cli/reviewer_registry.py`
- Modify: `src/agos/cli/worker_registry.py`
- Create: `tests/integration/test_multi_agent_runtime.py`

- [ ] **Step 1: Write end-to-end runtime test**

Create `tests/integration/test_multi_agent_runtime.py`:

```python
from __future__ import annotations

import json


def test_multi_agent_runtime_closed_loop(cli_repo, run_cli):
    repo = cli_repo
    (repo / ".agos" / "agos.yaml").write_text(
        """
executor:
  name: multica
  agent: Lambda
workers:
  local_worktree:
    type: local_worktree
reviewers:
  manual:
    type: manual
    role: test_reviewer
    required: true
orchestration:
  backend: native_async
  max_parallel: 2
  max_retries: 1
workflows:
  docs_only:
    gates: []
""",
        encoding="utf-8",
    )
    plan = {
        "id": "plan-01",
        "task_id": "agos-01",
        "max_parallel": 2,
        "requires_candidate_review": True,
        "subtasks": [
            {"id": "readme", "title": "README", "write_scope": ["README.md"], "worker": {"adapter": "local_worktree"}},
            {"id": "guide", "title": "Guide", "write_scope": ["docs/guide.md"], "worker": {"adapter": "local_worktree"}},
        ],
    }
    plan_path = repo / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    started = run_cli("execute-plan", "run", "--plan", str(plan_path))
    assert started.exit_code == 0
    assert "running:" in started.stdout

    run_id = started.stdout.split("|", 1)[0].strip()
    status = run_cli("execute-plan", "status", run_id)
    resumed = run_cli("execute-plan", "resume", run_id)

    assert status.exit_code == 0
    assert resumed.exit_code == 0
    assert (repo / ".agos" / "tasks" / "current" / "execution" / "runs" / run_id).exists()
```

If the repo uses different fixture names, adapt only `cli_repo` and `run_cli` to the existing integration fixture names.

- [ ] **Step 2: Run end-to-end test and verify RED or GREEN**

Run:

```powershell
python -m pytest tests/integration/test_multi_agent_runtime.py -q
```

Expected: fail if runtime CLI still misses config/runtime wiring; pass if Task 2 already completed the path.

- [ ] **Step 3: Add `--json` status output**

In `cmd_execute_plan.py`, add `json_output: bool = typer.Option(False, "--json")` to `run`, `resume`, `status`, and `cancel`.

When `--json` is set, print:

```python
snapshot.model_dump_json()
```

If `ExecutionRuntimeSnapshot` is still a dataclass, add:

```python
def _snapshot_json(snapshot: ExecutionRuntimeSnapshot) -> str:
    return json.dumps(
        {
            "run_id": snapshot.run_id,
            "running_subtasks": list(snapshot.running_subtasks),
            "completed_subtasks": list(snapshot.completed_subtasks),
            "failed_subtasks": list(snapshot.failed_subtasks),
            "cancelled_subtasks": list(snapshot.cancelled_subtasks),
        },
        sort_keys=True,
    )
```

- [ ] **Step 4: Ensure registries are CLI-bound only**

Verify these imports do not appear under `src/agos/core`:

```powershell
Select-String -Path src/agos/core/*.py -Pattern "agos.adapters"
```

Expected: no matches.

If matches exist, move concrete adapter construction to:

```text
src/agos/cli/worker_registry.py
src/agos/cli/reviewer_registry.py
```

- [ ] **Step 5: Run end-to-end verification**

Run:

```powershell
python -m pytest tests/integration/test_multi_agent_runtime.py tests/core/test_executor_seam.py tests/cli/test_execute_plan_runtime.py -q
python -m ruff check src tests
```

Expected: pass.

- [ ] **Step 6: Commit Task 8**

Run:

```powershell
git add src/agos/cli tests/integration/test_multi_agent_runtime.py
git commit -m "feat: wire end-to-end multi-agent runtime"
```

---

## Task 9: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-22-agos-multi-agent-orchestration-design.md`
- Modify: `docs/superpowers/plans/2026-06-23-agos-production-orchestration-runtime.md`
- Modify: `docs/superpowers/plans/2026-06-23-agos-remaining-production-orchestration-gaps.md`

- [ ] **Step 1: Document production config**

Add this example to `README.md`:

```yaml
workers:
  codex:
    type: codex_cli
    command: codex
    timeout_seconds: 120
    poll_interval_seconds: 2
    artifact_globs:
      - .agos-worker/*.json
  multica:
    type: multica
    command: multica
    agent: Lambda
    timeout_seconds: 120
  openhands:
    type: openhands
    endpoint: http://openhands.local
    token: ${OPENHANDS_TOKEN}
    timeout_seconds: 120

reviewers:
  security:
    type: manual
    role: security_reviewer
    required: true

orchestration:
  backend: native_async
  max_parallel: 2
  max_retries: 1
  worker_timeout_seconds: 900
  retry_backoff_seconds: 5
```

- [ ] **Step 2: Document merge strategies**

Add this table to `README.md`:

```markdown
| Strategy | Automatic Apply | Meaning |
|---|---:|---|
| `single_candidate` | Yes | One accepted candidate passes all guards. |
| `non_overlapping_bundle` | Yes | Multiple accepted candidates touch disjoint paths. |
| `ordered_patch_stack` | Yes, after stack dry-run | Multiple accepted candidates have explicit order and apply cleanly in a temporary stack workspace. |
| `manual_merge_required` | No | Dirty paths, conflicts, missing review/test evidence, or ambiguous ordering require human action. |
```

- [ ] **Step 3: Update design lifecycle interface**

In `docs/superpowers/specs/2026-06-22-agos-multi-agent-orchestration-design.md`, replace the old `ReviewerAdapter.review()` and backend sketches with:

```python
class ReviewerAdapter(Protocol):
    name: str
    def start(self, request: ReviewerStartRequest) -> ReviewerRun: ...
    def poll(self, run_id: str, *, reviewer_id: str) -> ReviewerRunStatus: ...
    def cancel(self, run_id: str) -> ReviewerRunStatus: ...


class OrchestratorBackend(Protocol):
    name: str
    def start(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle: ...
    def poll(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus: ...
    def cancel(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus: ...
    def collect(self, handle: OrchestratorRunHandle) -> dict[str, object]: ...
    def run(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle: ...
```

- [ ] **Step 4: Mark completed plan tasks**

In `docs/superpowers/plans/2026-06-23-agos-production-orchestration-runtime.md`, mark tasks that have landed as completed by changing their task headings to include:

```markdown
**Status:** Implemented in `<commit-sha>`.
```

Use actual commit shas from:

```powershell
git log --oneline -- docs/superpowers/plans/2026-06-23-agos-production-orchestration-runtime.md src/agos
```

- [ ] **Step 5: Run full verification**

Run:

```powershell
New-Item -ItemType Directory -Force E:\AGOS_V2\.tmp | Out-Null
$env:TMP='E:\AGOS_V2\.tmp'
$env:TEMP='E:\AGOS_V2\.tmp'
python -m pytest -q
python -m pytest --cov=agos --cov-report=term-missing -q
python -m ruff check src tests
python -m compileall -q src tests
```

Expected:

```text
pytest passes
coverage remains at or above the project threshold
ruff passes
compileall passes
```

- [ ] **Step 6: Commit documentation**

Run:

```powershell
git add README.md docs/superpowers/specs/2026-06-22-agos-multi-agent-orchestration-design.md docs/superpowers/plans/2026-06-23-agos-production-orchestration-runtime.md docs/superpowers/plans/2026-06-23-agos-remaining-production-orchestration-gaps.md
git commit -m "docs: plan remaining production orchestration gaps"
```

---

## Final Acceptance

The remaining production orchestration work is complete when all of these are true:

- Codex, Multica, and OpenHands adapters have config-driven timeout/env/artifact behavior and opt-in smoke tests.
- Execution runtime prevents duplicate starts on resume, retries recoverable failures, persists run-level status, and supports timeout/cancel semantics.
- Graph runtime polls worker/reviewer/arbiter nodes, persists output refs, retries within policy, and cancels through backends.
- Reviewer, worker, and arbiter adapters can be wrapped as graph node backends without importing concrete adapters into core.
- LangGraph backend dispatches real normalized node actions when installed and skips cleanly when absent.
- External backend is verified against a real local HTTP server and documents its remote contract.
- Merge arbiter supports `single_candidate`, `non_overlapping_bundle`, `ordered_patch_stack`, and `manual_merge_required`.
- Ordered stack apply mutates the governed repo only after a temporary stack workspace proves every patch applies and gates pass.
- `agos execute-plan run/status/resume/cancel` can drive a fake/local end-to-end multi-agent runtime from persisted state.
- Core still has no imports from `agos.adapters`.
- Full verification passes:

```powershell
python -m pytest -q
python -m pytest --cov=agos --cov-report=term-missing -q
python -m ruff check src tests
python -m compileall -q src tests
```

## Self-Review

- Spec coverage: every user-listed unfinished item maps to at least one task. Worker adapters are Task 1; external backend is Task 6; production multi-worker runtime is Tasks 2, 3, 4, and 8; merge arbiter strategy is Task 7; LangGraph is Task 5.
- Placeholder scan: the plan avoids placeholder markers and avoids implementation-free "add tests" steps. The only fixture adaptation note is constrained to existing test fixture names because those names must match the current test harness.
- Type consistency: lifecycle names match current code: `WorkerStartRequest`, `WorkerRun`, `WorkerRunStatus`, `OrchestrationRunSpec`, `NodeSpec`, `AgentJobHandle`, `OrchestratorRunStatus`, `CandidateBundleDecision`, and `CandidateMergeArbiter`.

