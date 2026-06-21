# AGOS Execution Agent Orchestration Design

**Status:** Draft approved for spec capture
**Date:** 2026-06-22
**Scope:** v0.3 minimal closed loop

## Context

AGOS is a governance layer. It records, verifies, arbitrates, and enforces; it
does not become another coding agent.

The v0.2 Review layer is already in place. It creates deterministic review
packets, ingests normalized findings, resolves findings with evidence, and
blocks closeout while blocking findings remain open.

The execution layer should now add the smallest useful closed loop:

```text
execution plan
  -> isolated worktree
  -> candidate patch
  -> candidate test
  -> candidate review
  -> arbiter decision
  -> controlled apply
```

The first backend is local git worktrees plus fake/local workers. This lets AGOS
prove the candidate patch protocol before connecting Codex, Multica, LangGraph,
or remote worker platforms.

## Goals

- Add execution orchestration without weakening Review layer guarantees.
- Ensure workers never write directly to the governed main working tree.
- Represent each worker output as a hash-addressed candidate patch.
- Test, review, decide, and apply candidates through explicit commands.
- Ledger every state transition that affects governance.
- Keep the first implementation filesystem-first and easy to test.

## Non-Goals

- No automatic code generation by AGOS.
- No LangGraph dependency.
- No automatic merge of multiple competing patches.
- No direct concurrent writes into the governed working tree.
- No replacement for Codex, Multica, Aider, Cline, OpenHands, or other coding
  agents.
- No cloud coordination service.

## Recommended Approach

Use a native minimal closed-loop execution orchestrator:

1. `agos execute-plan --plan <file>` validates a plan and creates isolated
   workspaces for subtasks.
2. A fake/local worker or human/agent edits only the isolated worktree.
3. `agos candidate submit <subtask-id>` captures the worktree diff as a
   candidate patch evidence artifact.
4. `agos candidate test <candidate-id>` applies the candidate in a temporary
   verification workspace and records gate output.
5. `agos candidate review <candidate-id>` reuses the Review layer with
   `diff_kind="candidate_patch"`.
6. `agos candidate decide <candidate-id>` records an arbiter decision.
7. `agos candidate apply <candidate-id>` verifies all guards and applies the
   patch to the governed repo.

This keeps the first version useful even before real coding-agent adapters are
connected.

## Architecture

```text
                          AGOS CLI
       execute-plan / candidate submit / test / review / decide / apply
                              |
                              v
                    Execution Orchestration Core
                              |
          +-------------------+-------------------+
          |                                       |
          v                                       v
   Workspace Manager                      Candidate Store
   git worktree creation                  patches + metadata
          |                                       |
          v                                       v
   isolated worker dirs             tests + Review reports + decisions
          |                                       |
          +-------------------+-------------------+
                              |
                              v
                    Controlled Apply Guard
                              |
                              v
                  governed repo + task ledger
```

The execution core depends on existing task, status, ledger, repo, gates, and
Review services. The Review layer remains generic: candidate reviews pass patch
evidence plus optional subject/context refs, but Review does not import
execution models or mutate execution state.

## Core Models

### ExecutionPlan

An execution plan is a user-authored or planner-authored document:

```yaml
id: execution-plan-01
task_id: agos-...
max_parallel: 2
requires_candidate_review: true
subtasks:
  - id: subtask-core-models
    title: Add execution models
    intent: Define execution plan, candidate, and decision schemas.
    depends_on: []
    write_scope:
      - src/agos/core/execution.py
      - tests/core/test_execution.py
    worker:
      adapter: local_worktree
      role: worker_agent
```

Rules:

- `task_id` must match the active AGOS task.
- Plan files may be authored as YAML or JSON. AGOS persists the normalized plan
  as JSON under the active task so later commands read one canonical shape.
- `max_parallel` controls workspace setup and later worker dispatch.
- `requires_candidate_review` defaults to true.
- Every subtask must declare a non-empty `write_scope`.
- V0.3 rejects overlapping write scopes unless the dependency graph fully
  serializes each overlapping pair. For two subtasks with overlapping
  `write_scope`, one subtask must transitively depend on the other.
- Explicit arbiter merge-review overrides are deferred until a later version.

### ExecutionSubtask

Each subtask is a bounded unit of writable work:

```yaml
id: subtask-core-models
title: Add execution models
intent: Define data models for the execution layer.
depends_on: []
write_scope:
  - src/agos/core/execution.py
status: pending
workspace_ref: execution/workspaces/subtask-core-models.json
```

