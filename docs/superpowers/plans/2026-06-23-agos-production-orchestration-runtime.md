# AGOS Production Orchestration Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the unfinished AGOS multi-agent roadmap by turning the current review/execution seams into a pluggable, recoverable orchestration runtime with real worker adapters, parallel reviewers, optional LangGraph execution, HTTP external orchestration, and bundle-aware merge arbitration.

**Architecture:** Keep AGOS as the governance layer: core owns normalized models, runtime state machines, evidence refs, retry/cancel semantics, and arbiters; concrete adapters live under `src/agos/adapters/*`; CLI registries wire configured adapters into core services. The native graph runtime is the semantic reference implementation, while LangGraph and external HTTP backends must preserve the same normalized lifecycle contract.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, pytest, stdlib `subprocess`/`urllib`, optional LangGraph extra, filesystem JSON stores, deterministic fake adapters for unit tests.

---

## Current Baseline

The development docs define AGOS as:

```text
Agent writes.
AGOS verifies.
CI enforces.
```

The repository is currently around v0.4 seam maturity:

- v0.2 Review layer exists for deterministic packets, findings, resolution, and closeout gates.
- v0.3 Execution loop exists for local worktree candidates, candidate tests/reviews/decisions, and guarded apply.
- v0.4 backend seams exist for `native_async`, `langgraph`, and `external`, but LangGraph is still a compile shim and external is still a normalized submission payload boundary.
- Worker execution is still local/fake-first. Codex/Multica/OpenHands worker adapters are not production-connected through a common lifecycle.
- Multi-worker scheduling, polling, retry, timeout, cancel, resume, and failure recovery are not yet a production runtime.
- Merge arbitration protects against direct conflicts, but it cannot yet select or apply candidate bundles.

## File Structure

- Extend `src/agos/core/execution_worker.py`
  - Worker run lifecycle request/run/status models and `start/poll/cancel` protocol methods.
- Create `src/agos/adapters/workers/codex_cli.py`
  - Codex CLI worker adapter using JSON CLI calls.
- Create `src/agos/adapters/workers/multica_worker.py`
  - Multica worker adapter wrapping `issue create` and `issue runs`.
- Create `src/agos/adapters/workers/openhands.py`
  - OpenHands HTTP worker adapter.
- Modify `src/agos/adapters/workers/fake.py`
  - Deterministic lifecycle fake for runtime tests.
- Modify `src/agos/adapters/workers/local_worktree.py`
  - Local lifecycle no-op/completed semantics plus existing patch export.
- Modify `src/agos/core/config.py`
  - Worker, reviewer, orchestration backend config schemas.
- Create `src/agos/cli/worker_registry.py`
  - CLI boundary factory for worker adapters.
- Create `src/agos/core/review_adapter.py`
  - Pluggable reviewer lifecycle protocol independent of orchestration nodes.
- Create `src/agos/core/review_orchestrator.py`
  - Parallel reviewer scheduler and failure policy.
- Create `src/agos/cli/reviewer_registry.py`
  - CLI boundary factory for configured reviewer adapters.
- Modify `src/agos/adapters/reviewers/manual.py`
  - Implement the common reviewer adapter contract.
- Create `src/agos/adapters/reviewers/fake.py`
  - Deterministic reviewer adapter for runtime tests.
- Modify `src/agos/core/orchestration/protocols.py`
  - Promote backend lifecycle to `start/poll/cancel/collect`.
- Modify `src/agos/core/orchestration/runtime.py`
  - Persist node attempts, timestamps, job handles, output refs, retry state.
- Create `src/agos/core/orchestration/graph_runtime.py`
  - Native multi-agent DAG runtime for workers, reviewers, and arbiters.
- Modify `src/agos/core/orchestration/registry.py`
  - Resolve lifecycle-capable worker/reviewer/arbiter/orchestrator backends.
- Modify `src/agos/core/execution.py`
  - Execution run, worker attempt, merge bundle, and strategy models.
- Create `src/agos/core/execution_runtime.py`
  - Execution-plan-specific scheduler on top of the graph runtime.
- Modify `src/agos/core/execution_store.py`
  - Persist execution runs, worker attempts, bundle decisions, and runtime status.
- Modify `src/agos/core/execution_service.py`
  - Register adapters, expose adapter names, launch/resume/cancel runtime runs.
- Modify `src/agos/cli/cmd_execute_plan.py`
  - Add runtime commands while preserving legacy `--plan`.
- Modify `src/agos/cli/cmd_candidate.py`
  - Apply single candidates and bundle decisions through the same guard path.
- Modify `src/agos/backends/langgraph_backend.py`
  - Execute compiled LangGraph graphs when installed, not only compile them.
- Modify `src/agos/backends/external_backend.py`
  - Submit/poll/cancel/collect over HTTP.
- Modify `src/agos/core/arbiters.py`
  - Bundle-aware merge arbiter strategies.
- Modify `pyproject.toml`
  - Add optional LangGraph dependency extra.
- Modify `README.md`
  - Document adapters, runtime, backend contract, and merge strategies.

## Task 0: Baseline Guardrail

**Status:** Implemented in `0ee82de`.

**Files:**
- Read: `docs/superpowers/specs/2026-06-22-agos-multi-agent-orchestration-design.md`
- Read: `docs/superpowers/specs/2026-06-22-agos-execution-agent-orchestration-design.md`
- Read: `src/agos/core/orchestration/protocols.py`
- Read: `src/agos/core/execution_worker.py`

- [ ] **Step 1: Confirm the worktree and current uncommitted state**

Run:

```bash
git status --short
```

Expected: the plan file may be modified. If RED test files already exist from a previous attempt, keep them and continue; do not delete them.

- [ ] **Step 2: Run the existing seam regression tests**

Run:

```bash
python -m pytest tests/core/test_executor_seam.py tests/core/test_orchestration_registry.py tests/core/test_backend_parity.py -q
```

Expected before implementation: existing committed tests pass. If new RED tests are present, do not include them in this baseline command.

- [ ] **Step 3: Commit only if this task changed files**

If this task only gathered context, do not commit. If documentation was corrected, commit:

```bash
git add docs/superpowers/plans/2026-06-23-agos-production-orchestration-runtime.md
git commit -m "docs: plan production orchestration runtime"
```

## Task 1: Worker Lifecycle Protocol and Concrete Worker Adapters

**Status:** Implemented in `52bf964`; production config hardened in `d8eee4c`.

**Files:**
- Modify: `src/agos/core/execution_worker.py`
- Modify: `src/agos/adapters/workers/fake.py`
- Modify: `src/agos/adapters/workers/local_worktree.py`
- Create: `src/agos/adapters/workers/codex_cli.py`
- Create: `src/agos/adapters/workers/multica_worker.py`
- Create: `src/agos/adapters/workers/openhands.py`
- Modify: `src/agos/adapters/workers/__init__.py`
- Modify: `src/agos/core/config.py`
- Create: `src/agos/cli/worker_registry.py`
- Modify: `src/agos/core/execution_service.py`
- Tests:
  - `tests/core/test_execution_worker.py`
  - `tests/adapters/test_worker_adapters.py`
  - `tests/cli/test_worker_registry.py`

- [ ] **Step 1: Write or keep the failing lifecycle model tests**

Create or keep `tests/core/test_execution_worker.py` with the expected lifecycle model behavior:

```python
from __future__ import annotations

from agos.core.execution_worker import WorkerRun, WorkerRunStatus, WorkerStartRequest


def test_worker_run_models_round_trip_lifecycle_metadata():
    request = WorkerStartRequest(
        run_id="execution-run-01",
        subtask_id="subtask-01",
        prompt="Implement the scoped change.",
        workspace_path="C:/work/subtask-01",
        metadata={"plan_ref": "execution/plan.json"},
    )
    run = WorkerRun(
        backend="codex",
        run_id="worker-run-01",
        subtask_id=request.subtask_id,
        state="running",
        metadata={"pid": "123"},
    )
    status = WorkerRunStatus(
        backend=run.backend,
        run_id=run.run_id,
        subtask_id=run.subtask_id,
        state="completed",
        detail="done",
        output_refs=["workers/worker-run-01.log"],
    )

    assert request.model_dump()["workspace_path"] == "C:/work/subtask-01"
    assert run.model_dump()["metadata"] == {"pid": "123"}
    assert status.model_dump()["output_refs"] == ["workers/worker-run-01.log"]


def test_worker_run_status_identifies_terminal_states():
    assert WorkerRunStatus(
        backend="codex",
        run_id="run-01",
        subtask_id="subtask-01",
        state="completed",
    ).is_terminal
    assert not WorkerRunStatus(
        backend="codex",
        run_id="run-01",
        subtask_id="subtask-01",
        state="running",
    ).is_terminal
```

