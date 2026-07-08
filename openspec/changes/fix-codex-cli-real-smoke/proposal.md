## Why

The real-agent smoke tests currently do not prove the intended local Codex CLI path: Codex worker invocations ignore the user's local Codex configuration, Windows bare `codex` resolves through a PowerShell shim that fails under `subprocess`, and reviewer smoke can pass even when the reviewer run failed closed. This blocks reliable verification of the repository's documented Codex planner/worker/reviewer readiness.

## What Changes

- Make AGOS resolve npm-installed CLI shims on Windows to executable `.cmd`/`.exe` commands before falling back to PowerShell scripts.
- Stop forcing Codex worker smoke/production calls to ignore the user's local Codex configuration by default.
- Preserve an explicit configuration escape hatch for callers that intentionally want hermetic Codex worker execution.
- Strengthen real reviewer smoke coverage so a failed reviewer run no longer counts as success.
- Add focused tests that reproduce the Windows command-resolution behavior and Codex worker argument contract before changing production code.

## Capabilities

### New Capabilities
- `codex-cli-real-smoke-reliability`: Covers reliable local Codex CLI execution for planner, worker, and reviewer smoke tests across Windows shim resolution and local-user configuration behavior.

### Modified Capabilities
- None. The current repo has no archived main specs; this change adds a focused capability for the real-smoke reliability contract.

## Impact

- Affected code:
  - `src/agos/core/command.py`
  - `src/agos/adapters/workers/codex_cli.py`
  - `tests/core/test_command.py`
  - `tests/adapters/test_worker_adapters.py`
  - `tests/integration/test_reviewer_cli_opt_in.py`
- Affected behavior:
  - Windows AGOS subprocess calls prefer executable CLI shims such as `codex.cmd` over `codex.ps1`.
  - Codex worker calls use local Codex user configuration unless explicitly configured otherwise.
  - Reviewer real-smoke tests require a completed reviewer status.
- No new runtime dependencies.
