# AGOS Stabilization Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore a deterministic offline baseline, make asynchronous polling honor configured intervals, and require accepted decision evidence before merge-gate treats a candidate as governed.

**Architecture:** Keep all public commands and persisted models compatible. Correct environment-dependent tests and split provider smokes out of required CI, then change only the private automatic-poll loop and merge-gate validation seams. CI preparation temporarily creates a real arbiter decision over its current evidence; Phase 2 will replace its reconstructed review/provenance behavior.

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, pytest, GitHub Actions YAML

## Global Constraints

- Preserve existing CLI spellings and existing `.agos` data readability.
- Default pull-request CI must not require model-provider credentials.
- Use test-first red-green-refactor for every production behavior change.
- Keep `provenance_policy` work out of this phase except for the decision invariant needed by Phase 2.
- Do not weaken the 90 percent coverage threshold.

---

### Task 1: Hermetic Agent Discovery and Offline CI

**Files:**
- Modify: `tests/web/test_api.py`
- Modify: `tests/web/test_server.py`
- Modify: `tests/ci/test_autonomous_loop_ci_policy.py`
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/real-agent-smoke.yml`

**Interfaces:**
- Consumes: `agos.web.api.shutil.which`, GitHub Actions workflow documents.
- Produces: deterministic tests and a required CI workflow with no provider secrets.

- [ ] **Step 1: Make the Dashboard tests declare their Agent discovery environment**

Add this monkeypatch before calling `review_run_payload`:

```python
monkeypatch.setattr(
    "agos.web.api.shutil.which",
    lambda command: "/test/bin/codex" if command == "codex" else None,
)
```

Add `monkeypatch` to `test_dashboard_server_serves_agents_json` and use the same patch before starting
the server. This preserves the intended product behavior while removing host-machine assumptions.

- [ ] **Step 2: Verify the two previously failing Dashboard tests pass with Agent CLIs hidden**

Run:

```bash
PATH="$VIRTUAL_ENV/bin:/usr/bin:/bin" python -m pytest \
  tests/web/test_api.py::test_review_run_payload_accepts_local_review_agent \
  tests/web/test_server.py::test_dashboard_server_serves_agents_json -q
```

Expected: `2 passed`.

- [ ] **Step 3: Write the failing offline workflow policy test**

Replace the current smoke policy test with this function:

```python
def test_default_ci_is_offline_and_real_agent_smokes_are_opt_in() -> None:
    ci_text = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    ci = yaml.safe_load(ci_text)
    assert "real-agent-smoke" not in ci["jobs"]
    for token in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MULTICA_API_KEY", "AGOS_OPENHANDS_TOKEN"):
        assert token not in ci_text

    smoke_path = PROJECT_ROOT / ".github/workflows/real-agent-smoke.yml"
    smoke_text = smoke_path.read_text(encoding="utf-8")
    smoke = yaml.safe_load(smoke_text)
    assert "workflow_dispatch" in smoke["on"]
    assert "schedule" in smoke["on"]
    for token in (
        "AGOS_PLANNER_SMOKE",
        "AGOS_REVIEWER_SMOKE",
        "AGOS_CODEX_WORKER_SMOKE",
        "AGOS_CLAUDE_WORKER_SMOKE",
        "AGOS_MULTICA_WORKER_SMOKE",
        "AGOS_OPENHANDS_WORKER_SMOKE",
    ):
        assert token in smoke_text
```

- [ ] **Step 4: Run the policy test to verify RED**

Run: `python -m pytest tests/ci/test_autonomous_loop_ci_policy.py -q`

Expected: FAIL because `real-agent-smoke` is still in `ci.yml` and the opt-in workflow does not exist.

- [ ] **Step 5: Move provider smokes to an opt-in workflow**

Remove the `real-agent-smoke` job from `ci.yml`. Create `real-agent-smoke.yml` with:

```yaml
name: real-agent-smoke
on:
  workflow_dispatch:
  schedule:
    - cron: "17 3 * * 1"

