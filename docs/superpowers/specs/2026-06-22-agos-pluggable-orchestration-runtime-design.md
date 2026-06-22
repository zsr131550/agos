# AGOS Pluggable Orchestration Runtime Design

**Status:** Draft approved for spec capture
**Date:** 2026-06-22
**Author:** AGOS project
**Scope:** Review + execution orchestration architecture after v0.3 candidate closed loop

## Goal

Turn AGOS review and execution from command-oriented services into a pluggable multi-agent orchestration system without collapsing the governance core into any one runtime, model vendor, or graph framework.

The target outcome is:

```text
typed domain plans
  -> compiled orchestration run spec
  -> pluggable backend runtime
  -> reviewer/worker adapters
  -> arbiter decisions
  -> governed ledger/evidence/proof
```

This design preserves AGOS's thesis:

```text
Agents write or review.
AGOS records, verifies, arbitrates, and enforces.
```

## Context

AGOS already has:

- v0.1 local governance loop: `init`, `start`, `checkpoint`, `ci --local`
- v0.2 review lifecycle: packet creation, finding ingest, evidence-backed resolution, closeout proof
- v0.3 execution lifecycle: isolated worktrees, candidate patches, candidate tests, candidate review bindings, arbiter decisions, guarded apply

What it does not yet have is a true orchestration layer:

- no pluggable reviewer interface in code
- no pluggable worker interface in code
- no unified orchestration backend contract
- no runtime for fan-out, joins, resume, or backend replacement
- no independent arbiter layer
- no LangGraph or external orchestrator backend

Today `ReviewService` and `ExecutionService` are mostly domain services plus some orchestration logic. The next step is to split:

```text
domain facts and governance writes
from
multi-agent runtime control
```

## Non-Goals

- No attempt to make AGOS itself a coding agent
- No direct mutation of the governed repo by remote workers or reviewer backends
- No generic end-user graph DSL in the first iteration
- No backend-specific state leaking into core ledger or review/execution schemas
- No replacement of the current working flows before an equivalent native backend exists
- No mandatory LangGraph dependency for the core package

## Design Choice

Adopt **typed domain graphs compiled into a shared orchestration runtime spec**, with **pluggable backends**.

This rejects two weaker alternatives:

### Alternative A: LangGraph-first orchestration

Pros:

- Fastest path to a working graph
- Good built-in branching and resume concepts

Cons:

- Risks coupling AGOS core to LangGraph node/state semantics
- Makes AGOS harder to test independently of one runtime
- Pushes backend choice too low in the stack

### Alternative B: Generic graph DSL first

Pros:

- Maximum theoretical flexibility
- Future-proof on paper

Cons:

- Prematurely abstract for the current project size
- High design cost before shipping usable orchestration
- Harder for humans to read than domain-specific review/execution plans

### Chosen approach: typed domain graphs + shared runtime spec

Pros:

- Keeps Review and Execution readable as product concepts
- Makes backend replacement explicit and testable
- Preserves AGOS domain invariants independently of runtime choice
- Gives LangGraph a clean compiler target rather than making it the core

## Architectural Principles

### 1. Domain services write facts; runtimes schedule work

`ReviewService` and `ExecutionService` must become the places that validate and persist governance facts:

- packets
- findings
- candidate metadata
- test results
- decisions
- ledger events

They must not own long-running fan-out, backend polling, or node scheduling.

### 2. Runtime boundaries must be serializable

The backend boundary must be a JSON-serializable orchestration spec plus opaque run handles and normalized events. Backends must not receive Python callables, open file handles, or framework-native graph objects from the core.

### 3. Adapters describe agent capabilities; arbiters make governance decisions

Workers and reviewers produce evidence. Arbiters decide what that evidence means for AGOS.

### 4. The governed repo remains the only apply authority

Remote or external backends may review, plan, or produce candidate material, but only AGOS-controlled apply steps may write candidate patches into the governed working tree.

### 5. Resume is a first-class requirement

Every orchestration run must be restartable from disk and ledger state. Node execution must be idempotent or explicitly guarded against duplicate effects.

## Target Architecture

```text
CLI / future API
  -> ReviewOrchestrator / ExecutionOrchestrator / DeliveryOrchestrator
  -> OrchestrationCompiler
  -> OrchestrationRunSpec
  -> OrchestratorBackend
  -> ReviewerAdapter / WorkerAdapter / Arbiter implementations
  -> ReviewService / ExecutionService / Ledger / Evidence / Repo
```

