## Why

The repository declares `coverage.report.fail_under = 90` in `pyproject.toml`, but the current full coverage run reports about 88.85% total coverage and exits non-zero. This blocks the documented release verification path and weakens confidence in the Alpha delivery baseline.

## What Changes

- Add focused tests for existing behavior in under-covered modules until `python -m pytest --cov=agos --cov-report=term-missing -q` reaches at least 90% total coverage.
- Keep the change test-only unless a newly written regression test exposes an actual bug; any implementation change must follow a red/green cycle.
- Preserve existing behavior and public CLI/API surfaces.
- Update the change tasks with exact verification commands and mark completion only after the full coverage command passes.

## Capabilities

### New Capabilities
- `coverage-quality-gate`: Defines the requirement that the repository's full coverage verification command must satisfy the configured 90% minimum.

### Modified Capabilities

## Impact

- Affected code is expected to be concentrated in tests, especially modules with low coverage such as dashboard CLI/server paths, web API branches, initialization CLI branches, orchestration node backends, and transport/error paths.
- No runtime dependencies or public command semantics are expected to change.
- Verification depends on `pytest`, `pytest-cov`, and the existing `pyproject.toml` coverage configuration.
