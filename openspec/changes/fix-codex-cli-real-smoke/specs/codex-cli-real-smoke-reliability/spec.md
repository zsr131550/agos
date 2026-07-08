## ADDED Requirements

### Requirement: Windows CLI command resolution uses executable shims
AGOS SHALL resolve bare CLI command names on Windows to executable command shims when the default PATH result is a PowerShell script.

#### Scenario: Codex bare command resolves to cmd shim
- **WHEN** AGOS is asked to run the bare command `codex` on Windows and PATH contains both `codex.ps1` and `codex.cmd`
- **THEN** AGOS SHALL execute `codex.cmd`
- **AND** AGOS SHALL keep structured argv execution without enabling shell command parsing

#### Scenario: Existing executable command remains unchanged
- **WHEN** AGOS is asked to run a command that already resolves to an executable shim or binary
- **THEN** AGOS SHALL execute that resolved command without rewriting it to a PowerShell script

### Requirement: Codex worker uses local Codex configuration by default
AGOS SHALL invoke Codex worker executions through the local Codex CLI without ignoring user configuration unless explicitly configured to do so.

#### Scenario: Default worker invocation preserves user config
- **WHEN** a `CodexWorkerAdapter` starts a worker with default settings
- **THEN** the command argv SHALL NOT include `--ignore-user-config`
- **AND** the command argv SHALL NOT include `--ignore-rules`

#### Scenario: Hermetic worker invocation remains available
- **WHEN** a `CodexWorkerAdapter` is configured to ignore user config and rules
- **THEN** the command argv SHALL include `--ignore-user-config`
- **AND** the command argv SHALL include `--ignore-rules`

### Requirement: Codex planner smoke returns an execution plan object
AGOS SHALL prompt the local Codex planner CLI in a form that returns an extractable execution plan JSON object during opt-in real smoke.

#### Scenario: Planner returns JSONL protocol events
- **WHEN** `codex exec --json` returns protocol events containing an `agent_message` with the execution plan JSON
- **THEN** AGOS SHALL extract the nested execution plan object
- **AND** the extracted object SHALL include `subtasks`

#### Scenario: Local Codex rules are present
- **WHEN** the local Codex CLI uses the user's configured authentication and local rules/plugins
- **THEN** the planner smoke prompt SHALL still request an exact execution plan object
- **AND** AGOS SHALL validate the returned object at the existing planning boundary

### Requirement: Reviewer real smoke proves completed review
The Codex/Claude reviewer real-smoke test SHALL fail when the reviewer adapter reports a failed review status.

#### Scenario: Reviewer adapter fails closed
- **WHEN** the reviewer adapter returns a terminal failed status during opt-in real-smoke execution
- **THEN** the reviewer smoke test SHALL fail
- **AND** the failure SHALL expose the reviewer status detail for diagnosis