Statuses:

```text
pending
workspace_ready
running
completed
blocked
cancelled
```

The v0.3 implementation does not need to run a real autonomous worker. It only
needs to create the workspace, record the assignment, and let a local/fake
worker produce a diff.

### WorkspaceBinding

A workspace binding records where isolated work happens:

```yaml
subtask_id: subtask-core-models
kind: git_worktree
path: ../.agos-worktrees/agos-.../subtask-core-models
base_ref: main
base_commit: abc123
created_at: "2026-06-22T00:00:00Z"
```

Rules:

- The path must be outside the governed working tree root.
- The resolved path must be under a task-owned configured worktree root such as
  `../.agos-worktrees/<task-id>/<subtask-id>/`.
- The workspace base commit must be recorded.
- AGOS must reject any workspace path that resolves inside the governed working
  tree root.

### CandidatePatch

A candidate patch is the only artifact that can move from an isolated workspace
toward the governed repo:

```yaml
id: candidate-01
task_id: agos-...
subtask_id: subtask-core-models
source_agent: local_worktree
workspace_ref: execution/workspaces/subtask-core-models.json
patch_ref: evidence/candidate_patches/candidate-01.patch
patch_sha256: ...
base_commit: abc123
summary: Add execution data models and validation.
status: proposed
test_refs: []
review_refs: []
decision_ref: null
created_at: "2026-06-22T00:00:00Z"
```

Statuses:

```text
proposed
testing
tested
reviewing
reviewed
accepted
rejected
applied
superseded
```

Rules:

- The patch file is immutable once submitted.
- The stored SHA-256 must match the patch bytes before every test, review, or
  apply action.
- The patch must not touch files outside the subtask `write_scope`.
- A candidate cannot be applied from `proposed` directly.

### CandidateTestRun

Candidate tests run outside the governed main tree:

```yaml
id: candidate-test-01
candidate_id: candidate-01
gate_id: tests_pass
stage: candidate
command: python -m pytest tests/core/test_execution.py -q
state: passed
evidence_ref: gates/candidate-01-tests_pass-....log
workspace_ref: execution/workspaces/subtask-core-models.json
started_at: "2026-06-22T00:00:00Z"
completed_at: "2026-06-22T00:01:00Z"
```

States:

```text
running
passed
failed
```

Rules:

- `--gate` must name a gate in the active task's locked gate set. If omitted,
  AGOS runs every gate in that locked set.
- Gate commands should reuse the existing AGOS gate command runner with
  `GateContext.stage="candidate"`, `GateContext.repo_root` set to the temporary
  verification workspace, `GateContext.diff` set to the candidate patch text,
  and `GateContext.evidence_dir` set to the active task evidence directory.
- Tests apply the candidate to a temporary verification workspace, not directly
  to the governed main tree.
- Each candidate test run is tied to exactly one `gate_id` or the synthetic
  `patch_applies` check.
- The patch-apply dry run must succeed before any gate result can make the
  candidate applyable.
- Candidate test evidence refs are relative to `.agos/tasks/current/evidence/`.
- A failing test keeps the candidate test evidence but prevents acceptance
  unless a human arbiter explicitly rejects or supersedes it.

### ArbiterDecision

The arbiter records why a candidate may or may not be applied:

```yaml
id: decision-01
candidate_id: candidate-01
decision: accepted
reason: Tests pass, no open blocking candidate review findings, patch is in scope.
apply_strategy: direct_patch
evidence_refs:
  - evidence/candidate_patches/candidate-01.patch
  - reviews/review-.../findings.json
  - gates/candidate-01-tests_pass-....log
conflict_evidence_refs: []
decided_by: local_user
created_at: "2026-06-22T00:02:00Z"
```

Decision values:

```text
accepted
rejected
superseded
needs_changes
```

Apply strategy values:

```text
direct_patch
```

V0.3 supports only `direct_patch`: AGOS verifies the patch with `git apply
--check` in the governed repo before applying it. Three-way merge and manual
merge strategies are out of scope for v0.3 and should be added only when a
dedicated merge evidence model exists.

Rules:

- `accepted` decisions require non-empty `reason` and `evidence_refs`.
- `accepted` decisions must include the candidate patch ref, latest passed
  required candidate test refs, and latest completed candidate-bound review ref
  in `evidence_refs`.
- `accepted` decisions must use `apply_strategy: direct_patch`.
- `conflict_evidence_refs` is empty for accepted v0.3 decisions. Conflict
  evidence belongs to a blocked apply attempt, not to an accepted decision.

