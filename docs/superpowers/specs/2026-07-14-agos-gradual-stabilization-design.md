# AGOS Gradual Stabilization Design

**Status:** Approved for implementation
**Date:** 2026-07-14
**Scope:** Stable open-source convergence while preserving existing CLI and `.agos` data

## Context

AGOS currently contains three independently useful paths:

1. The original executor path: `init/start -> executor -> checkpoint/review/closeout`.
2. The candidate path: `plan -> worktree worker -> patch -> gates -> review -> decision -> apply`.
3. The pull-request path: `PR diff -> prepare-merge-gate -> merge-gate`.

The domain models and candidate guards are substantial, but the paths do not share one task
application service. The current PR preparation path also reconstructs a candidate from the submitted
diff, synthesizes a clean review, and marks the candidate applied without a decision. That can prove
that two newly-created artifacts match, but it cannot prove that the submitted diff originated from a
governed candidate.

This design converges the paths incrementally. Existing commands and persisted task data remain
readable throughout the migration.

## Goals

- Make the default pull-request CI deterministic and fully usable without model-provider credentials.
- Distinguish cryptographically proven candidate provenance from reconstructed PR validation.
- Require explicit candidate decisions for governed candidate application.
- Stop treating a normal asynchronous worker poll as a stuck execution.
- Introduce one application service used by CLI and Dashboard task entry points.
- Preserve current CLI spellings and existing `.agos` files.
- Make local state writes safe across concurrent CLI and Dashboard processes.
- Make dangerous agent permissions and remotely exposed Dashboard writes opt-in.
- Add a reproducible release path, license text, dependency updates, and security scanning.

## Non-Goals

- AGOS will not provide an offline semantic replacement for an LLM code reviewer.
- Reconstructed PR validation will not be described as proof that an Agent produced a diff.
- Existing v0.1 task archives will not be rewritten in place.
- This work will not enable GitHub branch-protection settings on a remote repository automatically.
- Provider-specific Agent CLIs will remain optional integrations.

## Compatibility Policy

### CLI and API

- Existing commands, options, and Dashboard request fields remain accepted.
- New task execution selects a mode from configuration or an explicit request override.
- `legacy` mode preserves the current direct executor behavior.
- `candidate` mode uses the planner, isolated workers, candidate tests, reviews, decisions, and apply.
- Existing configurations without an execution-mode field resolve to `legacy`.
- Configurations written by a new `agos init` resolve to `candidate` when their selected adapters can
  satisfy candidate-mode readiness; otherwise init writes `legacy` and reports the missing capability.
- Deprecation warnings are machine-readable and do not change command exit codes.

### Persisted data

- New model fields are optional or have legacy-safe defaults.
- Existing candidate records without provenance metadata are classified as `legacy_unattested`.
- Existing trust-anchor formats continue to verify as integrity anchors, but do not satisfy signed
  provenance requirements.
- Status loading accepts old files and rebuilds stale cache state from the ledger where possible.

## Target Architecture

```text
CLI / Dashboard
      |
      v
TaskExecutionService
  |-- create task + lock gates
  |-- legacy adapter (compatibility)
  `-- candidate pipeline
        |-- planner
        |-- isolated worker runtime
        |-- candidate patch + tests
        |-- reviewer orchestration
        |-- decision arbiter
        `-- guarded apply

Trusted merge-gate CLI
  |-- signed/provenanced candidate verification
  `-- reconstructed PR validation (explicitly unprovenanced)
