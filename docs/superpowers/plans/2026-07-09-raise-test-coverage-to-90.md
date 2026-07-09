# Raise Test Coverage To 90 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise AGOS repository coverage from the measured 88.85% baseline to at least the configured 90% gate.

**Architecture:** Add deterministic tests for existing behavior in under-covered modules. Production code stays unchanged unless a focused test exposes an actual defect, in which case RED/GREEN/REFACTOR applies.

**Tech Stack:** Python 3.14 local runtime, pytest, pytest-cov, Typer CliRunner, stdlib HTTP server/urllib, OpenSpec CLI.

---

## File Structure

- Modify: `E:/AGOS_V2/agos/tests/cli/test_dashboard.py` — add CLI error/interrupt branch coverage for `agos dashboard`.
- Modify: `E:/AGOS_V2/agos/tests/core/test_orchestration_node_backends.py` — add reviewer backend, arbiter cancel, worker cancel/collect, and state/output-ref mapping tests.
- Modify: `E:/AGOS_V2/agos/tests/web/test_server.py` — add dashboard server request body and POST error branch tests using local loopback only.
- No planned production file modifications.

### Task 1: Dashboard CLI branches

**Files:**
- Modify: `E:/AGOS_V2/agos/tests/cli/test_dashboard.py`

- [ ] **Step 1: Add tests for KeyboardInterrupt and generic server errors**

```python
def test_dashboard_command_exits_zero_on_keyboard_interrupt(monkeypatch, tmp_repo) -> None:
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_dashboard.serve_dashboard_forever", interrupt)

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 0


def test_dashboard_command_reports_server_error(monkeypatch, tmp_repo) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("bind failed")

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_dashboard.serve_dashboard_forever", fail)

    result = runner.invoke(app, ["dashboard"])

    assert result.exit_code == 1
    assert "bind failed" in result.stderr
```

- [ ] **Step 2: Run focused test**

Run: `python -m pytest tests/cli/test_dashboard.py -q`
Expected: all tests in the file pass.

### Task 2: Orchestration node backend branches

**Files:**
- Modify: `E:/AGOS_V2/agos/tests/core/test_orchestration_node_backends.py`

- [ ] **Step 1: Add fake reviewer imports/classes and lifecycle tests**

```python
from agos.core.review_adapter import ReviewerRun, ReviewerRunStatus


class FakeReviewer:
    name = "fake-reviewer"

    def start(self, request):
        self.request = request
        return ReviewerRun(backend=self.name, run_id=request.run_id, reviewer_id=request.reviewer_id, state="running")

    def poll(self, run_id: str, *, reviewer_id: str):
        return ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id=reviewer_id,
            state="completed",
            raw_ref="reviews/reviewer.json",
        )

    def cancel(self, run_id: str):
        return ReviewerRunStatus(
            backend=self.name,
            run_id=run_id,
            reviewer_id="reviewer-a",
            state="cancelled",
        )
```

Then add tests for `ReviewerNodeBackend.start/poll/cancel/collect`, worker cancel/collect, `_single_output_ref` with empty refs, and `_node_state` mapping through public backend methods.

- [ ] **Step 2: Run focused test**

Run: `python -m pytest tests/core/test_orchestration_node_backends.py -q`
Expected: all tests in the file pass.

### Task 3: Dashboard HTTP server error branches

**Files:**
- Modify: `E:/AGOS_V2/agos/tests/web/test_server.py`

- [ ] **Step 1: Add POST body validation and internal error tests**

Add tests that send request bodies with invalid Content-Length, empty body, malformed JSON, non-object JSON, and oversized body to existing POST routes. Add monkeypatched route tests for internal errors on archive/continue/simple/review/select POST handlers. Use `urllib.request` against `running_dashboard_server` only.

- [ ] **Step 2: Run focused test**

Run: `python -m pytest tests/web/test_server.py -q`
Expected: all tests in the file pass.

### Task 4: Full verification

**Files:**
- Modify: `E:/AGOS_V2/agos/openspec/changes/raise-test-coverage-to-90/tasks.md` after verification to check off completed tasks.

- [ ] **Step 1: Run lint**

Run: `python -m ruff check src tests`
Expected: exit code 0.

- [ ] **Step 2: Run compile check**

Run: `python -m compileall -q src tests`
Expected: exit code 0.

- [ ] **Step 3: Run full coverage gate**

Run: `python -m pytest --cov=agos --cov-report=term-missing -q`
Expected: exit code 0 and total coverage at least 90%.

- [ ] **Step 4: Validate OpenSpec**

Run: `openspec.cmd validate raise-test-coverage-to-90 --strict`
Expected: exit code 0.

- [ ] **Step 5: Update OpenSpec task checkboxes**

Mark `E:/AGOS_V2/agos/openspec/changes/raise-test-coverage-to-90/tasks.md` tasks complete only after evidence exists.