### Layer responsibilities

| Layer | Responsibility |
|---|---|
| CLI | user-facing commands, input validation, progress display |
| Orchestrators | domain-specific graph assembly and run supervision |
| Compiler | turn typed plans into backend-neutral run specs |
| Backend | schedule nodes, fan-out, join, wait, retry, resume |
| Adapters | talk to external reviewers or workers |
| Arbiters | make AGOS decisions from normalized evidence |
| Domain services | validate and persist AGOS facts |

## New Core Interfaces

All new interfaces live in a new orchestration package and are imported by orchestrators, not by low-level storage layers.

### ReviewerAdapter

```python
class ReviewerAdapter(Protocol):
    name: str

    def submit(self, request: ReviewRequest) -> AgentJobHandle:
        ...

    def poll(self, handle: AgentJobHandle) -> AgentJobStatus:
        ...

    def collect(self, handle: AgentJobHandle) -> ReviewerOutput:
        ...
```

Purpose:

- represent one reviewer backend such as manual ingest, Codex review, PR-Agent, or external review API
- normalize all reviewer execution into one lifecycle: submit -> poll -> collect

Initial implementations:

- `ManualReviewerAdapter`
- `CodexReviewerAdapter` later
- `ExternalReviewImporterAdapter` later

### WorkerAdapter

```python
class WorkerAdapter(Protocol):
    name: str

    def prepare(self, assignment: WorkerAssignment) -> WorkerBinding:
        ...

    def submit(self, assignment: WorkerAssignment) -> AgentJobHandle:
        ...

    def poll(self, handle: AgentJobHandle) -> AgentJobStatus:
        ...

    def export_candidate(self, handle: AgentJobHandle) -> CandidateMaterialization:
        ...
```

Purpose:

- represent one candidate-producing worker backend
- separate workspace preparation from execution and candidate export

Initial implementations:

- `LocalWorktreeWorkerAdapter`
- `FakeWorkerAdapter` for tests

Future implementations:

- `CodexWorktreeWorkerAdapter`
- `MulticaPatchExportWorkerAdapter`
- `RemoteBranchWorkerAdapter`

### ReviewArbiter

```python
class ReviewArbiter(Protocol):
    name: str

    def decide(
        self,
        packet: ReviewPacket,
        outputs: list[ReviewerOutput],
    ) -> ArbiterResult:
        ...
```

Purpose:

- deduplicate findings across reviewers
- normalize severity and blocking
- decide report completeness or failure

### CandidateArbiter

```python
class CandidateArbiter(Protocol):
    name: str

    def decide(self, snapshot: CandidateSnapshot) -> CandidateDecision:
        ...
```

Purpose:

- determine whether one candidate satisfies policy for acceptance
- keep test/review/guard reasoning out of `ExecutionService`

### MergeArbiter

```python
class MergeArbiter(Protocol):
    name: str

    def decide(
        self,
        candidates: list[CandidateSnapshot],
        conflicts: ConflictMatrix,
    ) -> MergeDecision:
        ...
```

Purpose:

- reason across multiple accepted candidates
- choose ordering, rejection, or one-candidate selection
- keep merge strategy explicit and reviewable

### OrchestratorBackend

```python
class OrchestratorBackend(Protocol):
    name: str

    def start(self, spec: OrchestrationRunSpec) -> BackendRunHandle:
        ...

    def poll(self, handle: BackendRunHandle) -> BackendRunStatus:
        ...

    def collect(self, handle: BackendRunHandle) -> BackendRunResult:
        ...
```

Purpose:

- unify native, LangGraph, and external runtime implementations
- provide a single runtime seam for orchestration execution

## Shared Runtime Model

The orchestration runtime is backend-neutral and deliberately small.

### OrchestrationRunSpec

```yaml
run_id: orchestration-run-01
kind: review_run | execution_run | delivery_run
task_id: agos-...
backend: native_async
entry_nodes:
  - build_packet
nodes: [...]
edges: [...]
artifacts: {...}
limits:
  max_parallel: 4
metadata: {...}
```

### NodeSpec

Each node is one unit of work with one action kind:

```yaml
id: reviewer-security
kind: reviewer_submit
inputs:
  packet_ref: reviews/review-01/packet.json
adapter: security_reviewer
policy:
  retry: 2
  timeout_seconds: 300
```

Supported node kinds in the first design:

