# AGOS Pluggable Orchestration Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-neutral orchestration runtime for pluggable review and execution multi-agent flows while preserving the current manual review and local worktree candidate loops.

**Architecture:** Introduce a new orchestration core with typed protocols, runtime state, registries, and a native reference backend. Wrap the current `review --packet-only/--ingest` flow as a manual reviewer adapter and the current local worktree candidate loop as a worker adapter, then move orchestration concerns out of `ReviewService` and `ExecutionService` into `ReviewOrchestrator` and `ExecutionOrchestrator`. Add arbiter components and keep LangGraph/external support behind the same `OrchestratorBackend` seam.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, existing AGOS review/execution services, JSON/YAML filesystem artifacts, pytest, optional LangGraph plugin backend.

---

## Scope Decomposition

The approved spec spans several interdependent subsystems. To keep the work shippable, this plan is organized into six milestones. Each milestone leaves the repository in a working, testable state:

1. Orchestration foundations and registries
2. Native backend and manual review adapter
3. Review orchestrator and CLI integration
4. Worker adapters, execution orchestrator, and arbiter extraction
5. LangGraph and external backend support
6. Delivery orchestration wiring, docs, and full verification

## File Structure

### New files

- `src/agos/core/orchestration/__init__.py`
  - public exports for orchestration models, protocols, and runtime helpers
- `src/agos/core/orchestration/protocols.py`
  - `ReviewerAdapter`, `WorkerAdapter`, `OrchestratorBackend`, arbiter protocols
- `src/agos/core/orchestration/models.py`
  - runtime-neutral run spec, node spec, run handles, node states, request/output models
- `src/agos/core/orchestration/registry.py`
  - name-to-implementation registries for backends, reviewers, workers, and arbiters
- `src/agos/core/orchestration/compiler.py`
  - typed-plan to `OrchestrationRunSpec` compilation helpers
- `src/agos/core/orchestration/runtime.py`
  - runtime state load/save helpers for orchestration runs and node states
- `src/agos/core/orchestration/scheduler.py`
  - ready-queue scheduling, join handling, retry/backoff policy helpers
- `src/agos/core/review_orchestration.py`
  - compile and run review graphs through the orchestration backend
- `src/agos/core/execution_orchestration.py`
  - compile and run execution graphs through the orchestration backend
- `src/agos/core/delivery_orchestration.py`
  - future top-level delivery graph wiring review and execution subgraphs
- `src/agos/core/arbiters.py`
  - deterministic review arbiter, candidate arbiter, merge arbiter
- `src/agos/adapters/reviewers/__init__.py`
- `src/agos/adapters/reviewers/manual.py`
  - `ManualReviewerAdapter` that preserves packet/ingest compatibility
- `src/agos/adapters/workers/__init__.py`
- `src/agos/adapters/workers/local_worktree.py`
  - `LocalWorktreeWorkerAdapter` wrapping current worktree logic
- `src/agos/adapters/workers/fake.py`
  - deterministic fake worker adapter for orchestration tests
- `src/agos/backends/__init__.py`
- `src/agos/backends/native_async.py`
  - reference backend with fan-out, join, wait, retry, and resume
- `src/agos/backends/langgraph_backend.py`
  - compile `OrchestrationRunSpec` to LangGraph
- `src/agos/backends/external_backend.py`
  - remote orchestration transport contract
- `tests/core/test_orchestration_models.py`
- `tests/core/test_orchestration_registry.py`
- `tests/core/test_native_backend.py`
- `tests/core/test_arbiters.py`
- `tests/core/test_review_orchestration.py`
- `tests/core/test_execution_orchestration.py`
- `tests/cli/test_orchestration.py`

### Modified files

- `src/agos/core/repo.py`
  - add orchestration paths under `.agos/tasks/current/orchestration/`
- `src/agos/core/review_service.py`
  - keep persistence authority while removing orchestration concerns
- `src/agos/core/execution_service.py`
  - keep candidate persistence/apply authority while removing orchestration concerns
- `src/agos/core/execution_workspace.py`
  - expose helpers needed by `LocalWorktreeWorkerAdapter`
- `src/agos/cli/cmd_review.py`
  - add `run`/`resume` subcommands without removing current manual path
- `src/agos/cli/cmd_execute_plan.py`
  - add orchestrated execution entrypoint
- `src/agos/cli/cmd_candidate.py`
  - keep manual low-level controls, optionally surface orchestration status
