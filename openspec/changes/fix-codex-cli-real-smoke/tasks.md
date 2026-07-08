## 1. TDD Coverage

- [x] 1.1 Add a failing command-resolution test proving a bare Windows `codex` command prefers `codex.cmd` over `codex.ps1` without using `shell=True`.
- [x] 1.2 Add failing Codex worker adapter tests proving default argv omits `--ignore-user-config`/`--ignore-rules` and explicit hermetic mode includes them.
- [x] 1.3 Strengthen the reviewer opt-in smoke test so a failed terminal reviewer status fails the test.
- [x] 1.4 Add/update planner prompt coverage for the exact JSON object prompt used by real Codex planner smoke.

## 2. Implementation

- [x] 2.1 Update `run_command()` executable resolution to prefer Windows executable shims when PATH resolution returns a PowerShell script.
- [x] 2.2 Update `CodexWorkerAdapter` with explicit `ignore_user_config` and `ignore_rules` configuration flags, defaulting to local user config.
- [x] 2.3 Ensure configured worker construction can pass the new flags when present without breaking existing config.
- [x] 2.4 Simplify the Codex planner prompt so local Codex returns the execution plan JSON object reliably.

## 3. Verification

- [x] 3.1 Run focused tests for command resolution, Codex worker adapter argv, reviewer smoke semantics, and worker config parsing.
- [x] 3.2 Validate the OpenSpec change with `openspec validate fix-codex-cli-real-smoke --strict`.
- [x] 3.3 Run lint and compile checks for changed Python files.
- [x] 3.4 Run the Codex planner/worker/reviewer real-smoke subset with `codex.cmd` and report whether any remaining failure is external CLI/auth/runtime behavior.
