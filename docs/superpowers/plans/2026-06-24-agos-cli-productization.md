# AGOS CLI Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing AGOS Typer command set into a more complete, scriptable CLI surface for initialization, diagnostics, configuration inspection, status, and execution-run lifecycle commands.

**Architecture:** Keep core behavior behind existing `agos.core` services and expose product-facing commands in small `agos.cli.cmd_*` modules. Add read-only diagnosis and status commands without changing execution semantics, and add `agos run` as a user-facing alias for the existing `execute-plan` runtime commands.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, PyYAML, pytest, existing filesystem-backed AGOS state.

---

## File Structure

- Create: `src/agos/cli/cmd_config.py`
  - Implements `agos config show` and `agos config validate`.
- Create: `src/agos/cli/cmd_status.py`
  - Implements top-level `agos status` with human and JSON output.
- Create: `src/agos/cli/cmd_doctor.py`
  - Implements `agos doctor` with git, initialization, config, worker, reviewer, and orchestration checks.
- Modify: `src/agos/cli/main.py`
  - Registers `config`, `status`, `doctor`, and `run` alias commands.
- Test: `tests/cli/test_config.py`
  - Covers config show/validate success and invalid config errors.
- Test: `tests/cli/test_status_command.py`
  - Covers initialized/uninitialized top-level status output.
- Test: `tests/cli/test_doctor.py`
  - Covers doctor JSON and failure behavior.
- Modify: `tests/cli/test_execute_plan_runtime.py`
  - Adds coverage that `agos run` aliases `agos execute-plan`.
- Modify: `README.md`
  - Documents the productized command groups and quickstart flows.

## Task 1: Config CLI

- [ ] Write failing tests for `agos config show --json`, `agos config validate`, and invalid config handling.
- [ ] Implement `cmd_config.py` using `find_initialized_repo_root()` and `AGOSConfig.load()`.
- [ ] Register `config_app` in `main.py`.
- [ ] Run `python -m pytest tests/cli/test_config.py -q`.

## Task 2: Status CLI

- [ ] Write failing tests for `agos status --json` in initialized and uninitialized git repos.
- [ ] Implement `cmd_status.py` using `find_repo_root()`, `repo_paths()`, and `load_status()`.
- [ ] Register `status_command` in `main.py`.
- [ ] Run `python -m pytest tests/cli/test_status_command.py -q`.

## Task 3: Doctor CLI

- [ ] Write failing tests for `agos doctor --json` healthy config and invalid config failures.
- [ ] Implement `cmd_doctor.py` with deterministic check payloads and nonzero exit when required checks fail.
- [ ] Register `doctor_command` in `main.py`.
- [ ] Run `python -m pytest tests/cli/test_doctor.py -q`.

## Task 4: Run Alias

- [ ] Add a failing test proving `agos run status <run-id> --json` returns the same runtime snapshot shape as `agos execute-plan status`.
- [ ] Register `execute_plan_app` a second time under `run` while preserving `execute-plan`.
- [ ] Run `python -m pytest tests/cli/test_execute_plan_runtime.py -q`.

## Task 5: Documentation And Verification

- [ ] Update `README.md` with `agos doctor`, `agos config`, `agos status`, and `agos run`.
- [ ] Run `python -m pytest tests/cli/test_config.py tests/cli/test_status_command.py tests/cli/test_doctor.py tests/cli/test_execute_plan_runtime.py -q`.
- [ ] Run full verification:
  - `python -m pytest -q`
  - `python -m pytest --cov=agos --cov-report=term-missing -q`
  - `python -m ruff check src tests`
  - `python -m compileall -q src tests`

## Self-Review

- Scope is focused on CLI product surface and does not change worker, reviewer, merge, or runtime semantics.
- Each new command has a dedicated CLI module and test file.
- `agos run` is an alias over the existing runtime app, so old `execute-plan` scripts remain compatible.