- `build_review_packet`
- `reviewer_submit`
- `reviewer_collect`
- `review_arbiter`
- `persist_review_report`
- `prepare_workspace`
- `worker_submit`
- `worker_collect_candidate`
- `candidate_test`
- `candidate_review_subgraph`
- `candidate_arbiter`
- `merge_arbiter`
- `guarded_apply`
- `wait_for_manual_input`

### EdgeSpec

Edges express only simple control flow:

- `on_success`
- `on_failure`
- `on_completed`
- `fanout`
- `join`

No user-authored edge expressions are needed in the first version. Review and execution plans are compiled into these edges by orchestrators.

### RunState and NodeState

Persist runtime state under the active task:

```text
.agos/tasks/current/orchestration/
  runs/
    orchestration-run-01.json
  node_states/
    orchestration-run-01/
      build_packet.json
      reviewer-security.json
```

Node states:

```text
pending
running
waiting
completed
failed
blocked
cancelled
```

Each node state stores:

- node id
- state
- attempt count
- input artifact refs
- output artifact refs
- backend handle ref if any
- timestamps
- error summary if failed

## Typed Orchestrators

The runtime spec is internal. Humans and CLI commands should still work in typed domain concepts.

### ReviewOrchestrator

Input:

- active task
- selected reviewers
- arbiter
- diff kind and evidence refs

Compiled graph:

```text
build_packet
  -> reviewer_submit fanout
  -> reviewer_collect fan-in
  -> review_arbiter
  -> persist_review_report
```

Outputs:

- one `ReviewReport`
- reviewer raw output refs
- arbiter result
- ledgered review completion

### ExecutionOrchestrator

Input:

- active task
- execution plan
- worker adapter selection
- candidate arbiter
- merge arbiter

Compiled graph:

```text
validate_plan
  -> prepare_workspace fanout
  -> worker_submit fanout
  -> worker_collect_candidate fan-in
  -> candidate_test fanout per candidate
  -> candidate_review_subgraph per candidate
  -> candidate_arbiter per candidate
  -> merge_arbiter
  -> guarded_apply
```

Outputs:

- candidate patches
- candidate review bindings
- candidate decisions
- merge decision
- optional apply result

### DeliveryOrchestrator

Later, a top-level delivery graph can compose both:

```text
execution subgraph
  -> review subgraph
  -> closeout
  -> merge-gate preparation
```

This is the future place where review and execution become one end-to-end orchestration graph without merging their domain services.

## Review Orchestration Details

### ReviewRequest and ReviewerOutput

```python
class ReviewRequest(BaseModel):
    review_id: str
    packet_ref: str
    reviewer_role: str
    adapter: str
    ledger_head_hash: str

class ReviewerOutput(BaseModel):
    review_id: str
    reviewer_role: str
    raw_ref: str
    findings: list[Finding]
    summary: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)
```

Rules:

- reviewer outputs are always captured as raw evidence before arbiter processing
- reviewers may return zero findings
- malformed outputs are stored as raw evidence and converted into runtime failure or arbiter-visible parse failure

### Manual review compatibility

The current `review --packet-only` and `review --ingest` flow must survive as a first adapter:

- `ManualReviewerAdapter.submit()` records a waiting job
- `wait_for_manual_input` node pauses the graph
- `review --ingest` or future `review resume` satisfies the waiting node
- `collect()` loads the normalized findings supplied by the human or external tool

This allows AGOS to gain orchestrator structure without breaking current workflows.

### Native reviewer fan-out

The first native review orchestration only requires:

- parallel submit to multiple adapters
- polling until all required reviewers finish or a quorum policy is reached
- collection of all completed outputs
- arbiter deduplication and final report write

The initial policy should be strict:

- all required reviewers must complete successfully
- optional reviewers may fail without blocking report generation

## Execution Orchestration Details

### WorkerAssignment and CandidateMaterialization

```python
class WorkerAssignment(BaseModel):
    task_id: str
    subtask_id: str
    worker_role: str
    adapter: str
    workspace_ref: str | None = None
    write_scope: list[str]
    context_refs: list[str] = Field(default_factory=list)

class CandidateMaterialization(BaseModel):
    subtask_id: str
    patch_bytes_ref: str
    patch_sha256: str
    summary: str
    metadata: dict[str, str] = Field(default_factory=dict)
```

### Local worktree compatibility

The current local execution path maps naturally:

- `prepare()` creates the worktree
- human or fake worker edits the worktree
- `export_candidate()` captures the patch and summary

This becomes `LocalWorktreeWorkerAdapter`.

### Why worker adapters are separate from executor adapters