## CLI Surface

The minimal closed loop adds:

```text
agos execute-plan --plan execution-plan.json
agos candidate list
agos candidate submit <subtask-id> [--summary "..."]
agos candidate test <candidate-id> [--gate tests_pass]
agos candidate review <candidate-id> [--packet-only]
agos candidate review <candidate-id> --ingest findings.json --review-id review-...
agos candidate decide <candidate-id> --decision accepted|rejected|superseded|needs-changes --reason "..."
agos candidate apply <candidate-id>
```

`agos candidate review <candidate-id> --packet-only` creates a Review packet and
stores a started candidate review binding. `agos candidate review <candidate-id>
--ingest ... --review-id ...` calls the generic Review ingest path, then updates
that candidate binding to completed or failed. Plain `agos review --ingest`
remains valid for task/global closeout review, but it must not satisfy
`candidate apply` unless the execution layer records the candidate binding.

`agos candidate test` runs the named task gate in a temporary verification
workspace. If `--gate` is omitted, AGOS runs every gate in the active task.
Every candidate test command also records a synthetic `patch_applies` dry-run
result so the candidate can prove it still applies cleanly outside the governed
tree.

Optional debug commands can be added after the loop is stable:

```text
agos candidate show <candidate-id>
agos candidate diff <candidate-id>
agos execute-plan status
```

## Filesystem Layout

Execution artifacts live under the active task:

```text
.agos/tasks/current/
  execution/
    plan.json
    subtasks/
      subtask-core-models.json
    workspaces/
      subtask-core-models.json
    candidates/
      candidate-01.json
    tests/
      candidate-test-01.json
    decisions/
      decision-01.json
  evidence/
    candidate_patches/
      candidate-01.patch
    execution/
      execute-plan-....log
      candidate-apply-....log
```

Git worktrees may be created under a sibling task-owned directory such as:

```text
../.agos-worktrees/<task-id>/<subtask-id>/
```

The execution metadata under `.agos/tasks/current/execution/workspaces/` stores
the binding record, while the actual git worktree lives outside the governed
working tree.

## Ledger Events

Add these event types:

```text
execution_plan_created
subtask_workspace_created
subtask_started
subtask_completed
subtask_blocked
candidate_patch_created
candidate_test_started
candidate_test_completed
candidate_review_started
candidate_review_completed
candidate_decision_recorded
candidate_applied
candidate_apply_blocked
candidate_rejected
candidate_superseded
```

Every event that changes execution state must include:

- `task_id`
- relevant `subtask_id` or `candidate_id`
- evidence refs when available
- the current ledger hash produced by the ledger append

## Candidate Apply Guards

`agos candidate apply` must fail unless all conditions are true:

1. The active task exists and is not closed out.
2. The candidate exists and is not already applied.
3. The candidate patch file exists.
4. The patch SHA-256 matches `patch_sha256`.
5. The candidate patch applies cleanly to the current governed repo with
   `git apply --check`. V0.3 records the candidate `base_commit` for audit but
   does not perform three-way merge.
6. The governed repo is not dirty in any file the candidate patch will touch.
   The safest v0.3 policy is to reject a dirty governed repo entirely.
7. The patch touches only files declared in the subtask `write_scope`.
8. The candidate has a passed `patch_applies` dry run and a passed candidate
   test for every gate in the active task.
9. If `requires_candidate_review` is true, the candidate has a completed Review
   packet/report bound to that candidate with no open blocking findings.
10. The latest arbiter decision for the candidate is `accepted`.
11. No other accepted/applied candidate touches the same files. V0.3 does not
    use conflict evidence to override this guard.

After successful apply:

- Apply the patch to the governed working tree.
- Mark the candidate `applied`.
- Append `candidate_applied`.
- Preserve the patch, decision, review, and test evidence refs.

## Review Integration

Candidate review reuses the existing Review layer:

```text
ReviewService.create_packet(
  diff_kind="candidate_patch",
  diff_evidence_ref="evidence/candidate_patches/candidate-01.patch",
  subject={
    "type": "candidate",
    "candidate_id": "candidate-01",
    "subtask_id": "subtask-core-models",
    "task_id": "agos-...",
  },
  context_refs=[
    "execution/candidates/candidate-01.json",
    "execution/subtasks/subtask-core-models.json",
    "execution/workspaces/subtask-core-models.json",
    "execution/tests/candidate-test-01.json",
  ],
)
```