jobs:
  real-agent-smoke:
    runs-on: ubuntu-latest
    env:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      MULTICA_API_KEY: ${{ secrets.MULTICA_API_KEY }}
      MULTICA_BASE_URL: ${{ vars.MULTICA_BASE_URL }}
      AGOS_PLANNER_SMOKE: "1"
      AGOS_PLANNER_EXECUTOR: codex_cli
      AGOS_PLANNER_BIN: codex
      AGOS_REVIEWER_SMOKE: "1"
      AGOS_REVIEWER_EXECUTOR: codex_cli
      AGOS_REVIEWER_BIN: codex
      AGOS_CODEX_WORKER_SMOKE: "1"
      AGOS_CODEX_BIN: codex
      AGOS_CLAUDE_WORKER_SMOKE: "1"
      AGOS_CLAUDE_BIN: claude
      AGOS_MULTICA_WORKER_SMOKE: "1"
      AGOS_MULTICA_BIN: ${{ vars.AGOS_MULTICA_BIN }}
      AGOS_MULTICA_AGENT: ${{ vars.AGOS_MULTICA_AGENT }}
      AGOS_OPENHANDS_WORKER_SMOKE: "1"
      AGOS_OPENHANDS_ENDPOINT: ${{ secrets.AGOS_OPENHANDS_ENDPOINT }}
      AGOS_OPENHANDS_TOKEN: ${{ secrets.AGOS_OPENHANDS_TOKEN }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - run: python -m pip install --upgrade pip
      - run: python -m pip install -e ".[dev]"
      - name: install local agent CLIs
        run: npm install -g @openai/codex @anthropic-ai/claude-code
      - name: run real planner/reviewer/worker smokes
        shell: bash
        run: |
          export AGOS_MULTICA_BIN="${AGOS_MULTICA_BIN:-multica}"
          export AGOS_MULTICA_AGENT="${AGOS_MULTICA_AGENT:-Lambda}"
          python -m pytest \
            tests/integration/test_planner_cli_opt_in.py \
            tests/integration/test_reviewer_cli_opt_in.py \
            tests/integration/test_worker_adapters_opt_in.py \
            -q
```

- [ ] **Step 6: Verify GREEN and commit**

Run:

```bash
python -m pytest tests/ci/test_autonomous_loop_ci_policy.py tests/web/test_api.py tests/web/test_server.py -q
```

Expected: all selected tests pass.

Commit:

```bash
git add .github/workflows tests/ci/test_autonomous_loop_ci_policy.py tests/web/test_api.py tests/web/test_server.py
git commit -m "ci: make required verification provider-independent"
```

---

### Task 2: Bounded Worker Polling Without Premature Stuck State

**Files:**
- Modify: `src/agos/core/execution_pipeline.py`
- Modify: `tests/core/test_execution_pipeline.py`

**Interfaces:**
- Consumes: `AGOSConfig.workers[*].poll_interval_seconds`, `ExecutionPlan`, `ExecutionRuntime.tick`.
- Produces: `_run_prepared_plan(service, plan, *, sleeper=time.sleep)` and deterministic `stuck` only after the configured iteration budget.

- [ ] **Step 1: Replace the repeated-state test with failing poll-budget tests**

Write one test where three identical running snapshots are returned with `max_tick_iterations=2` and a
worker interval of 2 seconds. Inject `sleeps.append` and assert:

```python
assert result.state == "stuck"
assert tick_run_ids == ["auto-run-01", "auto-run-01", "auto-run-01"]
assert sleeps == [2, 2]
```

Write a second test whose third snapshot is `completed` and assert the same two sleeps plus
`result.state == "completed"`.

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```bash
python -m pytest \
  tests/core/test_execution_pipeline.py::test_run_prepared_plan_waits_for_repeated_running_state_until_budget_exhausted \
  tests/core/test_execution_pipeline.py::test_run_prepared_plan_completes_after_repeated_running_state -q
```

Expected: FAIL because `_run_prepared_plan` has no sleeper argument and stops after the repeated state.

- [ ] **Step 3: Implement interval-aware bounded polling**

Import `replace` from `dataclasses`, `time`, and `Callable`. Change the private function signature to:

```python
def _run_prepared_plan(
    service: ExecutionService,
    plan,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> ExecutionRuntimeSnapshot:
```

Remove the repeated-state key. For each nonterminal poll iteration, calculate:

```python
interval = _running_poll_interval(config, plan, snapshot.running_subtasks)
if interval > 0:
    sleeper(interval)
snapshot = runtime.tick(plan, run_id=run_id)
```

After the loop, if state remains `queued` or `running`, return `replace(snapshot, state="stuck")`.
Implement `_running_poll_interval` by mapping running subtask IDs to their configured worker adapters and
returning the maximum `poll_interval_seconds`, defaulting to zero when nothing is running.

- [ ] **Step 4: Update the automatic pipeline note**

Change the stuck note to:

```python
"execution runtime exhausted its polling budget; resume or inspect persisted worker state"
```

- [ ] **Step 5: Verify GREEN, run the execution suite, and commit**

Run:

```bash
python -m pytest tests/core/test_execution_pipeline.py tests/core/test_execution_runtime.py -q
```

Expected: all selected tests pass with no real sleeps.

Commit:

```bash
git add src/agos/core/execution_pipeline.py tests/core/test_execution_pipeline.py
git commit -m "fix: honor worker polling budgets"
```

---

### Task 3: Require Accepted Candidate Decision Evidence

**Files:**
- Modify: `src/agos/core/merge_gate.py`
- Modify: `src/agos/cli/cmd_merge_gate.py`
- Modify: `src/agos/cli/cmd_prepare_merge_gate.py`
- Modify: `tests/core/test_merge_gate.py`
- Modify: `tests/ci/test_merge_gate_smoke.py`
- Modify: `tests/cli/test_merge_gate.py`

**Interfaces:**
- Consumes: `CandidatePatch.decision_ref`, `ExecutionStore.read_decisions`, `ArbiterDecision`, candidate apply ledger records.
- Produces: `candidate_decisions` merge-gate check and `--allow-legacy-decisionless` compatibility option.

- [ ] **Step 1: Add failing merge-gate tests for missing and stale decisions**

Extend the merge-gate fixture helper with `with_decision: bool = True`. For accepted/applied candidates,
write an accepted `ArbiterDecision` whose evidence refs contain the patch, all test refs, and the completed
review report; bind its returned ref to the candidate.

Add these two tests:

```python
def test_merge_gate_blocks_applied_candidate_without_decision(tmp_repo):
    paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="applied", clean_review=True, with_decision=False)
    result = verify_merge_gate(paths)
    assert _check(result, "candidate_decisions").state == "block"
    assert "missing decision_ref" in "; ".join(_check(result, "candidate_decisions").details)


def test_merge_gate_allows_explicit_legacy_decisionless_candidate(tmp_repo):
    paths = _write_active_task(tmp_repo)
    _write_candidate(paths, status="applied", clean_review=True, with_decision=False)
    result = verify_merge_gate(paths, allow_legacy_decisionless=True)
    assert _check(result, "candidate_decisions").state == "pass"
    assert "legacy decisionless" in "; ".join(_check(result, "candidate_decisions").details)
```

Add these focused tests with the stated assertions:

```python
def test_merge_gate_blocks_rejected_candidate_decision(...):
    assert "decision is not accepted" in decision_details


def test_merge_gate_blocks_decision_for_different_candidate(...):
    assert "candidate_id mismatch" in decision_details


def test_merge_gate_blocks_decision_missing_required_evidence(...):
    assert "missing evidence refs" in decision_details


def test_merge_gate_blocks_missing_decision_file(...):
    assert "decision evidence not found" in decision_details


def test_merge_gate_blocks_applied_event_with_stale_decision_ref(...):
    assert "candidate_applied decision_ref does not match" in decision_details
```

Each fixture setup writes a valid applied candidate first and changes only the variable named by the
test, so failures identify the intended invariant.

- [ ] **Step 2: Run new tests to verify RED**

Run: `python -m pytest tests/core/test_merge_gate.py -k decision -q`

Expected: FAIL because `verify_merge_gate` has no decision validation or compatibility argument.

- [ ] **Step 3: Implement the merge-gate decision check**

Add `allow_legacy_decisionless: bool = False` to `verify_merge_gate`. Add a
`candidate_decisions` check built from a helper returning `(issues, warnings)`.

For every accepted/applied candidate, the helper must verify:

```python
required_refs = {candidate.patch_ref, *candidate.test_refs, latest_review.report_ref}
decision.decision == "accepted"
decision.candidate_id == candidate.id
required_refs <= set(decision.evidence_refs)
candidate.decision_ref == f"execution/decisions/{decision.id}.json"
```

For applied candidates, require exactly one matching `candidate_applied` record and require its
`decision_ref` to equal the candidate ref. When compatibility is enabled, only a missing ref/file is a
warning; contradictory or rejected decisions still block.

- [ ] **Step 4: Expose the compatibility option through the CLI**

Add:

```python
allow_legacy_decisionless: bool = typer.Option(
    False,
    "--allow-legacy-decisionless",
    help="Allow legacy applied candidates that predate decision evidence.",
)
```

Pass it to `verify_merge_gate` and add CLI tests for default block and explicit compatibility pass-through.

- [ ] **Step 5: Make CI preparation persist a real arbiter decision**

Change `_materialize_clean_ci_review` to leave the candidate `reviewed`. Add a temporary Phase 1 helper
that constructs `CandidateDecisionSnapshot`, calls `CandidateDecisionArbiter.decide`, writes the decision,
and updates the candidate to `accepted` with `decision_ref`. Only then mark it `applied` and append the
apply event. Phase 2 will remove the synthetic review and reconstructed apply entirely.

- [ ] **Step 6: Update merge-gate fixtures and smoke evidence**

Write accepted decisions in `tests/core/test_merge_gate.py` and `tests/ci/test_merge_gate_smoke.py` using
the same patch, test, and review refs bound to each candidate. Do not use the legacy flag in strict smoke.

- [ ] **Step 7: Verify GREEN and commit**

Run:

```bash
python -m pytest tests/core/test_merge_gate.py tests/cli/test_merge_gate.py tests/ci/test_merge_gate_smoke.py -q
```

Expected: all selected tests pass.

Commit:

```bash
git add src/agos/core/merge_gate.py src/agos/cli/cmd_merge_gate.py \
  src/agos/cli/cmd_prepare_merge_gate.py tests/core/test_merge_gate.py \
  tests/cli/test_merge_gate.py tests/ci/test_merge_gate_smoke.py
git commit -m "fix: require accepted candidate decisions"
```

---

### Task 4: Phase 1 Verification

**Files:**
- Modify only if verification exposes a Phase 1 regression.

**Interfaces:**
- Consumes: all Phase 1 changes.
- Produces: a green offline baseline suitable for Phase 2.

- [ ] **Step 1: Run formatting and static verification**

Run:

```bash
python -m ruff check src tests
python -m compileall -q src tests
```

Expected: both commands exit zero.

- [ ] **Step 2: Run the complete offline suite with coverage**

Run:

```bash
python -m pytest --cov=agos --cov-report=term-missing -q
```

Expected: zero failures and total coverage at least 90 percent. Provider smoke tests are skipped.

- [ ] **Step 3: Build and inspect distributions**

Run:

```bash
python -m build
python -c "from pathlib import Path; assert list(Path('dist').glob('agos-*.whl')); assert list(Path('dist').glob('agos-*.tar.gz'))"
```

Expected: sdist and wheel build successfully.

- [ ] **Step 4: Review branch diff and commit any verification-only correction**

Run:

```bash
git diff --check
git status --short
git log --oneline --decorate -5
```

Expected: no whitespace errors and only intentional files differ from `origin/main`.
