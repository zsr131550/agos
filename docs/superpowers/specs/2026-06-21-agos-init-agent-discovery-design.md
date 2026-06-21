# AGOS Init Agent Discovery Design

**Status:** Draft (pending user review)  
**Date:** 2026-06-21  
**Author:** AGOS project

## Goal

Make `agos init` reject missing executor-agent configuration by detecting locally available Multica agents and requiring the user to choose one explicitly with `--agent`.

## Context

Today `agos init` defaults to `Lambda` when `--agent` is omitted. That default is encoded in:

- [src/agos/cli/cmd_init.py](/E:/AGOS_V2/agos/src/agos/cli/cmd_init.py:57)
- [src/agos/core/config.py](/E:/AGOS_V2/agos/src/agos/core/config.py:33)
- [tests/integration/test_round_trip.py](/E:/AGOS_V2/agos/tests/integration/test_round_trip.py:22)

This default is not portable across real Multica workspaces. In the current local environment, `multica agent list --output json` returns agents such as `codex-gpt-5.4 xhigh` and `glm-5.2`, not `Lambda`. As a result, AGOS can initialize a repo successfully and only fail later during `agos start`, which is a poor user experience and weakens confidence in the generated config.

The new design removes the product assumption that AGOS can invent a valid default agent name. Instead, AGOS must either:

- use an explicit `--agent` provided by the user, or
- detect available local agents and require the user to choose one explicitly.

## Product Decision

When the user does not pass `--agent`, `agos init` must:

1. Attempt to discover local Multica agents.
2. Exit non-zero.
3. Print the discovered candidates, if any.
4. Instruct the user to re-run `agos init --agent "<name>"`.

AGOS must not:

- silently fall back to `Lambda`
- auto-select one candidate
- prompt interactively

This keeps AGOS aligned with its governance-oriented posture: explicit configuration beats inferred execution identity.

## User Experience

### Case 1: User passes `--agent`

Command:

```bash
agos init --executor multica --agent "codex-gpt-5.4 xhigh"
```

Behavior:

- AGOS writes the config using the explicit value.
- AGOS does not choose a different agent.
- AGOS may perform lightweight validation when local discovery succeeds.

### Case 2: User omits `--agent` and discovery finds candidates

Command:

```bash
agos init
```

Behavior:

- AGOS exits with an error before writing `.agos/agos.yaml`.
- stderr prints a stable message including the candidate names.

Recommended output shape:

```text
No default agent configured and --agent was not provided.

Available Multica agents:
- codex-gpt-5.4 xhigh
- glm-5.2

Re-run with:
  agos init --agent "codex-gpt-5.4 xhigh"
```

### Case 3: User omits `--agent` and discovery returns no candidates

Behavior:

- AGOS exits with an error before writing config.
- stderr explains that no agents were found in the current workspace.

Recommended output shape:

```text
No default agent configured and --agent was not provided.

No available Multica agents were found in the current workspace.
Create or enable an agent in Multica, then re-run:
  agos init --agent "<agent-name>"
```

### Case 4: User omits `--agent` and discovery cannot run

Behavior:

- AGOS exits with an error before writing config.
- stderr includes a short diagnostic summary from the failed `multica agent list` call.

Recommended output shape:

```text
No default agent configured and --agent was not provided.

Could not discover Multica agents for the current workspace:
  multica agent list failed: <reason>

Re-run with:
  agos init --agent "<agent-name>"
```

## Architecture

Keep the feature local to `agos init`. Do not move this policy into `start`.

### New internal functions

In [src/agos/cli/cmd_init.py](/E:/AGOS_V2/agos/src/agos/cli/cmd_init.py:1), add:

- `discover_multica_agents() -> list[str]`
- `resolve_init_agent(agent: str | None) -> str`

#### `discover_multica_agents()`

Responsibility:

- run `multica agent list --output json`
- parse the JSON payload
- return a list of human-usable agent names

Rules:

- include only non-empty `name` values
- preserve CLI order
- ignore malformed list items instead of crashing on partial bad data
- treat CLI execution errors and JSON parse failures as discovery failures

This function is intentionally low-level so it can be reused later by other commands such as `doctor` or future setup tooling.

#### `resolve_init_agent(agent: str | None) -> str`

Responsibility:

- implement the product policy for `agos init`

Rules:

- if `agent` is provided, return it unchanged unless lightweight validation can prove it is invalid
- if `agent` is omitted, call `discover_multica_agents()` and fail with one of the explicit error modes above

