## Why

The real AGOS Codex loop failed on Windows even after the CLI adapters were configured to call the local `codex` command: multi-line prompts passed through `.cmd` shims were truncated or delivered as empty stdin. This prevented a real automatic planner → worker → reviewer run from producing and reviewing a candidate without manual intervention.

## What Changes

- Send Codex worker prompts through stdin using `codex exec ... -` instead of passing multi-line prompts as argv.
- Send Codex reviewer prompts through stdin using `codex exec ... -` so candidate patch review sees the full diff and review contract.
- Teach the Windows `.cmd` shim runner to pipe `input=` into `stdin=PIPE`, matching `subprocess.run` behavior.
- Lead AGOS non-interactive prompts with the concrete task request before the execution contract to reduce local skill/workflow misclassification.
- Add regression coverage for prompt ordering, Codex worker stdin, Codex reviewer stdin, and `.cmd` stdin piping.

## Capabilities

### New Capabilities
- `autonomous-agent-review-loop`: Documents the real local Codex planner, worker, reviewer loop and its Windows prompt-delivery requirements.

### Modified Capabilities
- None.

## Impact

- Affected code:
  - `src/agos/adapters/noninteractive.py`
  - `src/agos/adapters/workers/codex_cli.py`
  - `src/agos/adapters/workers/transport.py`
  - `src/agos/adapters/reviewers/llm_cli.py`
  - `src/agos/core/command.py`
- Affected tests:
  - `tests/adapters/test_noninteractive.py`
  - `tests/adapters/test_worker_adapters.py`
  - `tests/adapters/test_llm_cli_reviewer.py`
  - `tests/core/test_command.py`
- No dependency or public API changes are required.