```

`TaskExecutionService` owns entry-point selection and result normalization. Existing domain services
remain responsible for candidate, review, decision, ledger, and evidence invariants. Provider adapters
remain outside the application service.

## Phase 1: Deterministic Baseline

### Hermetic tests and CI

- Tests that expose locally discovered Agents must control discovery explicitly and must not depend on
  `codex`, `claude`, or `multica` being installed on the test machine.
- The default CI matrix runs lint, compile, unit/integration tests, coverage, build, wheel install, and
  CLI smoke tests without model credentials.
- Real planner, reviewer, and worker smokes move to a separate `workflow_dispatch` and scheduled
  workflow. Missing provider configuration skips an integration instead of failing ordinary PR CI.
- The coverage gate remains 90 percent and must be met with behavior-oriented tests.

### Worker polling

- A repeated `running` snapshot is normal and never immediately becomes `stuck`.
- The automatic pipeline polls until a terminal state or `max_tick_iterations` is exhausted.
- Between running polls it sleeps for the largest configured interval among currently running workers.
  This prevents any worker from being polled more frequently than configured.
- Exhausting the iteration budget produces `stuck` with a deterministic reason and leaves persisted
  worker state available for `resume`.
- Tests inject a sleeper so no test waits in wall-clock time.

### Candidate decision invariant

- A candidate can become `applied` only after an accepted decision has been persisted.
- Merge-gate verification requires the decision reference, parses the referenced decision, verifies the
  candidate ID and accepted value, and checks that decision evidence covers patch, tests, and review.
- Legacy applied candidates without decisions are accepted only under an explicit compatibility flag and
  are reported as legacy evidence. The flag is `--allow-legacy-decisionless` and defaults to false.

## Phase 2: Honest Hybrid Provenance

### Provenance classes

Candidate evidence records one of these source classes:

- `worker_export`: patch exported from an AGOS-owned isolated workspace.
- `external_attested`: patch supplied by an external worker with a signed attestation.
- `ci_reconstructed`: candidate created after the fact from a submitted PR diff.
- `legacy_unattested`: old candidate with no source metadata.

The source field is descriptive. A `worker_export` becomes cryptographically proven only when its ledger
head is covered by a valid signed trust anchor from an allowed issuer.

### Merge-gate policy

The merge gate supports three provenance policies:

- `required`: every submitted diff must be represented by accepted/applied candidate evidence, an
  accepted decision, a clean current review, passing tests, and a valid signed anchor.
- `optional`: fully verifies provenanced candidates; otherwise accepts a `ci_reconstructed` candidate
  after deterministic gates and reports `unprovenanced` in structured output.
- `disabled`: validates tests and submitted diff binding without making a provenance claim.

The default for old configurations is `optional`. Protected repositories can set `required`.
Configuration stores this under `merge_gate.provenance_policy`; the CLI override is
`--provenance-policy required|optional|disabled`.

### PR preparation

- `prepare-merge-gate` never creates a ReviewReport with hard-coded empty findings.
- Reconstructed PR evidence is stored as `ci_reconstructed`, remains `tested`, and has no applied event
  or decision.
- Optional policy verifies its tests and exact submitted-diff binding through a dedicated reconstructed
  validation path.
- Required policy rejects reconstructed evidence.
- Output JSON includes `provenance_state` with `proven`, `unprovenanced`, or `disabled`.

### Trusted verifier

- PR workflows install and run the verifier from the protected base revision, not the PR checkout.
- The verifier loads gate and provenance policy from a trusted base-revision config supplied explicitly by
  the workflow through `--trusted-config <path>`.
- The subject checkout supplies code and PR diff only; it cannot delete `.agos/agos.yaml` to skip the
  gate or modify the verifier that evaluates it.
- The prepare and verify jobs exchange only task evidence artifacts. No model secrets are available.

### Signed anchors

- Existing file and git-ref stores are integrity-only anchor backends.
- A signed anchor envelope binds the canonical anchor payload, issuer, key ID, algorithm, and signature.
- The first signed backend uses Ed25519. Runtime support is provided by the optional `signing` extra with
  `cryptography>=42`; the development extra includes the same dependency for verification tests.
- Verification uses a configured public key and works offline.
- Signing uses a private key outside the governed repository. Private keys are never read from `.agos`.
- Unsigned anchors cannot satisfy `provenance_policy=required`.
- Allowed issuer/key pairs live under `merge_gate.trusted_signers`; keys are PEM-encoded public-key files
  resolved relative to the trusted configuration file.

## Phase 3: Unified Product Entry

### TaskExecutionService

The service provides one start operation with this normalized result:

```text
task_id
mode
run_id
state
candidate_ids
applied_candidate_ids
blocked_stage
blocked_reason
compatibility_warnings
```

The operation performs:

1. Validate config, workflow, gates, and mode readiness without side effects.
2. Create and atomically publish task state.
3. In `legacy` mode, dispatch the configured executor through the existing adapter.
4. In `candidate` mode, register workers/planner/reviewers and invoke the automatic candidate pipeline.
5. Persist a mode-specific start event and return a common result.

### CLI and Dashboard

- `agos start` delegates to `TaskExecutionService` and adds `--mode legacy|candidate`.
- Existing callers that omit `--mode` use the configuration default.
- Dashboard POST `/api/runs` accepts an optional `mode` and delegates to the same service.
- Dashboard lifecycle operations act on the normalized run and no longer redispatch a different runtime
  implicitly.
- `agos run auto` remains as a compatibility and advanced-control command over the same candidate
  pipeline.

### Output contract

- Source-code tasks are successful when the governed/candidate patch is non-empty and valid.
- `outputs/<task-id>` is required only for standalone deliverable task kinds or an explicit output
  contract.
- Legacy tasks retain the current output-directory behavior unless their config opts into the corrected
  contract.

### Offline end-to-end test adapter

- Add a deterministic command worker for tests and local automation.
- It runs an explicit argv in the isolated worktree without a shell.
- The end-to-end test must create a task, execute a worker that changes a real file, capture a patch, run
  gates, ingest a deterministic review result, persist an accepted decision, apply the patch, and pass
  merge-gate verification.

## Phase 4: State, Security, and Release

### State consistency

- Ledger append holds a cross-process lock from tail read through append and fsync.
- The implementation supports POSIX and Windows using standard-library locking primitives.
- Status writes remain atomic.
- Status reads compare the cached ledger head with the actual verified head and rebuild stale cache state
  from ledger events.
- A crash between ledger and cache writes is recoverable on the next read.

### Agent permissions

- New worker configuration defaults to a workspace-write sandbox and non-interactive approval denial.
- Dangerous sandbox/approval bypass remains available only through an explicit compatibility option.
- Health and doctor output identifies dangerous adapters and configurations.
- Patch scope remains an evidence guard, not a claim that non-Git side effects were sandboxed.

### Dashboard exposure

- Loopback remains the default bind address.
- Non-loopback binding requires an authentication token.
- Mutating requests require the token and pass same-origin validation.
- The server rejects oversized bodies and never logs tokens.
- Existing loopback use remains compatible.

### Open-source release readiness

- Add the complete MIT license text to source and distributions.
- Add Dependabot configuration and a CodeQL workflow.
- Release workflow creates GitHub release assets and supports PyPI trusted publishing once the repository
  environment is configured.
- Release jobs run the same offline verification suite before publishing.
- Document required branch-protection checks and provide a read-only verification command/script; do not
  mutate remote repository settings automatically.

## Error Handling

- Readiness failures occur before task publication where possible.
- A candidate-mode failure records its exact blocked stage and keeps resumable evidence.
- Invalid or stale signed anchors fail closed under required policy.
- Optional provenance never silently upgrades reconstructed evidence to proven.
- Trusted-config failure blocks the PR instead of falling back to PR-controlled config.
- Legacy compatibility paths emit warnings but preserve their previous exit behavior.

## Testing Strategy

Each behavior change follows red-green-refactor:

- Focused unit tests for polling, provenance classification, decisions, signed anchors, locks, and status
  replay.
- CLI and Dashboard contract tests for mode selection and backward compatibility.
- Offline end-to-end candidate test with a real worktree and subprocess worker.
- Workflow policy tests that assert ordinary CI contains no required provider credentials and that trusted
  verifier checkout/configuration cannot come from the PR subject.
- Existing test suite on Python 3.11 and 3.12 across Linux, macOS, and Windows.
- Packaging test that inspects wheel contents for Dashboard assets and license text.

## Delivery Order

1. Deterministic baseline and candidate decision invariant.
2. Hybrid provenance and trusted offline PR verification.
3. Unified TaskExecutionService and corrected output contract.
4. Ledger/status hardening, safer permissions, Dashboard authentication, and release readiness.

Every phase must leave the repository testable and must include migration documentation before the next
phase begins.
