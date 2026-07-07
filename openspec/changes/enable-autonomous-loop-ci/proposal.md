## Why

The autonomous-agent-review-loop code is now present, but the repository still needs an enabled default configuration and CI policy that prove the loop rather than documenting it as optional. This change closes the remaining gap: planner-backed decomposition, multiple local workers, required local agent review, and CI-visible real-agent smoke coverage.

## What Changes

- Track a repository `.agos/agos.yaml` that enables the planner, configures at least two workers, and requires a local LLM CLI reviewer.
- Add CI readiness verification for the enabled autonomous loop.
- Add CI real-agent smoke coverage for planner, reviewer, Codex worker, Claude worker, Multica worker, and OpenHands worker paths.
- Remove the merge-gate `--allow-missing-review` escape hatch from CI and make `prepare-merge-gate` materialize candidate-bound clean review evidence for PR diff candidates.
- Document required CI secrets, variables, and local validation commands.

## Capabilities

### New Capabilities
- `autonomous-loop-ci-enablement`: Covers repository-default autonomous loop configuration and CI proof requirements.

### Modified Capabilities
- None. The previous autonomous loop capability remains implemented; this change enables and proves it at repository/CI policy level.

## Impact

- Affected config/docs:
  - `.agos/agos.yaml`
  - `.github/workflows/ci.yml`
  - `README.md`
- Affected code/tests:
  - `src/agos/cli/cmd_prepare_merge_gate.py`
  - `tests/cli/test_prepare_merge_gate.py`
  - `tests/integration/test_worker_adapters_opt_in.py`
  - `tests/ci/test_autonomous_loop_ci_policy.py`
- CI now expects real-agent smoke credentials/endpoints when the real-agent smoke job runs.