- [ ] **Step 2: Run model tests and verify RED**

Run:

```bash
python -m pytest tests/core/test_execution_worker.py -q
```

Expected: fail with `cannot import name 'WorkerRun'` or equivalent missing lifecycle models.

- [ ] **Step 3: Add lifecycle models and protocol methods**

Modify `src/agos/core/execution_worker.py` so it contains these core API shapes:

```python
from typing import Literal, Protocol

from pydantic import BaseModel, Field


WorkerRunState = Literal["queued", "running", "completed", "failed", "cancelled", "blocked"]


class WorkerStartRequest(BaseModel):
    run_id: str
    subtask_id: str
    prompt: str
    workspace_path: str
    metadata: dict[str, str] = Field(default_factory=dict)


class WorkerRun(BaseModel):
    backend: str
    run_id: str
    subtask_id: str
    state: WorkerRunState
    metadata: dict[str, str] = Field(default_factory=dict)


class WorkerRunStatus(BaseModel):
    backend: str
    run_id: str
    subtask_id: str
    state: WorkerRunState
    detail: str | None = None
    output_refs: list[str] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.state in {"completed", "failed", "cancelled", "blocked"}


class ExecutionWorkerAdapter(Protocol):
    name: str

    def prepare(self, assignment: WorkerAssignment) -> WorkspaceBinding | WorkerPreparedWorkspace: ...

    def start(self, request: WorkerStartRequest) -> WorkerRun: ...

    def poll(self, run_id: str, *, subtask_id: str) -> WorkerRunStatus: ...

    def cancel(self, run_id: str) -> WorkerRunStatus: ...

    def export_candidate(self, handle: WorkerWorkspaceHandle) -> dict[str, bytes]: ...
```

- [ ] **Step 4: Run model tests and verify GREEN**

Run:

```bash
python -m pytest tests/core/test_execution_worker.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Write or keep adapter tests for Codex, Multica, OpenHands, local, fake**

Create or keep `tests/adapters/test_worker_adapters.py`. It must mock command/HTTP boundaries and assert no real external tool is required.

Key assertions:

```python
assert calls[0][:2] == ["codex", "exec"]
assert ["codex", "status", "codex-run-01", "--json"] in calls
assert ["codex", "cancel", "codex-run-01", "--json"] in calls
assert run.backend == "multica"
assert status.state == "completed"
assert requests[0][0] == "POST"
assert requests[0][1].endswith("/runs")
```

- [ ] **Step 6: Run adapter tests and verify RED**

Run:

```bash
python -m pytest tests/adapters/test_worker_adapters.py -q
```

Expected: fail because `codex_cli`, `multica_worker`, and `openhands` modules are missing or lifecycle methods are missing.

- [ ] **Step 7: Implement adapter command boundaries**

Implementation requirements:

```text
CodexWorkerAdapter.start:
  run_command([command, "exec", "--json", request.prompt], cwd=Path(request.workspace_path), ...)
  parse {"run_id": "..."}

CodexWorkerAdapter.poll:
  run_command([command, "status", run_id, "--json"], ...)
  map JSON state/detail/output_refs to WorkerRunStatus

CodexWorkerAdapter.cancel:
  run_command([command, "cancel", run_id, "--json"], ...)
  return WorkerRunStatus(state="cancelled" unless payload says otherwise)

MulticaWorkerAdapter.start:
  run_command([multica_bin, "issue", "create", "--title", subtask_id, "--description", prompt, "--assignee", agent, "--allow-duplicate", "--output", "json"])
  then run_command([multica_bin, "issue", "runs", issue_id, "--output", "json"])
  return first run id

OpenHandsWorkerAdapter.start:
  POST {endpoint}/runs with run_id, subtask_id, prompt, workspace_path, metadata

OpenHandsWorkerAdapter.poll:
  GET {endpoint}/runs/{run_id}

OpenHandsWorkerAdapter.cancel:
  POST {endpoint}/runs/{run_id}/cancel
```

Map external states using this normalized table:

```python
STATE_MAP = {
    "queued": "queued",
    "pending": "queued",
    "todo": "queued",
    "running": "running",
    "in_progress": "running",
    "done": "completed",
    "completed": "completed",
    "blocked": "blocked",
    "failed": "failed",
    "error": "failed",
    "cancelled": "cancelled",
}
```

- [ ] **Step 8: Add config-driven worker registry**

Create `src/agos/cli/worker_registry.py`:

```python
from __future__ import annotations

from agos.adapters.workers import LocalWorktreeWorkerAdapter
from agos.adapters.workers.codex_cli import CodexWorkerAdapter
from agos.adapters.workers.multica_worker import MulticaWorkerAdapter
from agos.adapters.workers.openhands import OpenHandsWorkerAdapter
from agos.core.config import load_config
from agos.core.execution_service import ExecutionService


def register_configured_worker_adapters(service: ExecutionService) -> None:
    config = load_config(service.paths.repo_root)
    workers = config.workers or {"local_worktree": {"type": "local_worktree"}}
    for name, worker in workers.items():
        worker_type = worker.type
        if worker_type == "local_worktree":
            service.register_worker_adapter(LocalWorktreeWorkerAdapter(service.workspace_manager))
        elif worker_type == "codex_cli":
            service.register_worker_adapter(CodexWorkerAdapter(name=name, command=worker.command or "codex"))
        elif worker_type == "multica":
            service.register_worker_adapter(
                MulticaWorkerAdapter(name=name, multica_bin=worker.command or "multica", agent=worker.agent)
            )
        elif worker_type == "openhands":
            service.register_worker_adapter(
                OpenHandsWorkerAdapter(name=name, endpoint=worker.endpoint, token=worker.token)
            )
        else:
            raise ValueError(f"unsupported worker adapter type: {worker_type}")
```

Add `WorkerConfig` to `src/agos/core/config.py` with fields:

```python
class WorkerConfig(BaseModel):
    type: str
    command: str | None = None
    agent: str | None = None
    endpoint: str | None = None
    token: str | None = None


class AGOSConfig(BaseModel):
    workers: dict[str, WorkerConfig] = Field(default_factory=dict)
```

Also add `ExecutionService.worker_adapter_names()`:

```python
def worker_adapter_names(self) -> list[str]:
    return sorted(self._worker_adapters)
```

- [ ] **Step 9: Run Task 1 tests and verify GREEN**

Run:

```bash
python -m pytest tests/core/test_execution_worker.py tests/adapters/test_worker_adapters.py tests/cli/test_worker_registry.py -q
```

Expected: all Task 1 tests pass.

- [ ] **Step 10: Run seam safety tests**

Run:

```bash
python -m pytest tests/core/test_executor_seam.py tests/core/test_execution_service.py tests/cli/test_candidate.py -q
python -m ruff check src tests
```

Expected: pass. `tests/core/test_executor_seam.py` must still prove core does not import `agos.adapters`.

- [ ] **Step 11: Commit Task 1**

Run:

```bash
git add src/agos/core/execution_worker.py src/agos/core/config.py src/agos/core/execution_service.py src/agos/adapters/workers src/agos/cli/worker_registry.py tests/core/test_execution_worker.py tests/adapters/test_worker_adapters.py tests/cli/test_worker_registry.py
git commit -m "feat: add execution worker lifecycle adapters"
```

## Task 2: ReviewerAdapter and Parallel Reviewer Scheduling

**Status:** Implemented in `7964616`.

**Files:**
- Create: `src/agos/core/review_adapter.py`
- Create: `src/agos/core/review_orchestrator.py`
- Modify: `src/agos/adapters/reviewers/manual.py`
- Create: `src/agos/adapters/reviewers/fake.py`
- Modify: `src/agos/adapters/reviewers/__init__.py`
- Modify: `src/agos/core/config.py`
- Create: `src/agos/cli/reviewer_registry.py`
- Tests:
  - `tests/core/test_review_adapter.py`
  - `tests/core/test_parallel_review_orchestrator.py`
  - `tests/cli/test_reviewer_registry.py`

- [ ] **Step 1: Write reviewer adapter protocol tests**

Create `tests/core/test_review_adapter.py`:

```python
from __future__ import annotations

