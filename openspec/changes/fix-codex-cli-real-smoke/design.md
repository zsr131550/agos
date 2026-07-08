## Context

AGOS documents a real-agent smoke path for Codex planner, Codex reviewer, and Codex worker execution. The current implementation has three reliability gaps exposed by local smoke runs on Windows:

- `subprocess` may resolve bare `codex` to `codex.ps1`, which cannot be executed directly under the current policy/permission model and fails with `WinError 5`.
- `CodexWorkerAdapter.start()` always passes `--ignore-user-config`, so local Codex login/configuration is intentionally bypassed even when the user wants the normal local CLI path.
- The reviewer opt-in smoke test only checks for a terminal state, so a failed reviewer run can appear as a passing smoke result.

The planner and reviewer adapters already invoke `codex exec --skip-git-repo-check --json ...`, while the worker adapter invokes `codex exec ... --json ...` in a prepared git workspace. The fix should stay small and avoid changing the overall worker/reviewer/planner abstraction.

## Goals / Non-Goals

**Goals:**

- Prefer executable Windows CLI shims (`.cmd`, `.exe`, `.bat`) over PowerShell scripts when resolving bare commands such as `codex` and `claude`.
- Let Codex worker calls use the user's local Codex configuration by default.
- Preserve a deliberate hermetic mode for tests or production environments that want to ignore user config/rules.
- Make reviewer real-smoke tests fail when the reviewer adapter reports a failed status.
- Cover each behavior with focused tests before implementation.

**Non-Goals:**

- Do not introduce direct OpenAI API calls in AGOS.
- Do not change Codex CLI authentication semantics; AGOS only invokes the CLI.
- Do not require OpenAI, Claude, Multica, or OpenHands credentials for default unit tests.
- Do not redesign the worker/reviewer/planner protocol.
- Do not solve unrelated Multica/OpenHands endpoint configuration failures.

## Decisions

### Decision 1: Resolve Windows commands before execution

`run_command()` should resolve bare command names on Windows before spawning. If `shutil.which("codex")` returns a `.ps1` script, AGOS should look for sibling or PATH-resolved `codex.cmd`, `codex.exe`, or `codex.bat` and execute that instead. This keeps structured argv execution and avoids `shell=True`.

Alternatives considered:

- Use `shell=True`: rejected because AGOS prompts contain task content and shell interpolation would weaken command-injection boundaries.
- Require users to configure `codex.cmd`: rejected because README examples use `codex`, and AGOS should handle common npm CLI installations.

### Decision 2: Make Codex user-config behavior explicit

`CodexWorkerAdapter` should default to using local user configuration. Add constructor flags such as `ignore_user_config` and `ignore_rules`, defaulting to `False`. When enabled, include the existing `--ignore-user-config` and `--ignore-rules` flags. Keep sandbox/approval bypass behavior unchanged for the worker adapter's noninteractive workspace flow.

Alternatives considered:

- Remove the flags entirely: rejected because CI or hermetic callers may still want this behavior.
- Continue ignoring user config: rejected because it contradicts real local Codex smoke expectations.

### Decision 3: Strengthen reviewer smoke semantics

The reviewer opt-in smoke test should require `status.state == "completed"`, not merely `status.is_terminal`. The adapter can still fail closed in production; the smoke test's purpose is to prove a usable real reviewer path.

Alternatives considered:

- Change adapter failure handling to raise: rejected because production orchestration benefits from failed status objects and raw evidence.

### Decision 4: Keep Codex planner prompts minimal and exact

The Codex planner adapter should ask the CLI to return the exact JSON object AGOS already knows is valid instead of combining task prose, field descriptions, and an example. Local Codex installations can load user skills/plugins before answering; the shorter exact-object prompt proved more reliable while preserving the same validated `ExecutionPlan` boundary.

Alternatives considered:

- Keep the descriptive prompt and add more instructions: rejected because local Codex still returned an error-shaped object during real smoke.
- Ignore user config for planner calls: rejected because the user explicitly wants AGOS to use the locally configured Codex CLI path.

## Risks / Trade-offs

- Windows command resolution can pick a different executable than PowerShell would choose → mitigate with narrow tests and only prefer executable shim alternatives for script suffixes.
- Using local Codex config by default makes worker behavior less hermetic → mitigate with explicit `ignore_user_config`/`ignore_rules` flags for callers that need hermetic mode.
- Reviewer smoke may fail more often in underconfigured environments → this is intentional; opt-in real smoke should prove real readiness.
- Exact planner prompts reduce planner creativity → acceptable for smoke and fallback planning because AGOS validates the returned plan and uses the same deterministic shape as fallback.