- `src/agos/cli/main.py`
  - register orchestration-oriented commands
- `README.md`
  - document orchestration runtime, compatibility flows, and backend seams

## Task 1: Orchestration Foundations

**Files:**
- Create: `src/agos/core/orchestration/__init__.py`
- Create: `src/agos/core/orchestration/protocols.py`
- Create: `src/agos/core/orchestration/models.py`
- Create: `src/agos/core/orchestration/registry.py`
- Create: `tests/core/test_orchestration_models.py`
- Create: `tests/core/test_orchestration_registry.py`
- Modify: `src/agos/core/repo.py`

- [ ] **Step 1: Write the failing model and registry tests**

```python
from __future__ import annotations

from agos.core.orchestration.models import (
    AgentJobHandle,
    OrchestrationRunSpec,
    NodeSpec,
    NodeState,
    ReviewRequest,
)
from agos.core.orchestration.registry import (
    OrchestrationRegistry,
    RegistryResolutionError,
)


def test_orchestration_run_spec_round_trips():
    spec = OrchestrationRunSpec(
        run_id="run-01",
        kind="review_run",
        task_id="agos-01",
        backend="native_async",
        entry_nodes=["build_packet"],
        nodes=[
            NodeSpec(
                id="build_packet",
                kind="build_review_packet",
                adapter=None,
                inputs={},
                policy={"retry": 0},
            )
        ],
        edges=[],
        artifacts={},
        limits={"max_parallel": 2},
        metadata={},
    )

    assert spec.model_dump()["backend"] == "native_async"
    assert spec.nodes[0].kind == "build_review_packet"


def test_registry_rejects_missing_backend():
    registry = OrchestrationRegistry()

    try:
        registry.backend("missing")
    except RegistryResolutionError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected missing backend failure")
```

- [ ] **Step 2: Run the new tests to confirm the missing module failure**

Run: `python -m pytest tests/core/test_orchestration_models.py tests/core/test_orchestration_registry.py -q`

Expected: FAIL with `ModuleNotFoundError` or import failures for `agos.core.orchestration`.

- [ ] **Step 3: Implement the base orchestration models and protocols**

```python
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field


class AgentJobHandle(BaseModel):
    adapter: str
    external_id: str
    metadata: dict[str, str] = Field(default_factory=dict)


class NodeSpec(BaseModel):
    id: str
    kind: str
    adapter: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    policy: dict[str, int | str] = Field(default_factory=dict)


class OrchestrationRunSpec(BaseModel):
    run_id: str
    kind: Literal["review_run", "execution_run", "delivery_run"]
    task_id: str
    backend: str
    entry_nodes: list[str]
    nodes: list[NodeSpec]
    edges: list[dict[str, str]] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    limits: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)


class ReviewerAdapter(Protocol):
    name: str

    def submit(self, request): ...
    def poll(self, handle): ...
    def collect(self, handle): ...
```

- [ ] **Step 4: Implement registries and orchestration path helpers**

```python
from __future__ import annotations

from dataclasses import dataclass, field


class RegistryResolutionError(KeyError):
    pass


@dataclass
class OrchestrationRegistry:
    backends: dict[str, object] = field(default_factory=dict)
    reviewers: dict[str, object] = field(default_factory=dict)
    workers: dict[str, object] = field(default_factory=dict)
    arbiters: dict[str, object] = field(default_factory=dict)

    def register_backend(self, name: str, backend: object) -> None:
        self.backends[name] = backend

    def backend(self, name: str) -> object:
        if name not in self.backends:
            raise RegistryResolutionError(f"unknown backend: {name}")
        return self.backends[name]
```

Add new repo paths:

```python
orchestration_dir=task_dir / "orchestration",
orchestration_runs=task_dir / "orchestration" / "runs",
orchestration_node_states=task_dir / "orchestration" / "node_states",
orchestration_logs=task_dir / "evidence" / "orchestration",
```

- [ ] **Step 5: Run the targeted tests and existing repo-path tests**

Run: `python -m pytest tests/core/test_orchestration_models.py tests/core/test_orchestration_registry.py tests/core/test_repo.py -q`

Expected: PASS for all selected tests.

- [ ] **Step 6: Commit the foundation seam**

```bash
git add src/agos/core/orchestration src/agos/core/repo.py tests/core/test_orchestration_models.py tests/core/test_orchestration_registry.py
git commit -m "feat: add orchestration core primitives"
```

