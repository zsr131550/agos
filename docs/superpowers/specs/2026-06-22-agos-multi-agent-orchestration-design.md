# AGOS Multi-Agent Orchestration Design

**status:** Draft approved for spec capture
**Date:** 2026-06-22
**Author:** AGOS project

## Context

AGOS is a governance layer, not another coding agent. The project already has the
v0.1 local loop:

```text
init -> start -> checkpoint -> ci --local
```

The current core owns task definitions, workflow/gate selection, checkpoint
evidence, a hash-chained task ledger, and local advisory gates. The first
executor adapter is Multica. The core deliberately sees only the stable
`ExecutorAdapter` seam, not the executor's native schema.

Multi-agent orchestration must preserve that boundary:

```text
Agents write or review.
AGOS records, verifies, arbitrates, and enforces.
```

This design adds two orchestration layers:

1. **Review layer** — multiple read-only reviewer agents inspect task evidence,
   diffs, test logs, and gate outputs, then produce normalized findings.
2. **Execution layer** — multiple coding agents work in isolated workspaces,
   each producing candidate patches and evidence that AGOS can test, review, and
   arbitrate.

The Review layer should land first because it is read-only and fits naturally on
top of the existing ledger/evidence model. The Execution layer comes after
review/finding/proof primitives exist, because it introduces workspace isolation,
patch merge, and conflict arbitration.

## Goals

- Add a governance-first multi-agent design without turning AGOS into a coding
  agent implementation.
- Keep the existing v0.1 core stable: task, ledger, evidence, status, gates, and
  executor adapters remain the source of truth.
- Let multiple reviewers produce independent findings while AGOS normalizes,
  deduplicates, and records them.
- Let multiple executors produce isolated candidate patches while AGOS decides
  what can enter the governed repo.
- Make every agent decision auditable through ledger records and evidence refs.
- Keep the first implementation lightweight and filesystem-first.

## Non-Goals

- No cloud dashboard.
- No mandatory LangGraph dependency in the first implementation.
- No direct concurrent writes by multiple agents into the governed working tree.
- No automatic merge of unreviewed candidate patches.
- No replacement for Codex, Gemini CLI, Cline, Aider, OpenHands, Multica, or
  other coding agents.

## Recommended Approach

Use a native lightweight orchestrator first, with a backend interface that can
later be backed by LangGraph or an external orchestration platform.

```text
v0.2: Review orchestration
v0.3: Execution orchestration
v0.4: Optional LangGraph or external orchestrator backend
```

The native orchestrator should be simple:

- A small Python state machine.
- JSON/YAML schemas validated by Pydantic.
- Evidence written to `.agos/tasks/current/evidence/`.
- Task events appended to the existing hash-chained ledger.
- Each agent role represented by an adapter, not hardcoded model calls.

## Architecture

```text
                            AGOS CLI
          review / resolve / closeout / execute-plan
                                |
                                v
                    Orchestration Core
          ---------------------------------------
          |                                     |
          v                                     v
   Review Orchestrator                 Execution Orchestrator
          |                                     |
          v                                     v
   ReviewerAdapter[]                   ExecutorAdapter[]
          |                                     |
          v                                     v
   normalized findings                 candidate patches
          |                                     |
          +---------------+---------------------+
                          |
                          v
                  Arbiter / Policy
                          |
                          v
       ledger records + evidence refs + gate decisions
```

The orchestration layer is above the existing governance core. It calls into
ledger/evidence/gate/status APIs, but those APIs must not depend on any concrete
reviewer, executor, or orchestration backend.

## Review Layer

### Purpose

The Review layer answers:

```text
Given this task, diff, evidence, and gate output, what risks remain?
```

Reviewer agents are read-only. They must not write code, rewrite ledger files,
or mutate the governed repository. Their output is a set of findings.

### Inputs

The Review layer reads:

- `.agos/tasks/current/task.yaml`
- `.agos/tasks/current/status.json`
- `.agos/tasks/current/ledger.jsonl`
- `.agos/tasks/current/evidence/messages/*.jsonl`
- `.agos/tasks/current/evidence/gates/*.log`
- executor-reported patch or candidate patch evidence
- governed repo diff or PR diff
- test output and command logs
- optional acceptance criteria from the task

### Reviewer Roles

The first role set should be stable and small:

| Role | Responsibility |
|---|---|
| `security_reviewer` | Secret exposure, injection, unsafe shelling, auth, permissions, supply-chain risk |
| `test_reviewer` | Missing tests, weak assertions, failure paths, regression risk |
| `architecture_reviewer` | Module boundaries, coupling, compatibility, maintainability |
| `product_reviewer` | Fit against task intent, acceptance criteria, user-visible behavior |
| `arbiter` | Deduplicate findings, calibrate severity, decide blocking status |

Each reviewer can be backed by a model, a human, an external tool, or a local
script. AGOS only consumes the normalized output.

### Review Flow

```text
review_started
  -> build review packet
  -> run reviewer agents in parallel
  -> store raw reviewer outputs
  -> normalize to finding schema
  -> arbiter deduplicates and calibrates
  -> write review report
  -> append review_completed ledger record
  -> block closeout if blocking findings remain open
```

