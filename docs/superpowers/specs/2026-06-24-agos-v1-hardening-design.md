# AGOS V1 Hardening Design

**Status:** Approved for implementation  
**Date:** 2026-06-24  
**Author:** AGOS project

## Goal

Close the remaining AGOS production-readiness gaps by adding an authoritative merge gate, an out-of-band ledger trust anchor, production security gate types, an automatic execution pipeline, stronger diagnostics, and release-grade CI/packaging.

## Context

AGOS already has a usable Typer CLI and a substantial governance core:

- `init -> start -> checkpoint -> ci --local`
- review packet / ingest / resolve / closeout
- execution plans, isolated workspaces, worker adapters, candidates, reviews, decisions, and guarded apply
- native, LangGraph, and external orchestration backend seams
- local discovery for Multica, Codex CLI, and Claude Code

The remaining gap is not basic functionality. The gap is production closure: local hooks are advisory, the ledger lacks an external trust root, automatic execution still requires manual plan/candidate steps, security gates are minimal, and CI/release infrastructure is thin.

## Product Boundary

This design implements a repository-contained v1 MVP. It creates the commands, protocols, checks, tests, and documentation needed for production use. It does not claim to configure GitHub protected branches, PyPI credentials, or third-party scanner accounts automatically, because those require external platform state.

Where external infrastructure is required, AGOS must expose a deterministic local command and a CI-ready workflow boundary.

## Architecture

AGOS remains the governance layer. Coding agents and external orchestrators stay behind adapters. Merge decisions, ledger verification, gate evaluation, evidence checks, and candidate application remain AGOS-owned.

The implementation is split into three surfaces:

1. **Trusted verification surface**
   - `trust_anchor` records the expected ledger head outside the mutable task ledger.
   - `merge_gate` verifies chain integrity, locked gates, trust anchor consistency, accepted/applied candidates, test evidence, review evidence, and PR diff binding.
   - `anchor` and `merge-gate` CLI commands make this usable in CI.

2. **Autonomous execution surface**
   - `execution_planner` turns an active task title/intent into an `ExecutionPlan`.
   - `execution_pipeline` runs the existing execution runtime, submits completed candidates, tests them, optionally reviews them, records deterministic decisions, and optionally applies accepted output.
   - `agos run auto` provides the user-facing command.

3. **Productization surface**
   - `doctor` gains stronger checks and actionable remediation.
   - typed security gates support OPA, Semgrep, TruffleHog, and CodeQL without making those tools default dependencies.
   - CI runs tests, coverage, lint, compile checks, package build, and CLI smoke tests.
   - release workflow builds distributable artifacts.

## Trust Anchor

The ledger remains hash-chained JSONL, but a chain alone is not enough. A determined writer can rewrite every record and recompute every hash. The trust anchor fixes that by storing the expected task ledger head outside the task ledger.

MVP backends:

- `git_ref`: stores an anchor payload in `refs/agos/anchors/<task_id>`.
- `file`: local development fallback for tests and offline demos.

The `file` backend is explicitly not a security boundary. It exists so the command and merge gate can be tested without a remote Git host.

Anchor payload fields:

- `schema_version`
- `task_id`
- `ledger_head_hash`
- `ledger_seq`
- `repo_head`
- `created_at`
- `issuer`

`agos anchor publish` publishes the active task head. `agos anchor verify` verifies the active ledger against the configured or specified anchor. `agos checkpoint` may publish after checkpoint when configured.

## Server-Side Merge Gate

`agos merge-gate` is an authoritative verifier intended for CI. It fails closed.

Minimum checks:

- initialized AGOS repo exists
- active task exists
- task ledger hash chain verifies
- latest ledger head matches trust anchor when `--require-anchor` is used
- current workflow gates match the recorded `gates_locked` payload
- checkpoint repo heads are ancestors of the submitted head
- candidate patch files match recorded sha256
- accepted/applied candidates have passing required tests
- accepted/applied candidates have current candidate-bound review evidence unless explicitly bypassed
- applied candidate or bundle evidence binds to the submitted diff when `--base` and `--head` are provided
- manual merge required, failed previews, stale reviews, missing evidence, or dirty candidate state block the merge

The merge gate reuses existing `ExecutionStore`, `Ledger`, `GateSpec`, and candidate models rather than duplicating governance rules.

## Security Gates

Gate configuration keeps `command`, `argv`, or `type` as mutually exclusive choices. `type` is extended to:

- `secret_scan`
- `opa`
- `semgrep`
- `trufflehog`
- `codeql`

Typed external security gates are fail-closed. If the required executable is unavailable or returns non-zero, the gate blocks and writes evidence. Default workflows remain lightweight; a new `production_security` workflow documents how to opt in.

Gate lock payloads include `options`, so changing a policy file, Semgrep config, TruffleHog mode, or CodeQL query after task start is detected.

## Automatic Execution Pipeline

The current manual flow stays supported:

```bash
agos run start --plan execution-plan.yaml
agos candidate submit ...
agos candidate test ...
agos candidate review ...
agos candidate decide ...
agos candidate apply ...
```

The new automatic flow is:

```bash
agos run auto --dry-run
agos run auto --apply
```

Behavior:

- use the active task title and intent as planning input
- ask the configured planner/executor for JSON if supported
- validate the returned `ExecutionPlan`
- fall back to a deterministic single-subtask plan using the first configured worker
- run workers through the existing `ExecutionRuntime`
- submit candidates for completed subtasks
- run required gates
- run configured reviewers when available
- accept only candidates with passing tests and clean review evidence
- apply only when `--apply` is explicit

`--dry-run` never modifies governed source files. `--apply` uses the existing guarded apply path.

## Orchestration Backends

The existing native, LangGraph, and external backends stay behind the same `OrchestrationBackend` seam. This hardening pass does not replace the working execution runtime with a new graph engine. Instead, it adds tests proving backend lifecycle and normalized node dispatch stay compatible, while the automatic pipeline uses the existing production-ready execution service.

## CLI UX

`agos doctor` should identify:

- git repository status
- AGOS initialization
- config validity
- Python version
- console script availability
- installed hooks
- worker registration and health
- reviewer registration
- orchestration backend registration
- trust anchor status when an active task exists

Failures should include concrete recovery commands where possible.

## CI And Release

CI must run:

- pytest
- coverage gate
- ruff
- compileall
- package build
- installed wheel CLI smoke

The release workflow builds source and wheel artifacts for tags and manual dispatch. Publishing to a package index remains a separate credentialed platform step.

## Testing Strategy

Tests are grouped by surface:

- `tests/core/test_trust_anchor.py`
- `tests/cli/test_anchor.py`
- `tests/core/test_merge_gate.py`
- `tests/cli/test_merge_gate.py`
- `tests/core/test_gate.py`
- `tests/core/test_execution_planner.py`
- `tests/core/test_execution_pipeline.py`
- `tests/cli/test_run_auto.py`
- `tests/cli/test_doctor.py`

Existing execution service and candidate tests remain the authority for candidate apply behavior. New tests should call through those services rather than reimplement candidate policy.

## Rollout

1. Add trust anchor and merge gate.
2. Add typed security gates.
3. Add automatic execution planner and pipeline.
4. Harden doctor, CI, release, and docs.
5. Run full verification.

## Non-Goals

- No automatic GitHub branch protection mutation.
- No bundled OPA/Semgrep/TruffleHog/CodeQL installation.
- No PyPI publishing credentials in this repository.
- No rewrite of the existing execution service or candidate lifecycle.
