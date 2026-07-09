## 1. Baseline and Target Selection

- [x] 1.1 Confirm the current baseline with `python -m pytest --cov=agos --cov-report=term-missing -q` and record the total coverage plus high-value missing-line targets.
- [x] 1.2 Select deterministic under-covered modules whose behavior can be tested without real external services.

## 2. Focused Coverage Tests

- [x] 2.1 Add focused tests for small CLI and adapter branches that currently have low or moderate coverage.
- [x] 2.2 Add focused tests for orchestration/backend validation and error branches.
- [x] 2.3 Add focused tests for web API/server validation, dispatch, and error-handling branches.
- [x] 2.4 If any new test exposes an actual defect, follow RED/GREEN/REFACTOR and keep the production change minimal.

## 3. Verification

- [x] 3.1 Run focused tests for every modified or newly added test file.
- [x] 3.2 Run `python -m ruff check src tests`.
- [x] 3.3 Run `python -m compileall -q src tests`.
- [x] 3.4 Run `python -m pytest --cov=agos --cov-report=term-missing -q` and verify it exits zero with total coverage at or above 90%.
- [x] 3.5 Run `openspec.cmd validate raise-test-coverage-to-90 --strict`.