from agos.core.review import Finding, ReviewPacket
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus, ReviewerStartRequest


def test_reviewer_lifecycle_models_round_trip():
    packet = ReviewPacket(
        review_id="review-01",
        task_id="agos-01",
        task_title="Title",
        task_intent="Intent",
        diff_kind="governed_repo_diff",
        ledger_head_hash="abc123",
    )
    request = ReviewerStartRequest(
        run_id="review-run-01",
        reviewer_id="security",
        role="security_reviewer",
        packet=packet,
        metadata={"required": "true"},
    )
    run = ReviewerRun(
        backend="fake_reviewer",
        run_id=request.run_id,
        reviewer_id=request.reviewer_id,
        state="running",
    )
    status = ReviewerRunStatus(
        backend=run.backend,
        run_id=run.run_id,
        reviewer_id=run.reviewer_id,
        state="completed",
        findings=[
            Finding(
                id="finding-01",
                review_id="review-01",
                source_agent="security",
                category="security",
                severity="high",
                blocking=True,
                title="Unsafe command",
                body="Unsafe shell use.",
            )
        ],
    )

    assert status.is_terminal
    assert status.findings[0].source_agent == "security"
```

- [ ] **Step 2: Run reviewer model tests and verify RED**

Run:

```bash
python -m pytest tests/core/test_review_adapter.py -q
```

Expected: fail because `agos.core.review_adapter` does not exist.

- [ ] **Step 3: Implement `ReviewerAdapter` lifecycle protocol**

Create `src/agos/core/review_adapter.py`:

```python
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from agos.core.review import Finding, ReviewPacket


ReviewerRunState = Literal["queued", "running", "completed", "failed", "cancelled"]


class ReviewerStartRequest(BaseModel):
    run_id: str
    reviewer_id: str
    role: str
    packet: ReviewPacket
    metadata: dict[str, str] = Field(default_factory=dict)


class ReviewerRun(BaseModel):
    backend: str
    run_id: str
    reviewer_id: str
    state: ReviewerRunState
    metadata: dict[str, str] = Field(default_factory=dict)


class ReviewerRunStatus(BaseModel):
    backend: str
    run_id: str
    reviewer_id: str
    state: ReviewerRunState
    findings: list[Finding] = Field(default_factory=list)
    raw_ref: str | None = None
    detail: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in {"completed", "failed", "cancelled"}


class ReviewerAdapter(Protocol):
    name: str

    def start(self, request: ReviewerStartRequest) -> ReviewerRun: ...

    def poll(self, run_id: str, *, reviewer_id: str) -> ReviewerRunStatus: ...

    def cancel(self, run_id: str) -> ReviewerRunStatus: ...
```

- [ ] **Step 4: Run reviewer model tests and verify GREEN**

Run:

```bash
python -m pytest tests/core/test_review_adapter.py -q
```

Expected: pass.

- [ ] **Step 5: Write parallel reviewer scheduler tests**

Create `tests/core/test_parallel_review_orchestrator.py`:

```python
from __future__ import annotations

from agos.core.review import Finding, ReviewPacket
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus
from agos.core.review_orchestrator import ParallelReviewOrchestrator, ReviewerSpec


class FakeReviewer:
    def __init__(self, name: str, *, state: str = "completed") -> None:
        self.name = name
        self.state = state
        self.started: list[str] = []

    def start(self, request):
        self.started.append(request.reviewer_id)
        return ReviewerRun(
            backend=self.name,
            run_id=f"{request.run_id}:{request.reviewer_id}",
            reviewer_id=request.reviewer_id,
            state="running",
        )

    def poll(self, run_id: str, *, reviewer_id: str):
        if self.state == "failed":
            return ReviewerRunStatus(
                backend=self.name,
                run_id=run_id,
                reviewer_id=reviewer_id,
                state="failed",
                detail="reviewer failed",
            )
        return ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id=reviewer_id,
            state="completed",
            findings=[
                Finding(
                    id=f"finding-{reviewer_id}",
                    review_id="review-01",
                    source_agent=reviewer_id,
                    category="test",
                    severity="medium",
                    blocking=False,
                    title="Observation",
                    body="Reviewer observation.",
                )
            ],
        )

    def cancel(self, run_id: str):
        return ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id="unknown",
            state="cancelled",
        )


def _packet() -> ReviewPacket:
    return ReviewPacket(
        review_id="review-01",
        task_id="agos-01",
        task_title="Title",
        task_intent="Intent",
        diff_kind="governed_repo_diff",
        ledger_head_hash="abc123",
    )


def test_parallel_review_orchestrator_runs_multiple_reviewers():
    security = FakeReviewer("security_backend")
    tests = FakeReviewer("test_backend")
    orchestrator = ParallelReviewOrchestrator(
        reviewers={
            "security_backend": security,
            "test_backend": tests,
        }
    )

    result = orchestrator.run(
        run_id="review-run-01",
        packet=_packet(),
        reviewers=[
            ReviewerSpec(id="security", role="security_reviewer", adapter="security_backend"),
            ReviewerSpec(id="tests", role="test_reviewer", adapter="test_backend"),
        ],
        max_parallel=2,
    )

    assert result.state == "completed"
    assert [finding.source_agent for finding in result.findings] == ["security", "tests"]


def test_required_reviewer_failure_fails_review_run():
    failing = FakeReviewer("security_backend", state="failed")
    orchestrator = ParallelReviewOrchestrator(reviewers={"security_backend": failing})

    result = orchestrator.run(
        run_id="review-run-01",
        packet=_packet(),
        reviewers=[
            ReviewerSpec(
                id="security",
                role="security_reviewer",
                adapter="security_backend",
                required=True,
            )
        ],
    )

    assert result.state == "failed"
    assert result.failed_reviewers == ("security",)
```

- [ ] **Step 6: Run scheduler tests and verify RED**

Run:

```bash
python -m pytest tests/core/test_parallel_review_orchestrator.py -q
```

Expected: fail because `review_orchestrator` does not exist.

- [ ] **Step 7: Implement parallel review scheduler**

Create `src/agos/core/review_orchestrator.py`:

```python
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from pydantic import BaseModel, Field

from agos.core.review import Finding, ReviewPacket
from agos.core.review_adapter import ReviewerAdapter, ReviewerStartRequest


class ReviewerSpec(BaseModel):
    id: str
    role: str
    adapter: str
    required: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True)
class ReviewRunResult:
    run_id: str
    state: str
    findings: tuple[Finding, ...] = ()
    failed_reviewers: tuple[str, ...] = ()


class ParallelReviewOrchestrator:
    def __init__(self, reviewers: dict[str, ReviewerAdapter]) -> None:
        self._reviewers = dict(reviewers)

    def run(
        self,
        *,
        run_id: str,
        packet: ReviewPacket,
        reviewers: list[ReviewerSpec],
        max_parallel: int = 4,
    ) -> ReviewRunResult:
        with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
            futures = {
                pool.submit(self._run_one, run_id, packet, spec): spec
                for spec in reviewers
            }
            findings: list[Finding] = []
            failed: list[str] = []
            for future in as_completed(futures):
                spec = futures[future]
                status = future.result()
                if status.state == "completed":
                    findings.extend(status.findings)
                elif spec.required:
                    failed.append(spec.id)
        state = "failed" if failed else "completed"
        return ReviewRunResult(
            run_id=run_id,
            state=state,
            findings=tuple(findings),
            failed_reviewers=tuple(failed),
        )

    def _run_one(self, run_id: str, packet: ReviewPacket, spec: ReviewerSpec):
        adapter = self._reviewers[spec.adapter]
        run = adapter.start(
            ReviewerStartRequest(
                run_id=run_id,
                reviewer_id=spec.id,
                role=spec.role,
                packet=packet,
                metadata=spec.metadata,
            )
        )
        return adapter.poll(run.run_id, reviewer_id=spec.id)
