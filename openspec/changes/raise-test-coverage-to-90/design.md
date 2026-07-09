## Context

The project already declares a repository-wide coverage gate in `pyproject.toml`:

```toml
[tool.coverage.report]
fail_under = 90
```

The current complete command, `python -m pytest --cov=agos --cov-report=term-missing -q`, runs the suite successfully but exits non-zero because total coverage is below the configured threshold. The lowest-impact way to satisfy the gate is to add focused tests for existing behavior in under-covered modules rather than changing runtime logic.

## Goals / Non-Goals

**Goals:**

- Raise measured total coverage to at least 90%.
- Prefer focused tests for existing public behavior and error branches.
- Keep runtime behavior unchanged unless a red test exposes a real defect.
- Preserve the existing coverage command and `fail_under = 90` configuration.
- Keep tests deterministic and independent of real external services.

**Non-Goals:**

- Do not lower or remove the coverage threshold.
- Do not mark lines with coverage pragmas merely to game the metric.
- Do not require real Codex, Claude, Multica, OpenHands, GitHub, or network services.
- Do not refactor production modules solely for coverage unless necessary to make behavior testable.

## Decisions

1. **Target low-risk uncovered branches first.**
   - Focus on CLI error paths, web server dispatch/error handling, web API validation branches, orchestration node backend validation, and adapter transport failures.
   - These modules have many deterministic branches that can be covered without external tools.
   - Alternative considered: broad integration tests. Rejected because they are slower and may require environment-specific setup.

2. **Use TDD for every production behavior change.**
   - If new tests cover existing behavior, no production code change is needed.
   - If a test exposes an actual bug, verify RED first, implement the minimal fix, and verify GREEN.
   - Alternative considered: editing code first to make testing easier. Rejected because this task is explicitly about confidence and coverage.

3. **Measure completion only with the full repository coverage command.**
   - Focused tests guide development, but success is defined by `python -m pytest --cov=agos --cov-report=term-missing -q` exiting zero.
   - Alternative considered: module-level coverage estimates. Rejected because they can diverge from the configured project gate.

4. **Keep generated artifacts separate from source changes.**
   - OpenSpec artifacts document the change.
   - Test files hold implementation evidence.
   - No changes to release artifacts or generated build directories are needed.

## Risks / Trade-offs

- Coverage could remain below 90 after initial tests → Re-run the full report and choose the next uncovered deterministic branches.
- Some branches depend on platform-specific behavior → Prefer tests that monkeypatch command runners or HTTP handlers instead of invoking external systems.
- Tests may become brittle if they assert implementation details → Assert public return values, CLI output/exit codes, or documented model fields.
- Existing working tree already has unrelated documentation edits → Keep coverage changes focused and avoid touching unrelated files.

## Migration Plan

1. Add tests in small batches.
2. Run each focused test file before moving on.
3. Run lint/compile checks for modified tests.
4. Run full coverage command and stop when it passes.
5. Mark OpenSpec tasks complete only after verification evidence exists.
