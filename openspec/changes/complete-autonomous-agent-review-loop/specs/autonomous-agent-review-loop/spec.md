## ADDED Requirements

### Requirement: Automatic run creates an execution plan for the active task
AGOS SHALL make `agos run auto` create an `ExecutionPlan` for the active task using, in priority order, explicit planner JSON, an enabled configured planner, or a deterministic fallback plan.

#### Scenario: Explicit planner JSON is used
- **WHEN** a user runs `agos run auto --planner-json <json>`
- **THEN** AGOS SHALL validate the supplied JSON as an `ExecutionPlan` for the active task
- **AND** AGOS SHALL report that the plan source was explicit planner JSON

#### Scenario: Enabled planner is used
- **WHEN** `orchestration.planner.enabled` is true and the configured planner returns valid plan JSON
- **THEN** AGOS SHALL use the planner-produced subtasks and worker assignments after validation
- **AND** AGOS SHALL report that the plan source was the configured planner

#### Scenario: Planner unavailable falls back
- **WHEN** no explicit planner JSON is provided and the configured planner is disabled, unavailable, times out, or returns no JSON object
- **THEN** AGOS SHALL create a deterministic fallback plan
- **AND** AGOS SHALL report that the plan source was fallback

### Requirement: Automatic plans assign every subtask to a configured worker
AGOS SHALL ensure every automatic plan subtask has a non-empty `worker.adapter` that resolves to a configured execution worker or the default local fallback worker.

#### Scenario: Planner references unknown worker
- **WHEN** planner output references a worker adapter that is not configured
- **THEN** AGOS SHALL reject the plan
- **AND** AGOS SHALL not start worker execution for that plan

#### Scenario: Multiple configured workers are assigned
- **WHEN** a valid planner plan contains multiple subtasks assigned to different configured workers
- **THEN** AGOS SHALL preserve those worker assignments when preparing workspaces and running subtasks
- **AND** AGOS SHALL include the subtask-to-worker mapping in the automatic run result

### Requirement: Automatic run executes subtasks through worker adapters
AGOS SHALL run automatic plan subtasks through the configured `ExecutionWorkerAdapter` lifecycle and persist run status.

#### Scenario: Worker completes subtask
- **WHEN** a worker adapter reports a subtask as completed
- **THEN** AGOS SHALL include that subtask in the automatic run completed subtasks
- **AND** AGOS SHALL be able to export a candidate patch from the prepared worker workspace

#### Scenario: Worker fails or times out
- **WHEN** a worker adapter reports failure or exceeds the configured timeout
- **THEN** AGOS SHALL record the failed subtask
- **AND** AGOS SHALL not create an accepted candidate for that failed subtask

### Requirement: Automatic run validates candidate patches before review
AGOS SHALL create candidate patches only from completed subtasks and SHALL run patch-apply checks plus locked candidate gates before review.

#### Scenario: Candidate tests pass
- **WHEN** a completed subtask exports an in-scope patch and all locked candidate gates pass
- **THEN** AGOS SHALL mark the candidate as tested
- **AND** AGOS SHALL allow the candidate to proceed to review

#### Scenario: Candidate tests fail
- **WHEN** a candidate patch does not apply or any locked candidate gate fails
- **THEN** AGOS SHALL not run automatic reviewer acceptance for that candidate
- **AND** AGOS SHALL report the candidate as blocked by tests or gates

### Requirement: Automatic run starts configured local agent review before acceptance
AGOS SHALL run configured reviewer adapters for each tested candidate before accepting it, unless the user explicitly enables the missing-review override.

#### Scenario: Configured local reviewer succeeds
- **WHEN** at least one configured reviewer exists and all required reviewers complete successfully with no open blocking findings
- **THEN** AGOS SHALL ingest the candidate review report
- **AND** AGOS SHALL allow candidate acceptance
- **AND** AGOS SHALL report which reviewer IDs ran

#### Scenario: Required reviewer fails
- **WHEN** any required configured reviewer fails
- **THEN** AGOS SHALL mark the candidate review binding as failed
- **AND** AGOS SHALL not accept the candidate

#### Scenario: Blocking finding is returned
- **WHEN** a configured reviewer returns an open blocking finding
- **THEN** AGOS SHALL not accept the candidate
- **AND** AGOS SHALL report that review blocked the candidate

#### Scenario: No reviewer configured by default
- **WHEN** no reviewers are configured and the user has not passed `--allow-missing-review`
- **THEN** AGOS SHALL not accept the candidate
- **AND** AGOS SHALL report that candidate review is required

### Requirement: Missing review override remains explicit and non-production
AGOS SHALL accept a candidate without configured reviewers only when the user explicitly passes the missing-review override.

#### Scenario: Missing review override is enabled
- **WHEN** no reviewers are configured and the user passes `--allow-missing-review`
- **THEN** AGOS SHALL create an explicit clean candidate review record
- **AND** AGOS SHALL record that missing review was explicitly allowed

### Requirement: Automatic apply remains explicitly gated
AGOS SHALL not mutate governed source files during `agos run auto` unless the user passes `--apply`.

#### Scenario: Dry run accepts but does not apply
- **WHEN** a candidate passes tests and review during `agos run auto --dry-run`
- **THEN** AGOS SHALL record the accepted candidate
- **AND** AGOS SHALL not apply the candidate patch to the governed repository

#### Scenario: Apply flag mutates through guarded apply
- **WHEN** a candidate passes tests and review during `agos run auto --apply`
- **THEN** AGOS SHALL use the existing guarded candidate apply path
- **AND** AGOS SHALL apply only candidates that pass patch hash, write-scope, test, review, dirty-path, and patch-apply guards

### Requirement: Automatic loop readiness diagnostics are available
AGOS SHALL expose diagnostics that help users determine whether autonomous planning, worker execution, and local agent review are configured.

#### Scenario: Reviewer configuration is missing
- **WHEN** a repository has workers configured but no reviewers configured
- **THEN** `agos doctor` or equivalent diagnostics SHALL report that automatic acceptance will be blocked unless `--allow-missing-review` is used

#### Scenario: Planner command is unavailable
- **WHEN** planner support is enabled but the configured planner command is unavailable
- **THEN** diagnostics SHALL report the planner command issue
- **AND** `agos run auto` SHALL still be able to use deterministic fallback unless a future strict planner mode is requested
