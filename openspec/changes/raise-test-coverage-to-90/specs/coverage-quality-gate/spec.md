## ADDED Requirements

### Requirement: Repository coverage gate passes
The repository SHALL satisfy its configured coverage threshold when running the full coverage verification command from the repository root.

#### Scenario: Full coverage command meets configured threshold
- **WHEN** `python -m pytest --cov=agos --cov-report=term-missing -q` is run from the repository root
- **THEN** the command exits with code 0
- **AND** total coverage is at least the configured `fail_under` value of 90%

### Requirement: Coverage improvement preserves behavior
The coverage improvement SHALL use deterministic tests for existing behavior unless a failing test exposes an actual defect.

#### Scenario: No external services required
- **WHEN** the coverage tests are run in the default local test environment
- **THEN** they do not require real Codex, Claude, Multica, OpenHands, GitHub, or network services

#### Scenario: Runtime behavior remains stable
- **WHEN** focused tests and the full test suite are run
- **THEN** existing public CLI and API behavior remains compatible