The candidate metadata stores the resulting review refs. A candidate is
review-ready only when its candidate-bound review report exists. A candidate is
apply-ready only when no blocking finding in that candidate review remains open.

The Review layer remains read-only. It never mutates workspaces, patches, or the
governed repo.

### Candidate Review Contract

Candidate review must be an explicit binding between one candidate and one
Review report. A global task review or a review for another candidate cannot
satisfy `candidate apply`.

#### Review Input

Before creating the Review packet, AGOS must verify:

- The candidate patch file exists and matches `patch_sha256`.
- The candidate patch touches only files in the subtask `write_scope`.
- The candidate has passed `patch_applies` and every active-task gate.

The Review packet input must include, directly or through stable refs:

- Candidate identity: `task_id`, `subtask_id`, and `candidate_id`.
- Patch identity: `patch_ref`, `patch_sha256`, and `base_commit`.
- Scope identity: the normalized subtask `write_scope`.
- Workspace identity: the candidate `workspace_ref`.
- Verification identity: the passed `patch_applies` ref and the latest passed
  candidate test ref for every active-task gate.
- Ledger identity: the current task ledger head hash at packet creation time.

V0.3 implements this by extending the v0.2 Review packet API with two optional
generic fields:

```python
ReviewService.create_packet(
    *,
    diff_kind: str,
    diff_evidence_ref: str | None = None,
    subject: dict[str, str] | None = None,
    context_refs: list[str] | None = None,
) -> tuple[str, ReviewPacket]
```

`ReviewPacket.subject` defaults to `{}` and `ReviewPacket.context_refs` defaults
to `[]`, preserving governed-repo review compatibility.

The packet or referenced candidate metadata must expose the candidate
`base_commit`, `patch_sha256`, `write_scope`, and `test_refs`. This keeps
reviewers and the apply guard looking at the same facts. Review must treat
`subject` and `context_refs` as opaque data; it must not import execution models
or mutate execution state.

#### Review Output Binding

The candidate metadata must store review bindings in a machine-readable shape:

```yaml
review_refs:
  - review_id: review-01
    packet_ref: reviews/review-01/packet.json
    report_ref: reviews/review-01/findings.json
    raw_refs: []
    patch_sha256: ...
    base_commit: abc123
    write_scope:
      - src/agos/core/execution.py
      - tests/core/test_execution.py
    test_refs:
      - gates/candidate-01-patch_applies-....log
      - gates/candidate-01-tests_pass-....log
    ledger_head_at_start: ...
    ledger_head_at_completion: ...
    open_blocking_count: 0
    state: completed
    created_at: "2026-06-22T00:00:00Z"
    completed_at: "2026-06-22T00:01:00Z"
```

Binding states are:

```text
started
completed
failed
```

`candidate_review_started` records `candidate_id`, `review_id`, `packet_ref`,
and the candidate patch evidence ref. `candidate_review_completed` records
`candidate_id`, `review_id`, `report_ref`, and `open_blocking_count`.

The Review service remains generic and does not own this binding. The execution
service owns the binding lifecycle: it creates the binding after
`ReviewService.create_packet`, and it completes or fails the binding after
`ReviewService.ingest_findings` returns. This is the only bridge required from
execution to Review for v0.3.

If review ingestion fails or produces malformed findings, AGOS must keep the
raw evidence where available, mark that review binding as failed, and leave the
candidate non-applyable.

#### Apply-Time Review Selection

`candidate apply` must evaluate the latest completed review binding for the
candidate, ordered by ledger event order. It must reject when:

- No completed candidate-bound review exists.
- The selected `report_ref` is missing or does not match the bound `review_id`.
- The selected report has any open blocking finding.
- The selected binding's `patch_sha256`, `base_commit`, normalized
  `write_scope`, or `test_refs` do not match the current candidate/subtask/test
  metadata.
- The candidate lacks a passed `patch_applies` dry run.
- Any active-task gate lacks a passed candidate test.

Task-level reviews still participate in normal closeout, but they do not replace
candidate-bound review for `candidate apply`.

## Worker Backend

The first worker backend is `local_worktree`.

Responsibilities:

- Create or validate an isolated git worktree for the subtask.
- Record workspace metadata.
- Allow an external human, fake worker, or local tool to edit that workspace.
- Capture a binary patch from the workspace during `candidate submit`, including
  tracked changes and untracked files. The local git backend may use
  intent-to-add or an equivalent mechanism so new files appear in the captured
  patch. Empty patches are rejected.