### Review Packet

AGOS should create a deterministic review packet before dispatching reviewers:

```yaml
review_id: review-...
task_id: agos-...
task:
  title: ...
  intent: ...
  acceptance: []
diff:
  kind: governed_repo_diff | candidate_patch | pr_diff
  evidence_ref: candidate_patches/worker-1.patch
ledger:
  head_hash: ...
  checkpoint_refs: []
gates:
  - id: tests_pass
    state: pass
    evidence_ref: gates/tests_pass-....log
```

The review packet is evidence itself. Reviewers should receive the same packet
so findings are comparable.

### Finding Schema

The normalized finding should be compact and evidence-backed:

```yaml
id: finding-...
review_id: review-...
source_agent: security_reviewer
category: security
severity: high
blocking: true
title: Unsafe shell command construction
body: User-controlled input reaches a shell command without structured argv.
location:
  file: src/agos/core/gate.py
  line: 68
evidence_refs:
  - reviews/review-.../packet.json
  - gates/tests_pass-....log
suggested_fix: Use argv execution and avoid shell=True for structured gates.
status: open
resolution: null
```

Finding status values:

```text
open
resolved
accepted_risk
false_positive
superseded
```

Blocking findings can close only through evidence-backed resolution:

- `resolved` requires a new diff/checkpoint/gate evidence ref.
- `accepted_risk` requires an explicit human approval record.
- `false_positive` requires arbiter or human reviewer justification.

## Execution Layer

### Purpose

The Execution layer answers:

```text
Can multiple coding agents independently produce candidate solutions that AGOS
can test, review, and safely arbitrate?
```

Execution agents may write code, but never directly in the governed repo's active
working tree. Every worker gets an isolated workspace.

### Execution Roles

| Role | Responsibility |
|---|---|
| `planner_agent` | Break task into independent subtasks and define write scopes |
| `worker_agent` | Implement one assigned subtask in an isolated workspace |
| `test_agent` | Run verification commands and collect failure evidence |
| `fix_agent` | Apply bounded fixes for failed tests or blocking findings |
| `merge_arbiter` | Compare candidate patches and choose a merge strategy |

### Workspace Isolation

Each worker must run in one of these isolation modes:

1. Git worktree under a task-owned AGOS path.
2. Executor-native isolated workspace, if the adapter provides patch export.
3. Remote branch or PR generated by an external executor platform.

The governed repo should receive only candidate patches after AGOS records:

- subtask assignment
- workspace identity
- produced patch hash
- command/test output
- reviewer findings
- arbiter decision

### Execution Flow

```text
execution_plan_created
  -> planner creates subtask DAG
  -> AGOS validates disjoint write scopes where possible
  -> AGOS creates isolated workspace per worker
  -> workers run in parallel
  -> workers submit patch + summary + evidence
  -> test_agent runs gates against candidates
  -> Review layer reviews candidates
  -> merge_arbiter selects, combines, or rejects candidates
  -> accepted patch enters governed repo through a controlled apply step
  -> closeout requires gates + review findings + proof
```

### Subtask Schema

```yaml
id: subtask-...
task_id: agos-...
title: Add structured command execution
intent: Replace unsafe shell command usage in gate execution.
depends_on: []
write_scope:
  - src/agos/core/gate.py
  - tests/core/test_gate.py
agent:
  role: worker_agent
  adapter: codex
status: pending
```

The first implementation should prefer explicit write scopes. If scopes overlap,
AGOS should serialize those subtasks or require a merge arbiter review before
patch application.

### Candidate Patch Schema

```yaml
id: candidate-...
task_id: agos-...
subtask_id: subtask-...
source_agent: worker_agent
workspace:
  kind: git_worktree
  ref: .agos/tasks/current/workspaces/subtask-...
patch:
  evidence_ref: candidate_patches/candidate-....patch
  sha256: ...
summary: ...
test_refs:
  - gates/tests_pass-....log
status: proposed
```

Candidate statuses:

```text
proposed
testing
reviewing
accepted
rejected
superseded
applied
```

### Merge Arbitration

The merge arbiter must not blindly choose the largest patch or the first passing
patch. It should evaluate:

- Does the patch satisfy the task acceptance criteria?
- Did required gates pass?
- Are blocking review findings open?
- Does the patch stay inside the declared write scope?
- Does it conflict with another accepted candidate?
- Is the patch simpler than alternatives?

Arbiter decisions are ledgered:

```yaml
type: candidate_selected
candidate_id: candidate-...
reason: tests pass, no blocking findings, smallest compatible patch
evidence_refs:
  - candidate_patches/candidate-....patch
  - reviews/review-....json
  - gates/tests_pass-....log
```

## Combined Lifecycle

The full future lifecycle is:

```text
agos start
  -> planner creates subtasks
  -> execution workers produce isolated candidates
  -> checkpoint captures executor activity
  -> gates run against candidates or governed repo diff
  -> review agents inspect evidence and diffs
  -> arbiter opens blocking/non-blocking findings
  -> resolve requires evidence-backed fixes
  -> closeout emits proof.md + proof.json
  -> merge-gate CI verifies proof and blocks unsafe merge
```

Review can exist without Execution. Execution must not bypass Review once Review
is enabled for the workflow.

## New Interfaces

### ReviewerAdapter

```python
class ReviewerAdapter(Protocol):
    name: str

    def review(self, packet: ReviewPacket) -> ReviewOutput:
        ...
```

The adapter can call Codex review, Claude, Gemini, PR-Agent, a human review
import, or a local script. The core only sees `ReviewOutput`.

### OrchestratorBackend

```python
class OrchestratorBackend(Protocol):
    name: str

    def run_review(self, packet: ReviewPacket, reviewers: list[ReviewerSpec]) -> ReviewRun:
        ...

    def run_execution_plan(self, plan: ExecutionPlan) -> ExecutionRun:
        ...
```

The first backend is native Python. A later backend can translate the same specs
into LangGraph nodes.

## Filesystem Layout

Add these directories under the active task:

```text
.agos/tasks/current/
  reviews/
    review-.../
      packet.json
      raw/
        security_reviewer.json
        test_reviewer.json
      findings.json
      report.md
  execution/
    plan.json
    subtasks/
      subtask-....json
    candidates/
      candidate-....json
  evidence/
    candidate_patches/
      candidate-....patch
    orchestration/
      review-....log
      execution-....log
```

The task ledger remains the chronological source of truth. Files under
`reviews/`, `execution/`, and `evidence/` are evidence artifacts referenced by
ledger records.

## Ledger Events

Add these event types:

```text
review_started
reviewer_completed
review_completed
finding_opened
finding_resolved
finding_accepted_risk
execution_plan_created
subtask_started
subtask_completed
candidate_patch_created
candidate_tested
candidate_reviewed
candidate_selected
candidate_applied
candidate_rejected
```

Every event that changes governance state must include evidence refs and the
current ledger head hash.

## Commands

Recommended CLI growth:

```text
agos review [--candidate <id>] [--all-candidates] [--ingest <file>]
agos resolve <finding-id> --evidence <ref> [--status resolved|accepted-risk|false-positive]
agos closeout
agos execute-plan [--parallel N]
agos candidate list
agos candidate apply <candidate-id>
```

Implementation order:

1. `agos review --ingest` for human/imported findings.
2. `agos review` with native reviewer adapters.
3. `agos resolve` with evidence-backed closure.
4. `agos closeout` proof generation.
5. `agos execute-plan` with isolated candidate patches.

## Error Handling

- Reviewer timeout: record `reviewer_completed` with state `failed`; arbiter can
  mark review incomplete. Required reviewer failure blocks closeout.
- Malformed reviewer output: store raw output, emit parse error finding, block if
  reviewer is required.
- Worker timeout: mark subtask blocked; do not apply partial patch unless a
  human explicitly imports it as a candidate.
- Patch conflict: mark candidate rejected or require merge arbiter resolution.
- Gate failure: candidate remains proposed or rejected; failure log is evidence.
- Ledger mismatch: hard block all orchestration commands until the chain is
  repaired or the task is cleared by explicit user action.

## Testing Strategy

### Review Layer Tests

- Review packet generation is deterministic.
- Multiple reviewer outputs normalize into one finding schema.
- Arbiter deduplicates equivalent findings.
- Blocking findings prevent closeout.
- Evidence-backed resolution closes a blocking finding.
- Accepted risk requires explicit human approval metadata.

### Execution Layer Tests

- Planner-created subtasks validate write scopes.
- Workers cannot write directly into the governed repo in native mode.
- Candidate patch hash matches the stored patch file.
- Gate failure prevents candidate selection.
- Conflicting candidates require arbiter decision.
- Applying a candidate records `candidate_applied` and preserves evidence refs.

### Integration Tests

- Review-only loop: start -> checkpoint -> review -> resolve -> closeout.
- Execution loop with fake worker adapters: start -> execute-plan -> candidate
  patch -> gates -> review -> apply.
- Optional real executor smoke tests remain opt-in like the current Multica test.

## Roadmap

| Version | Scope |
|---|---|
| v0.2 | Review layer, finding schema, evidence-backed resolve, closeout proof |
| v0.3 | Execution layer, isolated candidate patches, merge arbitration |
| v0.4 | Optional LangGraph backend, external orchestrator integration |
| v1.0 | Stable multi-agent governance protocol with server-side merge gate |

## Acceptance Criteria

- Reviewers are read-only and cannot mutate the governed repo.
- Findings are normalized, evidence-backed, and ledgered.
- Blocking findings prevent closeout until resolved or explicitly accepted.
- Execution workers run in isolated workspaces.
- Candidate patches are hash-addressed evidence artifacts.
- No candidate patch is applied without gate results and an arbiter decision.
- Review and Execution orchestration can run with fake adapters in tests.
- The governance core remains independent of concrete reviewer/executor backends.
