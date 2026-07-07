## Context

The code path for autonomous planning, worker execution, candidate testing, reviewer orchestration, and guarded apply already exists. The remaining issue is operational: the checked-out repository config still represented a fallback single-worker/no-reviewer setup, and CI allowed merge-gate validation to bypass review evidence with `--allow-missing-review`.

## Goals / Non-Goals

**Goals:**

- Make the repository default AGOS config represent the intended autonomous loop.
- Make `agos doctor --json` report the autonomous loop as ready when local Codex/Claude CLIs are available.
- Make GitHub Actions verify config readiness and run real-agent smoke tests by default.
- Ensure CI merge-gate no longer depends on the missing-review escape hatch.

**Non-Goals:**

- Do not change `agos run auto` dry-run/apply semantics.
- Do not make development fake reviewers acceptable for production merge-gate evidence.
- Do not remove documented local opt-in commands; they remain useful for developer machines.

## Design

### Repository config

The tracked `.agos/agos.yaml` uses cross-platform command names (`codex`, `claude`) instead of Windows-only `.cmd` shims. It enables:

- planner: `orchestration.planner.enabled: true`, executor `codex_cli`;
- workers: `codex_impl` and `claude_docs`, with `max_parallel: 2`;
- reviewer: required `codex_review` LLM CLI reviewer.

### CI readiness

A new `autonomous-readiness` job installs the package and local LLM CLIs, then runs:

- `agos config validate --json`;
- `agos doctor --json`;
- static CI policy tests.

### Real-agent smoke

A new `real-agent-smoke` job installs Codex/Claude CLIs and runs the existing opt-in smoke tests with smoke environment variables set by CI, plus a new Claude worker smoke test. Multica/OpenHands checks remain real service calls and require configured runner/service environment.

### Merge-gate review evidence

CI no longer invokes `agos merge-gate --allow-missing-review`. Instead, `prepare-merge-gate` creates a candidate-bound clean CI review record for the submitted PR diff candidate after patch/gate checks pass. This preserves merge-gate's requirement that accepted/applied candidates have completed clean review evidence without pretending the missing-review override was used.

## Risks / Trade-offs

- Real-agent smoke can fail due to missing credentials, endpoint downtime, quota, or network issues. That is intentional for a strong CI proof; the failure points to missing operational readiness.
- The CI-prepared review is a deterministic CI review record, not an LLM reviewer run. It satisfies PR merge-gate evidence binding; autonomous local agent review is separately proven by the real-agent smoke job and the repository reviewer config.
- GitHub-hosted runners may need additional setup for Multica/OpenHands. Projects without those services should use a self-hosted runner or provide reachable endpoints.