Test-only fake workers may be used to create deterministic changes in temp repos
so the execution protocol can be tested end to end. Production AGOS should treat
fake workers as a test adapter, not as an autonomous implementation engine.

Future worker backends can implement the same candidate patch contract:

- `codex_worktree`
- `multica_patch_export`
- `remote_branch`
- `pull_request`

## Error Handling

- Invalid plan: reject before creating workspaces.
- Overlapping write scopes: reject unless dependencies fully serialize the
  overlapping subtasks.
- Worktree creation failure: mark subtask `blocked` and record the error log.
- Dirty governed repo at apply time: reject when dirty files overlap the
  candidate patch scope. The default v0.3 implementation may reject any dirty
  governed repo until a narrower dirty-file policy is implemented.
- Patch hash mismatch: hard block test, review, decide, and apply.
- Patch outside write scope: reject the candidate.
- Candidate test failure: keep evidence and prevent `accepted` decision unless
  the user records a rejection, superseded candidate, or needs-changes decision.
- Review with open blocking findings: prevent apply.
- Patch conflict: reject apply, write conflict/apply-check evidence, append
  `candidate_apply_blocked`, and leave the candidate state unchanged.
- Ledger mismatch: hard block execution commands until the existing ledger
  repair or reset process is used.

## Testing Strategy

Core tests:

- Execution plan validates active task id, subtasks, dependencies, and write
  scopes.
- Workspace binding rejects paths inside the governed working tree.
- Candidate patch creation stores immutable patch bytes and SHA-256.
- Candidate patch creation includes untracked files and rejects empty patches.
- Candidate patch validation rejects hash mismatches.
- Candidate patch validation rejects files outside `write_scope`.
- Candidate test runs record one gate or `patch_applies` dry run per evidence
  file.
- Candidate apply requires passed `patch_applies` plus passed tests for every
  active-task gate.
- Candidate state transitions reject invalid jumps such as `proposed -> applied`.
- Arbiter decisions require non-empty reasons and evidence refs for accepted
  candidates.
- Arbiter decisions reject non-v0.3 apply strategies such as three-way merge.

Service tests:

- `execute-plan` writes plan, subtask, workspace metadata, and ledger events.
- `candidate submit` captures a worktree diff as patch evidence.
- `candidate test` uses a temporary verification workspace and records
  `patch_applies` plus gate evidence.
- `candidate review` creates a Review packet with `diff_kind="candidate_patch"`
  and records a candidate-bound review ref.
- `candidate review --ingest` updates only the matching candidate review binding
  and records `candidate_review_completed` with `open_blocking_count`.
- `candidate apply` blocks without tests, review, accepted decision, or valid
  hash.
- `candidate apply` ignores unrelated task/global reviews and uses only the
  latest completed candidate-bound review.
- `candidate apply` rejects stale review bindings whose patch hash, base commit,
  write scope, or test refs no longer match the candidate metadata.
- `candidate apply` records `candidate_apply_blocked` evidence when
  `git apply --check` fails.
- `candidate apply` applies a valid candidate and records `candidate_applied`.

CLI tests:

- `agos execute-plan --plan` creates workspaces.
- `agos candidate list` shows candidate statuses.
- `agos candidate submit/test/review/decide/apply` cover the happy path.
- CLI errors are clear for missing task, missing candidate, invalid status, and
  failed guards.

Integration tests:

- Use temp git repos and fake workers.
- Avoid requiring Multica or Codex for v0.3 correctness.
- Keep real executor smoke tests opt-in.

## Acceptance Criteria

- AGOS can create isolated workspaces from an execution plan.
- AGOS can submit a worktree diff as a hash-addressed candidate patch.
- Candidate tests run outside the governed main working tree.
- Candidate review reuses the v0.2 Review layer.
- Candidate review completion is recorded by the execution layer after Review
  ingest, without making Review import execution models.
- Blocking candidate review findings prevent apply.
- Candidate apply requires a valid patch hash, in-scope files, passed
  `patch_applies`, passed active-task gates, completed review, and accepted
  arbiter decision.
- All execution state changes are persisted and ledgered.
- The implementation can be tested end to end with a fake/local worker backend.

## Implementation Order

1. Add execution models and store.
2. Add plan validation and workspace binding.
3. Add candidate patch submission and hash validation.
4. Add candidate test command using temporary verification workspaces.
5. Add candidate review integration.
6. Add arbiter decision recording.
7. Add guarded candidate apply.
8. Add README updates and full verification.
