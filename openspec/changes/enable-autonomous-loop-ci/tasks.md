## 1. Repository Configuration

- [x] 1.1 Add a CI policy test asserting `.agos/agos.yaml` enables planner, multiple workers, and a required LLM CLI reviewer.
- [x] 1.2 Track and update `.agos/agos.yaml` with cross-platform planner, multi-worker, reviewer, and orchestration settings.
- [x] 1.3 Verify `agos config validate --json` and `agos doctor --json` on the enabled config.

## 2. CI Policy

- [x] 2.1 Add a CI policy test asserting autonomous readiness and real-agent smoke jobs exist and no merge-gate step uses `--allow-missing-review`.
- [x] 2.2 Add an `autonomous-readiness` GitHub Actions job.
- [x] 2.3 Add a `real-agent-smoke` GitHub Actions job with planner/reviewer/Codex/Claude/Multica/OpenHands smoke variables enabled.
- [x] 2.4 Remove `--allow-missing-review` from CI merge-gate invocation.

## 3. Merge-Gate Review Evidence

- [x] 3.1 Change prepare-merge-gate tests to require completed candidate-bound review evidence without `--allow-missing-review`.
- [x] 3.2 Make `prepare-merge-gate` materialize a completed clean CI review binding for PR diff candidates.

## 4. Smoke Coverage

- [x] 4.1 Add Claude worker real smoke coverage behind `AGOS_CLAUDE_WORKER_SMOKE=1`.
- [x] 4.2 Document CI secrets/variables and local validation commands.

## 5. Verification

- [x] 5.1 Run focused tests for CI policy, prepare-merge-gate, and worker opt-in smoke skip behavior.
- [x] 5.2 Run lint and compile checks.
- [x] 5.3 Run the full test suite.