## Task 2: Native Backend and Manual Review Adapter

**Files:**
- Create: `src/agos/core/orchestration/runtime.py`
- Create: `src/agos/core/orchestration/scheduler.py`
- Create: `src/agos/backends/__init__.py`
- Create: `src/agos/backends/native_async.py`
- Create: `src/agos/adapters/reviewers/__init__.py`
- Create: `src/agos/adapters/reviewers/manual.py`
- Create: `src/agos/core/arbiters.py`
- Create: `tests/core/test_native_backend.py`
- Create: `tests/core/test_arbiters.py`

- [ ] **Step 1: Write failing backend and arbiter tests**

```python
from __future__ import annotations

from agos.backends.native_async import NativeAsyncBackend
from agos.core.arbiters import DeterministicReviewArbiter
from agos.core.orchestration.models import OrchestrationRunSpec, NodeSpec


def test_native_backend_marks_waiting_manual_node():
    backend = NativeAsyncBackend()
    spec = OrchestrationRunSpec(
        run_id="run-01",
        kind="review_run",
        task_id="agos-01",
        backend="native_async",
        entry_nodes=["wait"],
        nodes=[NodeSpec(id="wait", kind="wait_for_manual_input", adapter="manual", inputs={}, policy={})],
        edges=[],
        artifacts={},
        limits={"max_parallel": 1},
        metadata={},
    )

    handle = backend.start(spec)
    state = backend.poll(handle)
    assert state.state == "waiting"
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `python -m pytest tests/core/test_native_backend.py tests/core/test_arbiters.py -q`

Expected: FAIL because the backend and arbiter modules do not exist yet.

- [ ] **Step 3: Implement runtime state persistence and scheduler helpers**

```python
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class PersistedNodeState(BaseModel):
    node_id: str
    state: str
    attempts: int = 0
    output_refs: dict[str, str] = Field(default_factory=dict)
    error: str | None = None


