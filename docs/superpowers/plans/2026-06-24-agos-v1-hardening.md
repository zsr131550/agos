# AGOS V1 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close AGOS v1 production-readiness gaps with trusted merge verification, ledger anchoring, typed security gates, automatic execution, stronger diagnostics, and release-grade CI.

**Architecture:** Preserve AGOS as the governance layer. Add small core services for trust anchors, merge-gate verification, execution planning, and automatic execution; expose them through focused Typer commands. Reuse existing ledger, gate, execution service, worker adapter, review, and candidate lifecycle modules.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, PyYAML, pytest, pytest-cov, ruff, setuptools/build, GitHub Actions.

---

## File Structure

- Create: `src/agos/core/trust_anchor.py`
  - Anchor payload model, canonical serialization, file backend, Git ref backend, publish/verify helpers.
- Create: `src/agos/cli/cmd_anchor.py`
  - `agos anchor publish` and `agos anchor verify`.
- Create: `src/agos/core/merge_gate.py`
  - CI-safe verifier for ledger, gate lock, anchor, candidate evidence, and optional diff binding.
- Create: `src/agos/cli/cmd_merge_gate.py`
  - `agos merge-gate` CLI.
- Modify: `src/agos/cli/main.py`
  - Register `anchor` and `merge-gate`.
- Modify: `src/agos/cli/cmd_checkpoint.py`
  - Optional anchor publication after checkpoint.
- Modify: `src/agos/core/config.py`
  - Add gate `options`, validate typed security gates, add anchor/pipeline config.
- Modify: `src/agos/core/gate.py`
  - Add typed external security gates and lock options.
- Modify: `src/agos/core/execution_service.py`
  - Add model-based plan execution helper and typed gate display helper.
- Create: `src/agos/core/execution_planner.py`
  - Generate and validate execution plans from task title/intent.
- Create: `src/agos/core/execution_pipeline.py`
  - Run plan generation, execution runtime, candidate submission/testing/review/decision/apply.
- Modify: `src/agos/cli/cmd_execute_plan.py`
  - Add `agos run auto`.
- Modify: `src/agos/cli/cmd_doctor.py`
  - Add actionable health checks.
- Modify: `.github/workflows/ci.yml`
  - Matrix, lint, coverage, build, CLI smoke, merge gate smoke.
- Create: `.github/workflows/release.yml`
  - Build release artifacts.
- Modify: `pyproject.toml`
  - Packaging metadata and `build` dev dependency.
- Modify: `README.md`
  - Full usage, automatic run, merge gate, security gates, release notes.
- Create: `docs/security-gates.md`
  - Typed gate examples and CI boundary.
- Tests:
  - `tests/core/test_trust_anchor.py`
  - `tests/cli/test_anchor.py`
  - `tests/core/test_merge_gate.py`
  - `tests/cli/test_merge_gate.py`
  - `tests/core/test_execution_planner.py`
  - `tests/core/test_execution_pipeline.py`
  - `tests/cli/test_run_auto.py`
  - update existing gate/config/doctor/CI-related tests.

## Task 1: Trust Anchor

- [ ] Write tests in `tests/core/test_trust_anchor.py` for canonical payload serialization, file backend publish/read, stale head detection, missing anchor failure, and Git ref name validation.
- [ ] Run `python -m pytest tests/core/test_trust_anchor.py -q` and confirm failures are from missing implementation.
- [ ] Implement `src/agos/core/trust_anchor.py` with `TrustAnchorPayload`, `AnchorVerification`, `FileTrustAnchorStore`, `GitRefTrustAnchorStore`, `publish_current_anchor()`, and `verify_current_anchor()`.
- [ ] Add `tests/cli/test_anchor.py` for `agos anchor publish --backend file --path <file>` and `agos anchor verify`.
- [ ] Implement `src/agos/cli/cmd_anchor.py` and register it in `src/agos/cli/main.py`.
- [ ] Run `python -m pytest tests/core/test_trust_anchor.py tests/cli/test_anchor.py -q`.

## Task 2: Merge Gate

- [ ] Write tests in `tests/core/test_merge_gate.py` covering clean ledger pass, tampered ledger block, gate-lock drift block, required anchor mismatch block, missing accepted candidate evidence block, and passing accepted candidate evidence.
- [ ] Write CLI tests in `tests/cli/test_merge_gate.py` for JSON output, non-zero exit on block, and `--require-anchor`.
- [ ] Run merge-gate tests and confirm missing implementation failures.
- [ ] Implement `src/agos/core/merge_gate.py` as a pure verifier returning structured checks.
- [ ] Implement `src/agos/cli/cmd_merge_gate.py` and register `agos merge-gate`.
- [ ] Run `python -m pytest tests/core/test_merge_gate.py tests/cli/test_merge_gate.py -q`.

