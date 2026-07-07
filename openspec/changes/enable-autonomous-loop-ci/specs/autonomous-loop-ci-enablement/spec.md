## ADDED Requirements

### Requirement: Repository config enables the autonomous loop
The repository SHALL track an AGOS configuration that enables planner-backed automatic decomposition, configures multiple execution workers, and requires at least one local LLM CLI reviewer.

#### Scenario: Planner is enabled
- **WHEN** the repository `.agos/agos.yaml` is loaded
- **THEN** `orchestration.planner.enabled` SHALL be true
- **AND** the planner executor SHALL be a supported local LLM CLI executor

#### Scenario: Multiple workers are configured
- **WHEN** the repository `.agos/agos.yaml` is loaded
- **THEN** at least two workers SHALL be configured
- **AND** `orchestration.max_parallel` SHALL allow at least two concurrent workers

#### Scenario: Required local reviewer is configured
- **WHEN** the repository `.agos/agos.yaml` is loaded
- **THEN** at least one required reviewer SHALL use `codex_cli` or `claude_code`

### Requirement: CI verifies autonomous-loop readiness
CI SHALL validate that the repository's autonomous-loop configuration is parseable and diagnostically ready.

#### Scenario: Readiness job runs
- **WHEN** GitHub Actions runs the default CI workflow
- **THEN** it SHALL run `agos config validate --json`
- **AND** it SHALL run `agos doctor --json`
- **AND** it SHALL run static CI policy tests for autonomous-loop enablement

### Requirement: CI runs real-agent smoke coverage
CI SHALL include real-agent smoke coverage for planner, reviewer, Codex worker, Claude worker, Multica worker, and OpenHands worker paths.

#### Scenario: Real-agent smoke job runs
- **WHEN** GitHub Actions runs the default CI workflow
- **THEN** it SHALL set the relevant AGOS smoke environment variables in CI
- **AND** it SHALL run the planner, reviewer, and worker adapter smoke tests

### Requirement: CI merge gate does not bypass missing review
CI SHALL not pass `--allow-missing-review` to the merge-gate command.

#### Scenario: PR merge gate verifies review evidence
- **WHEN** CI prepares a PR merge-gate candidate from the submitted diff
- **THEN** the prepared candidate SHALL include completed candidate-bound clean review evidence
- **AND** merge-gate SHALL pass only without the missing-review override