This function is intentionally policy-oriented. If AGOS later changes behavior, such as auto-accepting the single available agent, only this layer should change.

## Config Model Changes

The config model must stop encoding `Lambda` as a product default.

Update [src/agos/core/config.py](/E:/AGOS_V2/agos/src/agos/core/config.py:33) so that:

- `ExecutorConfig.agent` does not imply a universal default
- `AGOSConfig.default(...)` requires a resolved agent string from the caller
- `default_config(...)` also requires a resolved agent string

Practical intent:

- configuration creation still needs an agent
- the responsibility for deciding that agent moves out of `AGOSConfig` and into `cmd_init`

This keeps the core config layer neutral and removes a false product invariant.

## Validation Policy

When the user explicitly passes `--agent`, AGOS should prefer the explicit value, but may perform lightweight validation.

Recommended policy:

- if local discovery succeeds and the provided agent is not in the discovered names, fail early and list valid candidates
- if local discovery fails, do not reject the explicit agent solely because discovery failed

This gives early feedback for obvious typos without turning temporary local discovery problems into unnecessary blockers for explicit configuration.

## Error Handling

### `multica` not installed or not on PATH

Treat as a discovery failure. The error should clearly say that AGOS could not discover agents because `multica` could not be executed.

### `multica agent list` exits non-zero

Treat as a discovery failure. Include a compact stderr or stdout summary, whichever is more informative.

### `multica agent list` returns invalid JSON

Treat as a discovery failure with a stable parse-error summary.

### Empty agent list

Treat as a valid discovery result with zero candidates, not as an execution failure.

### Explicit invalid agent

If discovery succeeded, fail with a message that includes available candidates.

## Interaction With Existing Environment Checks

Do not merge this policy into `validate_multica_environment()`.

Reason:

- `validate_multica_environment()` is a non-blocking warning mechanism
- agent discovery for missing `--agent` is a blocking policy gate

Mixing them would blur the line between advisory diagnostics and hard requirements.

## File-Level Plan

### Modify

- [src/agos/cli/cmd_init.py](/E:/AGOS_V2/agos/src/agos/cli/cmd_init.py:1)
  - add discovery and resolution helpers
  - change `init_command()` so `agent` becomes optional at the CLI layer
  - resolve the final agent before config creation

- [src/agos/core/config.py](/E:/AGOS_V2/agos/src/agos/core/config.py:33)
  - remove the misleading `Lambda` default semantics
  - require a resolved agent when generating default config

- [tests/cli/test_init.py](/E:/AGOS_V2/agos/tests/cli/test_init.py:1)
  - update the old default-agent assumptions
  - add discovery-path tests

- [tests/integration/test_round_trip.py](/E:/AGOS_V2/agos/tests/integration/test_round_trip.py:22)
  - stop encoding `Lambda` as the implicit fallback
  - require `AGOS_INTEGRATION_AGENT` for real runs, or otherwise align the test with the new explicit-agent contract

### Optional new test module

- `tests/cli/test_init_agent_discovery.py`

Only add this if `test_init.py` becomes noisy. Otherwise keep the coverage in the existing test file.

## Test Strategy

### Unit tests

Add or update tests for these scenarios:

1. Explicit `--agent` succeeds and writes the provided value.
2. Missing `--agent` plus multiple discovered agents fails and prints candidates.
3. Missing `--agent` plus zero discovered agents fails with the empty-workspace message.
4. Missing `--agent` plus discovery execution failure fails with diagnostic output.
5. Explicit `--agent` plus successful discovery where the name is absent fails with candidate guidance.
6. Explicit `--agent` plus discovery failure still succeeds, preserving explicit configuration priority.

### Integration tests

Adjust the real Multica integration path so it no longer relies on a fake portable default.

Recommended behavior:

- require `AGOS_INTEGRATION_AGENT` for the integration run, or
- make the test invoke `agos init --agent <env value>` explicitly and skip if the env var is absent

This keeps the real integration contract honest and portable across workspaces.

## Non-Goals

This design does not include:

- interactive prompts
- automatic agent selection
- fuzzy matching or alias resolution
- agent selection during `agos start`
- daemon auto-start

## Success Criteria

This feature is successful when all of the following are true:

1. `agos init` no longer writes a config containing an invented default agent name.
2. A user who omits `--agent` gets a clear list of locally available agents and a copy-pastable next command.
3. A user who supplies an explicit valid agent can still initialize the repo in one command.
4. Tests cover the explicit and discovery failure paths.
5. The integration test no longer encodes `Lambda` as a supposed universal default.