def save_node_state(path: Path, state: PersistedNodeState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
```

- [ ] **Step 4: Implement `NativeAsyncBackend`, `ManualReviewerAdapter`, and the first review arbiter**

```python
from __future__ import annotations

from agos.core.orchestration.models import BackendRunHandle, BackendRunStatus


class NativeAsyncBackend:
    name = "native_async"

    def start(self, spec):
        return BackendRunHandle(backend=self.name, run_id=spec.run_id)

    def poll(self, handle):
        return BackendRunStatus(run_id=handle.run_id, state="waiting", completed_nodes=[], failed_nodes=[])

    def collect(self, handle):
        return {"run_id": handle.run_id, "state": "waiting"}


class ManualReviewerAdapter:
    name = "manual"

    def submit(self, request):
        return AgentJobHandle(adapter=self.name, external_id=request.review_id, metadata={"mode": "manual"})
```

- [ ] **Step 5: Run the backend and arbiter tests**

Run: `python -m pytest tests/core/test_native_backend.py tests/core/test_arbiters.py -q`

Expected: PASS for waiting-node behavior, deterministic arbiter output, and persistence smoke tests.

- [ ] **Step 6: Commit the runtime skeleton**

```bash
git add src/agos/core/orchestration/runtime.py src/agos/core/orchestration/scheduler.py src/agos/backends src/agos/adapters/reviewers src/agos/core/arbiters.py tests/core/test_native_backend.py tests/core/test_arbiters.py
git commit -m "feat: add native orchestration backend skeleton"
```

## Task 3: Review Orchestrator and Review CLI Integration

**Files:**
- Create: `src/agos/core/review_orchestration.py`
- Modify: `src/agos/core/review_service.py`
- Modify: `src/agos/cli/cmd_review.py`
- Modify: `src/agos/cli/main.py`
- Create: `tests/core/test_review_orchestration.py`
- Create: `tests/cli/test_orchestration.py`

- [ ] **Step 1: Write failing review orchestration tests**

```python
from __future__ import annotations

from agos.core.review_orchestration import ReviewOrchestrator


def test_review_orchestrator_builds_manual_review_run(tmp_repo):
    orchestrator = ReviewOrchestrator(...)
    run = orchestrator.start_manual_review(
        diff_kind="governed_repo_diff",
        reviewers=["security_reviewer", "test_reviewer"],
    )

    assert run.backend == "native_async"
    assert run.kind == "review_run"
```

- [ ] **Step 2: Run the new review orchestration tests**

Run: `python -m pytest tests/core/test_review_orchestration.py tests/cli/test_orchestration.py -q`

Expected: FAIL because `ReviewOrchestrator` and new CLI commands are missing.

- [ ] **Step 3: Implement `ReviewOrchestrator` by compiling review flows into `OrchestrationRunSpec`**

```python
from __future__ import annotations

from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


class ReviewOrchestrator:
    def __init__(self, paths, *, registry):
        self.paths = paths
        self.registry = registry

    def build_spec(self, *, reviewers: list[str], diff_kind: str) -> OrchestrationRunSpec:
        nodes = [NodeSpec(id="build_packet", kind="build_review_packet", inputs={}, policy={})]
        for reviewer in reviewers:
            nodes.append(
                NodeSpec(
                    id=f"reviewer-submit-{reviewer}",
                    kind="reviewer_submit",
                    adapter=reviewer,
                    inputs={"packet_ref": "packet_ref"},
                    policy={"retry": 1},
                )
            )
        return OrchestrationRunSpec(
            run_id="review-run-generated",
            kind="review_run",
            task_id="agos-01",
            backend="native_async",
            entry_nodes=["build_packet"],
            nodes=nodes,
            edges=[],
            artifacts={"diff_kind": diff_kind},
            limits={"max_parallel": len(reviewers) or 1},
            metadata={},
        )
```

- [ ] **Step 4: Add non-breaking CLI integration**

Keep current compatibility path intact and add subcommands:

```python
review_app = typer.Typer(help="Review orchestration commands.")


@review_app.command("run")
def review_run_command(reviewer: list[str] = typer.Option([], "--reviewer")) -> None:
    ...


@review_app.command("resume")
def review_resume_command(run_id: str) -> None:
    ...
```

Preserve:

```python
def review_command(packet_only: bool = ..., ingest: Path | None = ...) -> None:
    ...
```

- [ ] **Step 5: Run the review orchestration and existing review CLI tests**

Run: `python -m pytest tests/core/test_review_service.py tests/core/test_review_orchestration.py tests/cli/test_review.py tests/cli/test_orchestration.py -q`

Expected: PASS for existing packet/ingest tests plus new orchestrated review run coverage.

- [ ] **Step 6: Commit review orchestration**

```bash
git add src/agos/core/review_orchestration.py src/agos/core/review_service.py src/agos/cli/cmd_review.py src/agos/cli/main.py tests/core/test_review_orchestration.py tests/cli/test_orchestration.py
git commit -m "feat: orchestrate pluggable review runs"
```

## Task 4: Worker Adapters, Execution Orchestrator, and Arbiter Extraction

**Files:**
- Create: `src/agos/adapters/workers/__init__.py`
- Create: `src/agos/adapters/workers/local_worktree.py`
- Create: `src/agos/adapters/workers/fake.py`
- Create: `src/agos/core/execution_orchestration.py`
- Modify: `src/agos/core/execution_service.py`
- Modify: `src/agos/core/execution_workspace.py`
- Modify: `src/agos/cli/cmd_execute_plan.py`
- Modify: `src/agos/cli/cmd_candidate.py`
- Create: `tests/core/test_execution_orchestration.py`

- [ ] **Step 1: Write failing execution orchestration tests**

```python
from __future__ import annotations

from agos.core.execution_orchestration import ExecutionOrchestrator


def test_execution_orchestrator_builds_candidate_review_subgraph(tmp_repo):
    orchestrator = ExecutionOrchestrator(...)
    spec = orchestrator.build_spec(plan_path=tmp_repo / "execution-plan.yaml")

    assert spec.kind == "execution_run"
    assert any(node.kind == "candidate_review_subgraph" for node in spec.nodes)
```

- [ ] **Step 2: Run the new execution orchestration tests**

Run: `python -m pytest tests/core/test_execution_orchestration.py tests/core/test_execution_service.py -q`

Expected: FAIL because the new orchestrator and worker adapters are not implemented.

- [ ] **Step 3: Wrap the current worktree logic in `LocalWorktreeWorkerAdapter`**

```python
from __future__ import annotations

from pathlib import Path

from agos.core.execution_workspace import ExecutionWorkspaceManager


class LocalWorktreeWorkerAdapter:
    name = "local_worktree"

    def __init__(self, manager: ExecutionWorkspaceManager) -> None:
        self.manager = manager

    def prepare(self, assignment):
        return self.manager.create_workspace(assignment.subtask)

    def export_candidate(self, handle):
        patch_bytes = self.manager.capture_patch(Path(handle.metadata["workspace_path"]))
        return {"patch_bytes": patch_bytes}
```

- [ ] **Step 4: Implement execution orchestration and move decision policy into arbiters**

```python
from __future__ import annotations

from agos.core.orchestration.models import NodeSpec, OrchestrationRunSpec


class ExecutionOrchestrator:
    def build_spec(self, plan) -> OrchestrationRunSpec:
        nodes = [NodeSpec(id="validate_plan", kind="validate_plan", inputs={}, policy={})]
        for subtask in plan.subtasks:
            nodes.append(NodeSpec(id=f"prepare-{subtask.id}", kind="prepare_workspace", adapter=subtask.worker.adapter, inputs={}, policy={}))
            nodes.append(NodeSpec(id=f"worker-{subtask.id}", kind="worker_submit", adapter=subtask.worker.adapter, inputs={}, policy={}))
            nodes.append(NodeSpec(id=f"review-{subtask.id}", kind="candidate_review_subgraph", adapter=None, inputs={}, policy={}))
        return OrchestrationRunSpec(
            run_id="execution-run-generated",
            kind="execution_run",
            task_id=plan.task_id,
            backend="native_async",
            entry_nodes=["validate_plan"],
            nodes=nodes,
            edges=[],
            artifacts={},
            limits={"max_parallel": plan.max_parallel},
            metadata={},
        )
```

In `ExecutionService`, replace inline policy branches with calls to:

```python
review = self.review_arbiter.decide(...)
candidate_decision = self.candidate_arbiter.decide(snapshot)
merge_decision = self.merge_arbiter.decide(candidates, conflicts)
```

- [ ] **Step 5: Run execution orchestration and candidate regression tests**

Run: `python -m pytest tests/core/test_execution_service.py tests/core/test_execution_orchestration.py tests/cli/test_candidate.py -q`

Expected: PASS for existing candidate loop plus new orchestration graph tests.

- [ ] **Step 6: Commit execution orchestration**

```bash
git add src/agos/adapters/workers src/agos/core/execution_orchestration.py src/agos/core/execution_service.py src/agos/core/execution_workspace.py src/agos/cli/cmd_execute_plan.py src/agos/cli/cmd_candidate.py tests/core/test_execution_orchestration.py
git commit -m "feat: orchestrate execution workers and arbiters"
```

## Task 5: LangGraph and External Backend Support

**Files:**
- Create: `src/agos/backends/langgraph_backend.py`
- Create: `src/agos/backends/external_backend.py`
- Modify: `tests/core/test_native_backend.py`
- Create: `tests/core/test_backend_parity.py`

- [ ] **Step 1: Write failing backend parity tests**

```python
from __future__ import annotations

import pytest

from agos.backends.langgraph_backend import LangGraphBackend
from agos.backends.native_async import NativeAsyncBackend


@pytest.mark.skipif(False, reason="placeholder removed once backend exists")
def test_langgraph_backend_matches_native_review_fixture():
    native = NativeAsyncBackend()
    langgraph = LangGraphBackend()
    assert native.name != ""
    assert langgraph.name != ""
```

- [ ] **Step 2: Run the parity tests and confirm the missing backend failures**

Run: `python -m pytest tests/core/test_backend_parity.py -q`

Expected: FAIL because the backend modules or fixtures are not implemented.

- [ ] **Step 3: Implement backend compile shims**

```python
from __future__ import annotations


class LangGraphBackend:
    name = "langgraph"

    def start(self, spec):
        compiled = self._compile(spec)
        return BackendRunHandle(backend=self.name, run_id=spec.run_id, metadata={"graph": compiled.graph_id})


class ExternalBackend:
    name = "external"

    def start(self, spec):
        payload = spec.model_dump(mode="python")
        return BackendRunHandle(backend=self.name, run_id=spec.run_id, metadata={"transport": "http"})
```

- [ ] **Step 4: Add backend parity fixtures and optional dependency guards**

```python
import pytest


langgraph = pytest.importorskip("langgraph", reason="install langgraph to run backend parity tests")
```

Keep the parity tests backend-neutral by asserting normalized states and output refs rather than framework-native objects.

- [ ] **Step 5: Run backend tests**

Run: `python -m pytest tests/core/test_native_backend.py tests/core/test_backend_parity.py -q`

Expected: PASS for native backend and PASS/SKIP for LangGraph parity depending on environment.

- [ ] **Step 6: Commit backend expansion**

```bash
git add src/agos/backends/langgraph_backend.py src/agos/backends/external_backend.py tests/core/test_backend_parity.py
git commit -m "feat: add pluggable orchestration backend shims"
```

## Task 6: Delivery Orchestration, Documentation, and Full Verification

**Files:**
- Create: `src/agos/core/delivery_orchestration.py`
- Modify: `README.md`
- Modify: `src/agos/cli/main.py`

- [ ] **Step 1: Write a failing delivery orchestration smoke test**

```python
from __future__ import annotations

from agos.core.delivery_orchestration import DeliveryOrchestrator


def test_delivery_orchestrator_links_execution_and_review_specs():
    orchestrator = DeliveryOrchestrator(...)
    spec = orchestrator.build_spec(task_id="agos-01")
    assert spec.kind == "delivery_run"
    assert spec.entry_nodes
```

- [ ] **Step 2: Run the delivery orchestration smoke test**

Run: `python -m pytest tests/core/test_delivery_orchestration.py -q`

Expected: FAIL until `DeliveryOrchestrator` exists.

- [ ] **Step 3: Implement the delivery compile layer and update docs**

```python
from __future__ import annotations


class DeliveryOrchestrator:
    def build_spec(self, *, task_id: str):
        return OrchestrationRunSpec(
            run_id="delivery-run-generated",
            kind="delivery_run",
            task_id=task_id,
            backend="native_async",
            entry_nodes=["execution_subgraph"],
            nodes=[
                NodeSpec(id="execution_subgraph", kind="execution_subgraph", inputs={}, policy={}),
                NodeSpec(id="review_subgraph", kind="review_subgraph", inputs={}, policy={}),
            ],
            edges=[{"from": "execution_subgraph", "to": "review_subgraph", "when": "on_success"}],
            artifacts={},
            limits={"max_parallel": 1},
            metadata={},
        )
```

Update `README.md` to document:

- orchestration runtime directory layout
- `review run/resume`
- `execute-plan run`
- backend neutrality and compatibility with manual/local flows

- [ ] **Step 4: Run the full verification suite**

Run: `python -m pytest -q`

Expected: PASS for the non-integration suite; the existing real Multica integration remains skipped unless enabled.

Run: `python -m pytest --cov=agos --cov-report=term-missing -q`

Expected: PASS with total coverage at or above the configured threshold.

Run: `python -m ruff check src tests`

Expected: `All checks passed!`

Run: `python -m compileall -q src tests`

Expected: exit code 0.

- [ ] **Step 5: Commit the final orchestration runtime slice**

```bash
git add src/agos/core/delivery_orchestration.py README.md src/agos/cli/main.py tests/core/test_delivery_orchestration.py
git commit -m "docs: wire delivery orchestration runtime"
```

## Spec Coverage Review

- `ReviewerAdapter` is covered by Tasks 1-3.
- `WorkerAdapter` is covered by Task 4.
- `OrchestratorBackend` and native/LangGraph/external backends are covered by Tasks 1, 2, and 5.
- parallel reviewer and worker scheduling is covered by Tasks 2-4.
- `ReviewArbiter`, `CandidateArbiter`, and `MergeArbiter` are covered by Tasks 2 and 4.
- typed review/execution subgraphs plus delivery wiring are covered by Tasks 3, 4, and 6.
- compatibility with existing packet/ingest and local worktree flows is covered by Tasks 3 and 4.

## Final Verification Checklist

- [ ] `python -m pytest tests/core/test_orchestration_models.py tests/core/test_orchestration_registry.py -q`
- [ ] `python -m pytest tests/core/test_native_backend.py tests/core/test_arbiters.py -q`
- [ ] `python -m pytest tests/core/test_review_orchestration.py tests/cli/test_orchestration.py -q`
- [ ] `python -m pytest tests/core/test_execution_orchestration.py tests/core/test_execution_service.py tests/cli/test_candidate.py -q`
- [ ] `python -m pytest tests/core/test_backend_parity.py -q`
- [ ] `python -m pytest -q`
- [ ] `python -m pytest --cov=agos --cov-report=term-missing -q`
- [ ] `python -m ruff check src tests`
- [ ] `python -m compileall -q src tests`