```

- [ ] **Step 8: Add config-driven reviewer registry**

Extend `AGOSConfig`:

```python
class ReviewerConfig(BaseModel):
    type: str
    role: str
    required: bool = True
    command: str | None = None


class AGOSConfig(BaseModel):
    reviewers: dict[str, ReviewerConfig] = Field(default_factory=dict)
```

Create `src/agos/cli/reviewer_registry.py`:

```python
from __future__ import annotations

from agos.adapters.reviewers.fake import FakeReviewerAdapter
from agos.adapters.reviewers.manual import ManualReviewerAdapter
from agos.core.config import load_config


def configured_reviewer_adapters(repo_root):
    config = load_config(repo_root)
    adapters = {}
    for name, reviewer in config.reviewers.items():
        if reviewer.type == "manual":
            adapters[name] = ManualReviewerAdapter(name=name)
        elif reviewer.type == "fake":
            adapters[name] = FakeReviewerAdapter(name=name)
        else:
            raise ValueError(f"unsupported reviewer adapter type: {reviewer.type}")
    return adapters
```

- [ ] **Step 9: Run review scheduler tests and seam checks**

Run:

```bash
python -m pytest tests/core/test_review_adapter.py tests/core/test_parallel_review_orchestrator.py tests/cli/test_reviewer_registry.py -q
python -m pytest tests/core/test_executor_seam.py -q
```

Expected: pass and core still does not import concrete adapters.

- [ ] **Step 10: Commit Task 2**

Run:

```bash
git add src/agos/core/review_adapter.py src/agos/core/review_orchestrator.py src/agos/core/config.py src/agos/adapters/reviewers src/agos/cli/reviewer_registry.py tests/core/test_review_adapter.py tests/core/test_parallel_review_orchestrator.py tests/cli/test_reviewer_registry.py
git commit -m "feat: add pluggable parallel reviewer adapters"
```

## Task 3: Unified OrchestratorBackend Lifecycle

**Status:** Implemented in `1565b5f`.

**Files:**
- Modify: `src/agos/core/orchestration/protocols.py`
- Modify: `src/agos/core/orchestration/models.py`
- Modify: `src/agos/core/orchestration/registry.py`
- Modify: `src/agos/backends/native_async.py`
- Modify: `src/agos/backends/langgraph_backend.py`
- Modify: `src/agos/backends/external_backend.py`
- Tests:
  - `tests/core/test_orchestrator_backend_lifecycle.py`
  - `tests/core/test_orchestration_registry.py`
  - `tests/core/test_backend_parity.py`

- [ ] **Step 1: Write backend lifecycle tests**

Create `tests/core/test_orchestrator_backend_lifecycle.py`:

```python
from __future__ import annotations

from agos.backends.native_async import NativeAsyncBackend
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


def _spec() -> OrchestrationRunSpec:
    return OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[
            NodeSpec(id="worker-01", kind="worker", backend="fake_worker"),
            NodeSpec(id="reviewer-01", kind="reviewer", backend="fake_reviewer", depends_on=["worker-01"]),
        ],
        limits={"max_parallel": 2},
    )


def test_native_backend_exposes_start_poll_cancel_collect_lifecycle():
    backend = NativeAsyncBackend()

    handle = backend.start(_spec())
    status = backend.poll(handle)
    cancelled = backend.cancel(handle)
    snapshot = backend.collect(handle)

    assert handle.backend == "native_async"
    assert status.run_id == "run-01"
    assert cancelled.state == "cancelled"
    assert snapshot["run_id"] == "run-01"
```

- [ ] **Step 2: Run backend lifecycle tests and verify RED**

Run:

```bash
python -m pytest tests/core/test_orchestrator_backend_lifecycle.py -q
```

Expected: fail because backend lifecycle is incomplete or `cancel` is missing.

- [ ] **Step 3: Extend the backend protocol**

Modify `src/agos/core/orchestration/models.py`:

```python
from typing import Literal


OrchestratorRunState = Literal["queued", "running", "waiting", "completed", "failed", "cancelled"]


@dataclass(frozen=True)
class OrchestratorRunHandle:
    backend: str
    run_id: str
    job_id: str | None = None


@dataclass(frozen=True)
class OrchestratorRunStatus:
    backend: str
    run_id: str
    state: OrchestratorRunState
    waiting_nodes: tuple[str, ...] = ()
    completed_nodes: tuple[str, ...] = ()
    failed_nodes: tuple[str, ...] = ()
    output_refs: dict[str, str] | None = None