The existing `ExecutorAdapter` is about long-running governed task execution and checkpoint streaming. Candidate-producing worker orchestration has different semantics:

- workspace preparation
- candidate export
- bounded subtask write scopes
- per-candidate review/test/apply

For that reason, `WorkerAdapter` is a new interface rather than an extension of `ExecutorAdapter`.

### Candidate review as a reusable subgraph

Each candidate review should invoke the same `ReviewOrchestrator` through a compiled subgraph, not via a one-off imperative method call.

This means execution orchestration does not special-case review; it depends on a review subgraph contract.

### Merge arbiter

`merge_arbiter` must be a real independent stage, not just a future note.

Inputs:

- all candidates with `accepted` from candidate-level arbitration
- patch path sets
- conflict matrix
- optional candidate scores or priorities

Outputs:

- selected candidates
- apply order if multiple non-conflicting candidates are allowed
- rejected candidates with reasons

The first implementation may be conservative:

- allow one accepted candidate by default
- allow multiple only when patch path sets are disjoint

## Arbiter Layer

Arbiters must live in their own package, not inside domain services.

### DeterministicReviewArbiter

Responsibilities:

- merge repeated findings from multiple reviewers
- normalize severities according to AGOS rules
- choose blocking flags consistently
- emit one canonical `ReviewReport`

The first implementation should be rule-based and deterministic for tests.

### RuleBasedCandidateArbiter

Responsibilities:

- verify passed tests exist for required gates
- verify required review binding exists and is current
- verify candidate status may move to `accepted`
- produce machine-readable reasons for rejection or `needs_changes`

### ConflictAwareMergeArbiter

Responsibilities:

- build conflict matrix from patch path sets
- reject overlapping accepted candidates unless a future merge strategy allows resolution
- choose apply order for disjoint accepted candidates

## Native Backend

The first backend is `NativeAsyncBackend`. It is the semantic reference implementation.

### Responsibilities

- ready-queue scheduling
- bounded parallelism
- node retries and backoff
- wait/resume for manual input
- run and node state persistence
- deterministic replay from disk

### Supported primitives

- fan-out
- join
- barrier
- wait for external input
- retry with backoff
- cancellation

### Persistence

Every state transition is written under `.agos/tasks/current/orchestration/` and ledgered at domain boundaries. The runtime may write its own operational logs under:

```text
.agos/tasks/current/evidence/orchestration/
```

### Resume model

On restart, the backend reloads:

- run spec
- node states
- unresolved backend handles
- waiting manual-input nodes

Then it resumes only incomplete nodes.

## LangGraph Backend

LangGraph should be a backend, not a foundational core dependency.

### Compilation strategy

`LangGraphBackend` compiles `OrchestrationRunSpec` into a `StateGraph`:

- fan-out groups become parallel branches
- joins become reducer or wait nodes
- `wait_for_manual_input` maps to interrupt/resume
- node outputs remain artifact refs, not in-memory payloads

### Rules

- LangGraph state must be treated as backend-local state
- AGOS ledger, review, candidate, and proof schemas remain authoritative
- switching away from LangGraph must not require changing review or execution domain objects

### Why this matters

If LangGraph is only a backend, AGOS can:

- test all orchestration semantics against `NativeAsyncBackend`
- use LangGraph only where its ergonomics help
- avoid core drift when LangGraph APIs change

## External Orchestrator Backend

An external backend is for cases where orchestration itself runs elsewhere.

### Contract

AGOS sends:

- `OrchestrationRunSpec`
- artifact refs or exported evidence payloads
- adapter names and policies

The external system returns:

- backend run handle
- normalized node events
- raw reviewer or worker outputs as evidence refs or payloads
- completion state

### Hard constraints

- external backends may not directly apply patches to the governed repo
- they may only return candidate materialization and reviewer outputs
- final AGOS accept/apply/closeout decisions remain local and governed

This preserves AGOS as the governance authority even when the runtime is remote.

## Registry and Configuration

Add registries for backends, reviewers, workers, and arbiters.

```yaml
orchestration:
  backend: native_async
  reviewers:
    security_reviewer:
      adapter: manual
    test_reviewer:
      adapter: manual
  workers:
    local_worktree:
      adapter: local_worktree
  arbiters:
    review: deterministic
    candidate: rule_based
    merge: conflict_aware
```

Rules:

- registries resolve names to implementations
- missing required implementations block run creation
- configuration chooses defaults but plans may override within allowed policy