## Task 3: Typed Security Gates

- [ ] Add failing tests in `tests/core/test_config.py` for `GateSpec.options`, allowed security gate types, and invalid type rejection.
- [ ] Add failing tests in `tests/core/test_gate.py` for OPA, Semgrep, TruffleHog, and CodeQL fake executables: pass, block, missing executable, evidence log, and `gates_locked` option drift.
- [ ] Update `src/agos/core/config.py` with `options` and supported gate validation.
- [ ] Update `src/agos/core/gate.py` with `ExternalSecurityGate` and per-type argv builders.
- [ ] Update candidate test command display in `src/agos/core/execution_service.py`.
- [ ] Run `python -m pytest tests/core/test_config.py tests/core/test_gate.py tests/cli/test_ci.py -q`.

## Task 4: Automatic Execution Planner

- [ ] Write tests in `tests/core/test_execution_planner.py` for deterministic fallback plan, valid JSON planner output, invalid JSON rejection with fallback, task-id normalization, default worker selection, and write-scope normalization.
- [ ] Implement `src/agos/core/execution_planner.py`.
- [ ] Add config models for planner/pipeline defaults in `src/agos/core/config.py`.
- [ ] Run `python -m pytest tests/core/test_execution_planner.py tests/core/test_config.py -q`.

## Task 5: Automatic Execution Pipeline

- [ ] Write tests in `tests/core/test_execution_pipeline.py` using fake workers and reviewers for dry-run completion, no candidate when worker fails, no accept when tests fail, no accept with blocking review, and explicit apply success.
- [ ] Add `execute_plan_model()` or equivalent helper in `src/agos/core/execution_service.py`.
- [ ] Implement `src/agos/core/execution_pipeline.py` using existing `ExecutionService`, `ExecutionRuntime`, worker adapters, reviewer adapters, and candidate decision/apply APIs.
- [ ] Run `python -m pytest tests/core/test_execution_pipeline.py tests/core/test_execution_service.py tests/core/test_execution_runtime.py -q`.

## Task 6: `agos run auto`

- [ ] Write CLI tests in `tests/cli/test_run_auto.py` for `agos run auto --dry-run --json`, missing active task, explicit `--apply`, and failure output.
- [ ] Modify `src/agos/cli/cmd_execute_plan.py` to expose `run auto`.
- [ ] Register configured worker/reviewer/orchestration adapters at the CLI boundary.
- [ ] Run `python -m pytest tests/cli/test_run_auto.py tests/cli/test_execute_plan_runtime.py -q`.

## Task 7: Doctor, CI, Release, Docs

- [ ] Extend `tests/cli/test_doctor.py` for hook checks, Python version, console script check, worker health details, and active anchor warning.
- [ ] Update `src/agos/cli/cmd_doctor.py` with actionable checks and stable JSON.
- [ ] Update `.github/workflows/ci.yml` with OS/Python matrix, ruff, coverage, compileall, build, wheel install, CLI smoke, and merge-gate smoke.
- [ ] Create `.github/workflows/release.yml` for tag/manual artifact builds.
- [ ] Update `pyproject.toml` package metadata and dev build dependency.
- [ ] Update `README.md` and add `docs/security-gates.md`.
- [ ] Run docs/CLI-focused tests and `python -m build`.

## Task 8: Full Verification And Integration

- [ ] Run `python -m pytest -q`.
- [ ] Run `python -m pytest --cov=agos --cov-report=term-missing -q`.
- [ ] Run `python -m ruff check src tests`.
- [ ] Run `python -m compileall -q src tests`.
- [ ] Run `python -m build`.
- [ ] Inspect `git diff --stat` and `git status -sb`.
- [ ] Request code review, fix findings, and rerun relevant tests.
- [ ] Commit all changes.

## Self-Review

- Every design requirement maps to at least one task.
- The first three tasks cover server-side merge gate, trust anchor, and security gates.
- Tasks 4-6 cover automatic title-to-plan-to-worker execution.
- Task 7 covers CLI productization, docs, CI, and release.
- Existing manual AGOS flows remain supported.
- No task requires external credentials to pass local tests.