```

Modify `src/agos/core/orchestration/protocols.py`:

```python
@runtime_checkable
class OrchestrationBackend(Protocol):
    name: str

    def start(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle: ...

    def poll(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus: ...

    def cancel(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus: ...

    def collect(self, handle: OrchestratorRunHandle) -> dict[str, object]: ...

    def run(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle: ...
```

- [ ] **Step 4: Update backend implementations**

Implement `start/poll/cancel/collect/run` for `NativeAsyncBackend`, `LangGraphBackend`, and `ExternalBackend`. `run(spec)` must remain a compatibility wrapper:

```python
def run(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle:
    return self.start(spec)
```

For existing code that imports `BackendRunHandle`, keep a compatibility alias:

```python
BackendRunHandle = OrchestratorRunHandle
```

- [ ] **Step 5: Run backend parity and registry tests**

Run:

```bash
python -m pytest tests/core/test_orchestrator_backend_lifecycle.py tests/core/test_orchestration_registry.py tests/core/test_backend_parity.py -q
```

Expected: pass.

- [ ] **Step 6: Commit Task 3**

Run:

```bash
git add src/agos/core/orchestration src/agos/backends tests/core/test_orchestrator_backend_lifecycle.py tests/core/test_orchestration_registry.py tests/core/test_backend_parity.py
git commit -m "feat: standardize orchestration backend lifecycle"
```

## Task 4: Native Multi-Agent Graph Runtime with Failure Recovery

**Status:** Implemented in `ac2846b`; recovery and lifecycle polling hardened in `9e5c3bd`, `e070306`, and `9c8960b`.

**Files:**
- Modify: `src/agos/core/orchestration/runtime.py`
- Create: `src/agos/core/orchestration/graph_runtime.py`
- Modify: `src/agos/core/orchestration/scheduler.py`
- Modify: `src/agos/core/orchestration/registry.py`
- Modify: `src/agos/core/execution.py`
- Create: `src/agos/core/execution_runtime.py`
- Modify: `src/agos/core/execution_store.py`
- Modify: `src/agos/core/execution_service.py`
- Modify: `src/agos/cli/cmd_execute_plan.py`
- Tests:
  - `tests/core/test_graph_runtime.py`
  - `tests/core/test_execution_runtime.py`
  - `tests/cli/test_execute_plan_runtime.py`

- [ ] **Step 1: Write graph runtime scheduling tests**

Create `tests/core/test_graph_runtime.py`:

```python
from __future__ import annotations

from agos.core.orchestration.graph_runtime import GraphRuntime, RuntimePolicy
from agos.core.orchestration.models import AgentJobHandle, NodeSpec, OrchestrationRunSpec
from agos.core.orchestration.registry import OrchestrationRegistry


class RecordingBackend:
    def __init__(self, name: str) -> None:
        self.name = name
        self.started: list[str] = []

    def start(self, run, node):
        self.started.append(node.id)
        return AgentJobHandle(
            backend=self.name,
            job_id=f"{run.run_id}:{node.id}",
            node_id=node.id,
            run_id=run.run_id,
        )


def _spec() -> OrchestrationRunSpec:
    return OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[
            NodeSpec(id="worker-a", kind="worker", backend="worker"),
            NodeSpec(id="worker-b", kind="worker", backend="worker"),
            NodeSpec(id="review", kind="reviewer", backend="reviewer", depends_on=["worker-a", "worker-b"]),
            NodeSpec(id="merge", kind="arbiter", backend="arbiter", depends_on=["review"]),
        ],
        limits={"max_parallel": 2},
    )


def test_graph_runtime_respects_max_parallel_and_dependencies(tmp_path):
    registry = OrchestrationRegistry()
    worker = RecordingBackend("worker")
    reviewer = RecordingBackend("reviewer")
    arbiter = RecordingBackend("arbiter")
    registry.register_worker(worker)
    registry.register_reviewer(reviewer)
    registry.register_arbiter(arbiter)

    runtime = GraphRuntime(registry=registry, state_dir=tmp_path, policy=RuntimePolicy(max_parallel=2))
    snapshot = runtime.tick(_spec())

    assert snapshot.running_nodes == ("worker-a", "worker-b")
    assert worker.started == ["worker-a", "worker-b"]
    assert reviewer.started == []
```

- [ ] **Step 2: Write failure recovery tests**

Extend `tests/core/test_graph_runtime.py`:

```python
class FlakyBackend:
    name = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    def start(self, run, node):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary failure")
        return AgentJobHandle(
            backend=self.name,
            job_id=f"{run.run_id}:{node.id}:{self.calls}",
            node_id=node.id,
            run_id=run.run_id,
        )


def test_graph_runtime_retries_failed_start(tmp_path):
    registry = OrchestrationRegistry()
    backend = FlakyBackend()
    registry.register_worker(backend)
    spec = OrchestrationRunSpec(
        run_id="run-01",
        task_id="agos-01",
        nodes=[NodeSpec(id="worker-a", kind="worker", backend="flaky")],
    )
    runtime = GraphRuntime(registry=registry, state_dir=tmp_path, policy=RuntimePolicy(max_retries=1))

    failed = runtime.tick(spec)
    recovered = runtime.tick(spec)

    assert failed.failed_nodes == ("worker-a",)
    assert recovered.running_nodes == ("worker-a",)
```

- [ ] **Step 3: Run graph runtime tests and verify RED**

Run:

```bash
python -m pytest tests/core/test_graph_runtime.py -q
```

Expected: fail because `graph_runtime` does not exist.

- [ ] **Step 4: Expand persisted node state**

Modify `src/agos/core/orchestration/runtime.py`:

```python
class PersistedNodeState(BaseModel):
    node_id: str
    state: str
    attempts: int = 0
    backend: str | None = None
    job_id: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    output_refs: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
```

- [ ] **Step 5: Implement `GraphRuntime`**

Create `src/agos/core/orchestration/graph_runtime.py` with a deterministic `tick()` loop that:

- loads persisted states for every node;
- starts only runnable nodes whose dependencies are completed;
- respects `RuntimePolicy.max_parallel`;
- records `running`, `queued`, or `failed` state per node;
- retries start failures while `attempts <= max_retries`;
- dispatches worker, reviewer, arbiter, or orchestration nodes through `OrchestrationRegistry`;
- writes one JSON state file per node under `<state_dir>/<run_id>/<node_id>.json`;
- exposes `cancel()` that transitions running nodes to `cancelled`.

The concrete API:

```python
class RuntimePolicy(BaseModel):
    max_parallel: int = Field(default=1, ge=1)
    max_retries: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class RuntimeSnapshot:
    run_id: str
    running_nodes: tuple[str, ...] = ()
    completed_nodes: tuple[str, ...] = ()
    failed_nodes: tuple[str, ...] = ()
    waiting_nodes: tuple[str, ...] = ()


class GraphRuntime:
    def __init__(self, *, registry: OrchestrationRegistry, state_dir: Path, policy: RuntimePolicy | None = None) -> None: ...
    def tick(self, spec: OrchestrationRunSpec) -> RuntimeSnapshot: ...
    def cancel(self, spec: OrchestrationRunSpec) -> RuntimeSnapshot: ...
```

- [ ] **Step 6: Write execution runtime tests**

Create `tests/core/test_execution_runtime.py`:

```python
from __future__ import annotations

from agos.core.execution import ExecutionPlan, ExecutionSubtask, ExecutionWorker
from agos.core.execution_runtime import ExecutionRuntime


class CompletingWorker:
    name = "fake"

    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, request):
        self.started.append(request.subtask_id)
        from agos.core.execution_worker import WorkerRun

        return WorkerRun(
            backend=self.name,
            run_id=f"worker-{request.subtask_id}",
            subtask_id=request.subtask_id,
            state="running",
        )

    def poll(self, run_id: str, *, subtask_id: str):
        from agos.core.execution_worker import WorkerRunStatus

        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id=subtask_id,
            state="completed",
        )

    def cancel(self, run_id: str):
        from agos.core.execution_worker import WorkerRunStatus

        return WorkerRunStatus(
            backend=self.name,
            run_id=run_id,
            subtask_id="unknown",
            state="cancelled",
        )


def test_execution_runtime_starts_only_ready_subtasks(tmp_path):
    worker = CompletingWorker()
    plan = ExecutionPlan(
        id="plan-01",
        task_id="agos-01",
        max_parallel=2,
        subtasks=[
            ExecutionSubtask(id="a", title="A", write_scope=["a.py"], worker=ExecutionWorker(adapter="fake")),
            ExecutionSubtask(id="b", title="B", depends_on=["a"], write_scope=["b.py"], worker=ExecutionWorker(adapter="fake")),
        ],
    )
    runtime = ExecutionRuntime(state_dir=tmp_path, worker_adapters={"fake": worker})

    snapshot = runtime.tick(plan, run_id="execution-run-01")

    assert snapshot.running_subtasks == ("a",)
    assert worker.started == ["a"]
```

- [ ] **Step 7: Implement `ExecutionRuntime`**

Create `src/agos/core/execution_runtime.py`. It must:

- derive ready subtasks from `depends_on` and completed attempt states;
- respect `plan.max_parallel`;
- create `WorkerStartRequest` with prompt from `subtask.title` and `subtask.intent`;
- persist each run under `.agos/tasks/current/execution/runs/<run_id>/attempts/<subtask_id>.json`;
- poll running attempts and mark terminal worker states;
- retry failed attempts only while `attempts <= max_retries`;
- expose `resume` by loading the same persisted attempt files;
- expose `cancel` by calling `adapter.cancel(run_id)` for running attempts.

Use these model shapes:

```python
class WorkerAttempt(BaseModel):
    subtask_id: str
    adapter: str
    worker_run_id: str
    state: WorkerRunState
    attempts: int = 1
    detail: str | None = None
    output_refs: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ExecutionRuntimeSnapshot:
    run_id: str
    running_subtasks: tuple[str, ...] = ()
    completed_subtasks: tuple[str, ...] = ()
    failed_subtasks: tuple[str, ...] = ()
```

- [ ] **Step 8: Wire runtime through `ExecutionService` and CLI**

Add service methods:

```python
def start_execution_run(self, plan_path: Path, *, run_id: str | None = None):
    plan = self.execute_plan(plan_path)
    runtime = ExecutionRuntime(
        state_dir=self.paths.current_task / "execution" / "runs",
        worker_adapters=self._worker_adapters,
    )
    return runtime.tick(plan, run_id=run_id or f"execution-{uuid4().hex[:12]}")


def resume_execution_run(self, run_id: str):
    plan = ExecutionStore(self.paths).read_plan()
    runtime = self._execution_runtime()
    return runtime.tick(plan, run_id=run_id)


def cancel_execution_run(self, run_id: str):
    plan = ExecutionStore(self.paths).read_plan()
    runtime = self._execution_runtime()
    return runtime.cancel(plan, run_id=run_id)
```

Add the helper used by both methods:

```python
def _execution_runtime(self) -> ExecutionRuntime:
    return ExecutionRuntime(
        state_dir=self.paths.current_task / "execution" / "runs",
        worker_adapters=self._worker_adapters,
    )
```

Update `src/agos/cli/cmd_execute_plan.py` so:

```text
agos execute-plan --plan plan.yaml
```

continues to create workspaces, and new subcommands are added:

```text
agos execute-plan run --plan plan.yaml
agos execute-plan status <run-id>
agos execute-plan resume <run-id>
agos execute-plan cancel <run-id>
```

- [ ] **Step 9: Run runtime tests and CLI tests**

Run:

```bash
python -m pytest tests/core/test_graph_runtime.py tests/core/test_execution_runtime.py tests/cli/test_execute_plan_runtime.py -q
```

Expected: pass.

- [ ] **Step 10: Run execution regression tests**

Run:

```bash
python -m pytest tests/core/test_execution_service.py tests/core/test_execution_orchestration.py tests/cli/test_candidate.py -q
python -m ruff check src tests
```

Expected: pass.

- [ ] **Step 11: Commit Task 4**

Run:

```bash
git add src/agos/core/orchestration src/agos/core/execution.py src/agos/core/execution_runtime.py src/agos/core/execution_store.py src/agos/core/execution_service.py src/agos/cli/cmd_execute_plan.py tests/core/test_graph_runtime.py tests/core/test_execution_runtime.py tests/cli/test_execute_plan_runtime.py
git commit -m "feat: add recoverable multi-worker execution runtime"
```

## Task 5: Real Optional LangGraph Backend

**Status:** Implemented in `877d831`; normalized dispatch added in `41b5f81`.

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/agos/backends/langgraph_backend.py`
- Tests:
  - `tests/core/test_backend_parity.py`
  - `tests/integration/test_langgraph_backend.py`

- [ ] **Step 1: Write opt-in LangGraph integration tests**

Create `tests/integration/test_langgraph_backend.py`:

```python
from __future__ import annotations

import pytest

from agos.backends.langgraph_backend import LangGraphBackend
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


pytestmark = pytest.mark.integration


def test_real_langgraph_backend_executes_simple_graph():
    if not LangGraphBackend.is_available():
        pytest.skip("langgraph is not installed")

    backend = LangGraphBackend()
    spec = OrchestrationRunSpec(
        run_id="langgraph-run-01",
        task_id="agos-01",
        nodes=[
            NodeSpec(id="worker-a", kind="worker", backend="langgraph"),
            NodeSpec(id="worker-b", kind="worker", backend="langgraph"),
            NodeSpec(id="merge", kind="arbiter", backend="langgraph", depends_on=["worker-a", "worker-b"]),
        ],
        entry_nodes=["worker-a", "worker-b"],
    )

    handle = backend.start(spec)
    status = backend.poll(handle)
    snapshot = backend.collect(handle)

    assert status.state in {"running", "completed"}
    assert snapshot["backend"] == "langgraph"
    assert snapshot["run_id"] == "langgraph-run-01"
```

- [ ] **Step 2: Run integration test and verify current skip**

Run:

```bash
python -m pytest tests/integration/test_langgraph_backend.py -q
```

Expected without optional dependency: skipped with `langgraph is not installed`.

- [ ] **Step 3: Add optional dependency extra**

Modify `pyproject.toml`:

```toml
[project.optional-dependencies]
langgraph = [
    "langgraph",
]
```

Keep `dev` unchanged.

- [ ] **Step 4: Execute the compiled graph in `LangGraphBackend`**

Modify `src/agos/backends/langgraph_backend.py` so `start()` compiles and invokes the graph:

```python
def start(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle:
    graph_module = self._graph_module or _load_langgraph_module()
    if graph_module is None:
        raise RuntimeError("langgraph is not installed")

    compiled = _compile_run(spec, graph_module)
    self._compiled_runs[spec.run_id] = compiled
    result = compiled.graph.invoke({"visited_nodes": []})
    self._native.start(spec.model_copy(update={"backend": self.name}))
    self._completed_runs[spec.run_id] = result
    return OrchestratorRunHandle(backend=self.name, run_id=spec.run_id)
```

`collect()` must include:

```python
{
    "backend": "langgraph",
    "run_id": handle.run_id,
    "visited_nodes": result.get("visited_nodes", []),
    "edges": list(compiled.edges),
}
```

- [ ] **Step 5: Run optional install verification locally when available**

Run:

```bash
python -m pip install -e ".[langgraph]"
python -m pytest tests/integration/test_langgraph_backend.py -q
```

Expected with dependency installed: integration test passes. If dependency cannot be installed in the environment, record the install error in the task notes and keep the skip behavior passing.

- [ ] **Step 6: Run core backend parity tests**

Run:

```bash
python -m pytest tests/core/test_backend_parity.py tests/core/test_orchestrator_backend_lifecycle.py -q
```

Expected: pass without requiring LangGraph.

- [ ] **Step 7: Commit Task 5**

Run:

```bash
git add pyproject.toml src/agos/backends/langgraph_backend.py tests/integration/test_langgraph_backend.py tests/core/test_backend_parity.py
git commit -m "feat: execute optional langgraph orchestration backend"
```

## Task 6: External HTTP Orchestrator Backend

**Status:** Implemented in `4369676`; remote contract documented in `c4e8dfd`.

**Files:**
- Modify: `src/agos/backends/external_backend.py`
- Modify: `src/agos/core/config.py`
- Tests:
  - `tests/core/test_external_backend_http.py`
  - `tests/core/test_backend_parity.py`

- [ ] **Step 1: Write HTTP backend tests with a fake request function**

Create `tests/core/test_external_backend_http.py`:

```python
from __future__ import annotations

from agos.backends.external_backend import ExternalBackend
from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


def _spec() -> OrchestrationRunSpec:
    return OrchestrationRunSpec(
        run_id="external-run-01",
        task_id="agos-01",
        nodes=[NodeSpec(id="worker-01", kind="worker", backend="external")],
    )


def test_external_backend_submits_polls_cancels_and_collects():
    calls: list[tuple[str, str, object | None]] = []

    def fake_request(method, url, payload=None, timeout=30, headers=None):
        del timeout, headers
        calls.append((method, url, payload))
        if method == "POST" and url.endswith("/runs"):
            return {"run_id": "remote-01", "job_id": "job-01", "state": "running"}
        if method == "GET" and url.endswith("/runs/remote-01"):
            return {"run_id": "remote-01", "state": "completed", "completed_nodes": ["worker-01"]}
        if method == "POST" and url.endswith("/runs/remote-01/cancel"):
            return {"run_id": "remote-01", "state": "cancelled"}
        if method == "GET" and url.endswith("/runs/remote-01/artifacts"):
            return {"output_refs": {"worker-01": "remote/artifacts/worker.log"}}
        raise AssertionError((method, url))

    backend = ExternalBackend(endpoint="http://orchestrator.local", token="secret", request_json=fake_request)

    handle = backend.start(_spec())
    status = backend.poll(handle)
    snapshot = backend.collect(handle)
    cancelled = backend.cancel(handle)

    assert handle.job_id == "job-01"
    assert status.state == "completed"
    assert snapshot["output_refs"] == {"worker-01": "remote/artifacts/worker.log"}
    assert cancelled.state == "cancelled"
    assert calls[0][0] == "POST"
    assert calls[0][1] == "http://orchestrator.local/runs"
```

- [ ] **Step 2: Run external backend HTTP tests and verify RED**

Run:

```bash
python -m pytest tests/core/test_external_backend_http.py -q
```

Expected: fail because `ExternalBackend` does not yet perform HTTP lifecycle calls.

- [ ] **Step 3: Implement stdlib JSON request helper**

Modify `src/agos/backends/external_backend.py`:

```python
import json
from urllib.request import Request, urlopen


def _json_request(method: str, url: str, payload=None, timeout: int = 30, headers=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=timeout) as response:
        data = response.read().decode("utf-8")
    return json.loads(data) if data.strip() else {}
```

- [ ] **Step 4: Implement external lifecycle methods**

`ExternalBackend` constructor:

```python
def __init__(self, *, endpoint: str | None = None, token: str | None = None, timeout: int = 30, request_json=_json_request) -> None:
    self.endpoint = endpoint.rstrip("/") if endpoint else None
    self.token = token
    self.timeout = timeout
    self._request_json = request_json
    self._submitted: dict[str, dict[str, object]] = {}
```

Lifecycle behavior:

```text
If endpoint is None:
  preserve current in-memory normalized payload behavior.

If endpoint is configured:
  start -> POST /runs with spec.model_dump(mode="json")
  poll -> GET /runs/{run_id}
  cancel -> POST /runs/{run_id}/cancel
  collect -> GET /runs/{run_id}/artifacts and merge with latest status
```

Headers:

```python
headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
```

- [ ] **Step 5: Normalize remote states**

Map remote payload states into `OrchestratorRunStatus`:

```python
REMOTE_STATE_MAP = {
    "queued": "queued",
    "submitted": "queued",
    "running": "running",
    "waiting": "waiting",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}
```

Unknown remote state must raise:

```python
ValueError(f"unsupported external run state: {state!r}")
```

- [ ] **Step 6: Run external tests and backend parity**

Run:

```bash
python -m pytest tests/core/test_external_backend_http.py tests/core/test_backend_parity.py -q
```

Expected: pass.

- [ ] **Step 7: Commit Task 6**

Run:

```bash
git add src/agos/backends/external_backend.py src/agos/core/config.py tests/core/test_external_backend_http.py tests/core/test_backend_parity.py
git commit -m "feat: add external HTTP orchestration backend"
```

## Task 7: Merge Arbiter Bundle Strategies

**Status:** Implemented in `c17e993`; ordered patch stack added in `3cbe207`.

**Files:**
- Modify: `src/agos/core/execution.py`
- Modify: `src/agos/core/arbiters.py`
- Modify: `src/agos/core/execution_store.py`
- Modify: `src/agos/core/execution_service.py`
- Modify: `src/agos/cli/cmd_candidate.py`
- Tests:
  - `tests/core/test_merge_arbiter.py`
  - `tests/cli/test_candidate_merge.py`

- [ ] **Step 1: Write merge arbiter strategy tests**

Create `tests/core/test_merge_arbiter.py`:

```python
from __future__ import annotations

from agos.core.arbiters import CandidateMergeArbiter, MergeCandidateSnapshot


def _candidate(candidate_id: str, paths: tuple[str, ...], *, score: int = 1) -> MergeCandidateSnapshot:
    return MergeCandidateSnapshot(
        candidate_id=candidate_id,
        patch_ref=f"evidence/candidate_patches/{candidate_id}.patch",
        patch_sha256=f"sha-{candidate_id}",
        touched_paths=paths,
        tests_passed=True,
        review_open_blocking_count=0,
        accepted=True,
        score=score,
    )


def test_merge_arbiter_selects_non_overlapping_bundle():
    decision = CandidateMergeArbiter().decide_bundle(
        [
            _candidate("candidate-a", ("src/a.py",)),
            _candidate("candidate-b", ("src/b.py",)),
        ],
        dirty_paths=(),
    )

    assert decision.strategy == "non_overlapping_bundle"
    assert decision.candidate_ids == ("candidate-a", "candidate-b")


def test_merge_arbiter_requires_manual_merge_for_overlapping_candidates():
    decision = CandidateMergeArbiter().decide_bundle(
        [
            _candidate("candidate-a", ("src/a.py",)),
            _candidate("candidate-b", ("src/a.py",)),
        ],
        dirty_paths=(),
    )

    assert decision.strategy == "manual_merge_required"
    assert decision.conflict_candidate_ids == ("candidate-a", "candidate-b")
```

- [ ] **Step 2: Run merge arbiter tests and verify RED**

Run:

```bash
python -m pytest tests/core/test_merge_arbiter.py -q
```

Expected: fail because bundle merge models and `decide_bundle` do not exist.

- [ ] **Step 3: Add merge strategy models**

Modify `src/agos/core/execution.py`:

```python
MergeStrategy = Literal[
    "single_candidate",
    "non_overlapping_bundle",
    "ordered_patch_stack",
    "manual_merge_required",
]


class CandidateBundleDecision(BaseModel):
    id: str
    strategy: MergeStrategy
    candidate_ids: list[str] = Field(default_factory=list)
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    conflict_candidate_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)
```

- [ ] **Step 4: Implement bundle arbiter snapshots and decisions**

Modify `src/agos/core/arbiters.py`:

```python
@dataclass(frozen=True)
class MergeCandidateSnapshot:
    candidate_id: str
    patch_ref: str
    patch_sha256: str
    touched_paths: tuple[str, ...]
    tests_passed: bool
    review_open_blocking_count: int
    accepted: bool
    score: int = 0


@dataclass(frozen=True)
class CandidateBundleMergeDecision:
    strategy: str
    candidate_ids: tuple[str, ...]
    reason: str
    evidence_refs: tuple[str, ...] = ()
    conflict_candidate_ids: tuple[str, ...] = ()
```

Add `CandidateMergeArbiter.decide_bundle()`:

```python
def decide_bundle(self, candidates: list[MergeCandidateSnapshot], *, dirty_paths: Iterable[str]) -> CandidateBundleMergeDecision:
    eligible = [
        candidate
        for candidate in candidates
        if candidate.accepted and candidate.tests_passed and candidate.review_open_blocking_count == 0
    ]
    if not eligible:
        return CandidateBundleMergeDecision(
            strategy="manual_merge_required",
            candidate_ids=(),
            reason="No eligible accepted candidates with passing tests and clean reviews.",
        )

    dirty = {_normalize_path(path) for path in dirty_paths}
    if any(dirty & {_normalize_path(path) for path in candidate.touched_paths} for candidate in eligible):
        return CandidateBundleMergeDecision(
            strategy="manual_merge_required",
            candidate_ids=tuple(candidate.candidate_id for candidate in eligible),
            reason="Dirty governed repo paths overlap candidate patches.",
            conflict_candidate_ids=tuple(candidate.candidate_id for candidate in eligible),
        )

    touched_by: dict[str, str] = {}
    conflicts: set[str] = set()
    for candidate in eligible:
        for path in candidate.touched_paths:
            normalized = _normalize_path(path)
            if normalized in touched_by:
                conflicts.update({touched_by[normalized], candidate.candidate_id})
            touched_by[normalized] = candidate.candidate_id

    if conflicts:
        return CandidateBundleMergeDecision(
            strategy="manual_merge_required",
            candidate_ids=tuple(candidate.candidate_id for candidate in eligible),
            reason="Candidate patches overlap and require manual merge.",
            conflict_candidate_ids=tuple(sorted(conflicts)),
        )

    if len(eligible) == 1:
        return CandidateBundleMergeDecision(
            strategy="single_candidate",
            candidate_ids=(eligible[0].candidate_id,),
            reason="Exactly one eligible accepted candidate.",
            evidence_refs=tuple(candidate.patch_ref for candidate in eligible),
        )

    ordered = tuple(candidate.candidate_id for candidate in sorted(eligible, key=lambda item: (-item.score, item.candidate_id)))
    return CandidateBundleMergeDecision(
        strategy="non_overlapping_bundle",
        candidate_ids=ordered,
        reason="Eligible candidates touch disjoint paths and can be applied as a bundle.",
        evidence_refs=tuple(candidate.patch_ref for candidate in eligible),
    )
```

- [ ] **Step 5: Persist bundle decisions**

Add to `ExecutionStore`:

```python
def write_bundle_decision(self, decision: CandidateBundleDecision) -> str:
    return self._write_model(
        self.execution_dir / "bundle_decisions" / f"{decision.id}.json",
        decision,
        f"execution/bundle_decisions/{decision.id}.json",
    )


def read_bundle_decision(self, decision_id: str) -> CandidateBundleDecision:
    path = self.execution_dir / "bundle_decisions" / f"{decision_id}.json"
    return CandidateBundleDecision.model_validate_json(path.read_text(encoding="utf-8"))
```

- [ ] **Step 6: Add service and CLI bundle commands**

Add service methods:

```python
def decide_candidate_bundle(self, candidate_ids: list[str] | None = None) -> CandidateBundleDecision:
    selected = self._candidate_merge_snapshots(candidate_ids)
    decision = self.merge_arbiter.decide_bundle(
        selected,
        dirty_paths=self.repo.changed_paths(),
    )
    stored = CandidateBundleDecision(
        id=f"bundle-{uuid4().hex[:12]}",
        strategy=decision.strategy,
        candidate_ids=list(decision.candidate_ids),
        reason=decision.reason,
        evidence_refs=list(decision.evidence_refs),
        conflict_candidate_ids=list(decision.conflict_candidate_ids),
    )
    self.execution_store.write_bundle_decision(stored)
    self.ledger.append(
        {
            "type": "candidate_bundle_decided",
            "strategy": stored.strategy,
            "candidate_ids": stored.candidate_ids,
            "evidence_refs": stored.evidence_refs,
        }
    )
    return stored


def apply_candidate_bundle(self, decision_id: str) -> list[CandidatePatch]:
    decision = self.execution_store.read_bundle_decision(decision_id)
    if decision.strategy == "manual_merge_required":
        raise ValueError("manual merge required; bundle cannot be applied automatically")
    candidates = [self.execution_store.read_candidate(candidate_id) for candidate_id in decision.candidate_ids]
    for candidate in candidates:
        self._validate_candidate_apply_guards(candidate)
    for candidate in candidates:
        self._apply_candidate_patch(candidate)
    return [self.execution_store.read_candidate(candidate.id) for candidate in candidates]
```

Update CLI:

```text
agos candidate merge decide [candidate-id...]
agos candidate merge apply <bundle-decision-id>
```

- [ ] **Step 7: Write CLI bundle tests**

Create `tests/cli/test_candidate_merge.py` by reusing the setup style in `tests/cli/test_candidate.py`. The minimum test body must execute these steps:

```text
1. Create an active task with one candidate-stage gate.
2. Create an execution plan with two subtasks whose write scopes do not overlap.
3. Run `agos execute-plan --plan <plan>`.
4. Modify each isolated workspace under its declared scope.
5. Submit, test, candidate-review with empty findings, and accept both candidates.
6. Run `agos candidate merge decide <candidate-a> <candidate-b>`.
7. Assert exit code 0 and stdout contains `non_overlapping_bundle`.
8. Run `agos candidate merge apply <bundle-decision-id>`.
9. Assert both governed repo files changed and both candidates are marked `applied`.
```

- [ ] **Step 8: Run merge tests**

Run:

```bash
python -m pytest tests/core/test_merge_arbiter.py tests/cli/test_candidate_merge.py -q
```

Expected: pass.

- [ ] **Step 9: Run candidate apply regressions**

Run:

```bash
python -m pytest tests/core/test_execution_service.py tests/cli/test_candidate.py -q
python -m ruff check src tests
```

Expected: pass.

- [ ] **Step 10: Commit Task 7**

Run:

```bash
git add src/agos/core/execution.py src/agos/core/arbiters.py src/agos/core/execution_store.py src/agos/core/execution_service.py src/agos/cli/cmd_candidate.py tests/core/test_merge_arbiter.py tests/cli/test_candidate_merge.py
git commit -m "feat: add merge arbiter bundle strategies"
```

## Task 8: End-to-End Multi-Agent Runtime Wiring and Documentation

**Status:** Implemented in `4a8863a`.

**Files:**
- Modify: `src/agos/cli/cmd_execute_plan.py`
- Modify: `src/agos/cli/main.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-06-22-agos-multi-agent-orchestration-design.md`
- Tests:
  - `tests/integration/test_multi_agent_runtime.py`

- [ ] **Step 1: Write end-to-end fake runtime integration test**

Create `tests/integration/test_multi_agent_runtime.py` by adapting the full closed-loop shape from `tests/cli/test_candidate.py`. The test must not call real Codex, Multica, OpenHands, LangGraph, or remote HTTP services. It must use configured fake/local adapters and assert this sequence:

```text
1. `.agos/agos.yaml` contains a fake/local worker, fake/manual reviewer, `native_async` orchestration backend, `max_parallel: 2`, and `max_retries: 1`.
2. `agos execute-plan run --plan <plan>` starts two independent worker subtasks in one runtime tick.
3. `agos execute-plan status <run-id>` reports both worker attempts as running or completed.
4. `agos execute-plan resume <run-id>` reads persisted attempt state instead of starting duplicate workers.
5. Candidate submit/test/review/decide/apply completes for generated patches.
6. `agos candidate merge decide` returns `non_overlapping_bundle` for disjoint accepted candidates.
7. Governed repo files remain unchanged before bundle apply and changed after bundle apply.
8. Runtime state files exist under `.agos/tasks/current/execution/runs/<run-id>/`.
```

- [ ] **Step 2: Run the new integration test and verify RED**

Run:

```bash
python -m pytest tests/integration/test_multi_agent_runtime.py -q
```

Expected: fail until runtime CLI and fake adapter wiring are complete.

- [ ] **Step 3: Wire configured registries into CLI commands**

Update `cmd_execute_plan.py` and `cmd_candidate.py` so CLI commands call:

```python
register_configured_worker_adapters(service)
```

instead of directly registering `LocalWorktreeWorkerAdapter`. Review runtime commands must use:

```python
configured_reviewer_adapters(repo_root)
```

at CLI boundary only.

- [ ] **Step 4: Document runtime commands and config**

Update `README.md` with this config example:

```yaml
workers:
  local_worktree:
    type: local_worktree
  codex:
    type: codex_cli
    command: codex
  multica:
    type: multica
    command: multica
    agent: Lambda
  openhands:
    type: openhands
    endpoint: http://openhands.local
    token: ${OPENHANDS_TOKEN}

reviewers:
  security:
    type: manual
    role: security_reviewer
    required: true

orchestration:
  backend: native_async
  max_parallel: 2
  max_retries: 1
```

Document the external backend contract:

```text
POST /runs
GET /runs/{run_id}
POST /runs/{run_id}/cancel
GET /runs/{run_id}/artifacts
```

Document merge strategies:

```text
single_candidate
non_overlapping_bundle
ordered_patch_stack
manual_merge_required
```

- [ ] **Step 5: Update design docs only where commitments changed**

If implementation adds lifecycle methods beyond the design docs, update:

```text
docs/superpowers/specs/2026-06-22-agos-multi-agent-orchestration-design.md
```

with the normalized backend lifecycle:

```python
class OrchestratorBackend(Protocol):
    name: str
    def start(self, spec: OrchestrationRunSpec) -> OrchestratorRunHandle: ...
    def poll(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus: ...
    def cancel(self, handle: OrchestratorRunHandle) -> OrchestratorRunStatus: ...
    def collect(self, handle: OrchestratorRunHandle) -> dict[str, object]: ...
```

- [ ] **Step 6: Run complete verification**

Run:

```bash
python -m pytest -q
python -m pytest --cov=agos --cov-report=term-missing -q
python -m ruff check src tests
python -m compileall -q src tests
```

Expected:

```text
pytest passes
coverage remains >= 90%
ruff passes
compileall passes
```

- [ ] **Step 7: Commit Task 8**

Run:

```bash
git add src/agos/cli src/agos/core src/agos/adapters README.md docs/superpowers/specs tests/integration/test_multi_agent_runtime.py
git commit -m "docs: document production orchestration runtime"
```

## Final Acceptance

The roadmap is complete when all of these are true:

- `ExecutionWorkerAdapter` supports `prepare/start/poll/cancel/export_candidate`.
- Codex, Multica, and OpenHands workers are configurable at the CLI boundary and mock-tested without real external tools.
- `ReviewerAdapter` supports pluggable parallel reviewers and required-reviewer failure semantics.
- `OrchestratorBackend` has one lifecycle contract across native, LangGraph, and external backends.
- Native graph runtime runs worker, reviewer, and arbiter nodes with dependency ordering, `max_parallel`, retry, resume, cancel, and persisted attempts.
- LangGraph backend executes a real graph when the optional dependency is installed and skips cleanly otherwise.
- External backend can submit, poll, cancel, and collect artifacts from a remote HTTP orchestrator.
- Merge arbiter can choose single candidate, non-overlapping bundle, ordered patch stack, or manual merge required.
- No candidate or bundle mutates the governed repo until every patch hash, scope, test, review, arbiter, dirty-path, and apply-check guard passes.
- Core modules still do not import concrete adapters.
- Full verification passes:

```bash
python -m pytest -q
python -m pytest --cov=agos --cov-report=term-missing -q
python -m ruff check src tests
python -m compileall -q src tests
```