## Proposed File Layout

```text
src/agos/core/orchestration/
  __init__.py
  protocols.py
  models.py
  registry.py
  compiler.py
  runtime.py
  scheduler.py

src/agos/core/review_orchestration.py
src/agos/core/execution_orchestration.py
src/agos/core/delivery_orchestration.py
src/agos/core/arbiters.py

src/agos/adapters/reviewers/
  __init__.py
  manual.py
  codex.py

src/agos/adapters/workers/
  __init__.py
  local_worktree.py
  fake.py
  codex_worktree.py

src/agos/backends/
  __init__.py
  native_async.py
  langgraph_backend.py
  external_backend.py
```

## CLI Evolution

The current command shapes should evolve without breaking existing workflows.

### Review

Current:

```text
agos review --packet-only
agos review --ingest findings.json --review-id review-...
```

Future additions:

```text
agos review run --reviewer security_reviewer --reviewer test_reviewer
agos review resume <run-id>
```

`--packet-only` and `--ingest` remain supported through the manual reviewer path.

### Execution

Current:

```text
agos execute-plan --plan execution-plan.yaml
agos candidate submit ...
agos candidate test ...
agos candidate review ...
agos candidate decide ...
agos candidate apply ...
```

Future additions:

```text
agos execute-plan run --plan execution-plan.yaml
agos orchestration status <run-id>
agos orchestration resume <run-id>
```

The current candidate commands can remain as low-level manual control paths even after full orchestration exists.

## Migration Plan

### Phase 1: extract orchestration primitives

- add orchestration protocols and models
- add implementation registries
- add native backend skeleton
- keep current CLI behavior unchanged

### Phase 2: wrap current manual review path

- implement `ManualReviewerAdapter`
- compile current review packet/ingest flow into review run specs
- keep `ReviewService` as packet/report persistence authority

### Phase 3: wrap current local execution path

- implement `LocalWorktreeWorkerAdapter`
- move current worktree logic behind worker adapter interface
- keep `ExecutionService` as candidate/test/decision/apply persistence authority

### Phase 4: add independent arbiters

- move report deduplication and candidate acceptance logic into arbiters
- make execution orchestrator call arbiters instead of embedding policy inline

### Phase 5: add real run supervision

- native fan-out, join, resume, backoff
- orchestration status/resume commands

### Phase 6: add LangGraph backend

- compile runtime spec to LangGraph graph
- prove parity with native backend on fixture runs

### Phase 7: add external backend

- remote run handle lifecycle
- normalized event import
- local final governance enforcement

## Testing Strategy

### Protocol and registry tests

- adapters register and resolve by name
- unknown adapters or backends fail at run creation

### Native backend tests

- fan-out respects `max_parallel`
- join waits for required branches
- waiting manual input resumes cleanly
- failed nodes retry with bounded backoff
- run state reload resumes incomplete nodes only

### Review orchestration tests

- multiple reviewer outputs produce one arbited report
- reviewer raw outputs are persisted before arbiter execution
- optional reviewer failure does not block if policy permits
- required reviewer failure blocks run completion

### Execution orchestration tests

- multiple worker assignments produce isolated candidate materializations
- candidate review subgraph uses the same review runtime contract
- candidate arbiter and merge arbiter decisions are persisted and replayable
- guarded apply remains local and authoritative

### Backend parity tests

- one fixture review run executes identically on native and LangGraph backends
- one fixture execution run executes identically on native and LangGraph backends

## Acceptance Criteria

This design is successful when all are true:

1. Review and execution orchestration both run through a backend-neutral runtime seam.
2. Reviewers and workers are selected through pluggable interfaces rather than hardcoded classes.
3. `ReviewService` and `ExecutionService` persist facts but no longer own scheduling logic.
4. A native backend can fan out multiple reviewers and multiple workers with resume support.
5. Candidate review is implemented as a reusable review subgraph, not a one-off imperative bridge.
6. Review, candidate, and merge decisions are made by explicit arbiter components.
7. LangGraph can be added as a backend without changing AGOS domain schemas.
8. External orchestration may run remotely without receiving local apply authority.
9. Current manual review and local worktree flows remain available as compatibility adapters during migration.

## Open Questions

None blocking this design. Future implementation decisions may refine:

- whether review completion requires all reviewers or allows role-based quorum policies
- whether merge arbiter v1 allows multiple disjoint accepted candidates in one apply batch or one at a time only
- whether external backends return artifact payloads directly or always via AGOS-imported evidence refs
