## ADDED Requirements

### Requirement: Codex worker prompt delivery
AGOS SHALL deliver the full non-interactive Codex worker prompt to the local Codex CLI without relying on multi-line argv transport.

#### Scenario: Worker prompt uses stdin
- **WHEN** AGOS starts a Codex worker for an execution subtask
- **THEN** AGOS SHALL invoke `codex exec` with `-` as the prompt argument
- **AND** AGOS SHALL provide the full task request and execution contract through stdin.

### Requirement: Codex reviewer prompt delivery
AGOS SHALL deliver the full candidate review prompt and candidate patch content to the local Codex reviewer without relying on multi-line argv transport.

#### Scenario: Reviewer prompt uses stdin
- **WHEN** AGOS starts a Codex reviewer for a candidate review packet
- **THEN** AGOS SHALL invoke `codex exec` with `-` as the prompt argument
- **AND** AGOS SHALL provide the full review prompt through stdin.

### Requirement: Windows command shim stdin support
AGOS SHALL preserve stdin input when running Windows `.cmd` or `.bat` command shims through its tree-aware subprocess helper.

#### Scenario: Shim receives piped input
- **WHEN** AGOS runs a `.cmd` or `.bat` shim with `input` provided
- **THEN** AGOS SHALL create a stdin pipe for the shim process
- **AND** AGOS SHALL pass the input to the process through `communicate`.

### Requirement: Real Codex loop evidence
AGOS SHALL be able to complete a real local Codex planner, worker, reviewer loop on a documentation-only task without manual worker or reviewer intervention.

#### Scenario: Automatic dry-run accepts candidate
- **WHEN** a user runs `agos run auto --dry-run --json` for a documentation-only task with local Codex planner, worker, and reviewer configured
- **THEN** the run result SHALL report an LLM planner source, a completed Codex worker subtask, a non-empty candidate id, a Codex reviewer id, a candidate review id, and an accepted candidate id
- **AND** the run result SHALL not report a blocked stage.
