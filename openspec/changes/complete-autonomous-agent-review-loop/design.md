## Context

AGOS already contains the core pieces for an autonomous governance loop:

- `execution_planner.create_execution_plan()` can produce an `ExecutionPlan` from explicit planner JSON, an enabled LLM planner, or a deterministic fallback.
- `worker_registry.register_configured_worker_adapters()` wires `local_worktree`, `codex_cli`, `claude_code`, `multica`, and `openhands` worker adapters.
- `ExecutionRuntime` can tick prepared subtasks, poll worker status, retry/timeout, and persist run state.
- `ExecutionService` can prepare workspaces, export candidate patches, run candidate gates, create candidate review packets, ingest review results, decide candidates, and apply accepted candidates.
- `reviewer_registry.configured_reviewer_adapters()` wires manual, fake, Codex CLI, and Claude Code reviewers.
- `execution_pipeline.run_auto_execution()` already composes planning, execution, candidate submission, tests, review, decision, and optional apply.

The gap is product closure. In the current repository configuration, planner support is not enabled, only one Codex worker is configured, and no reviewer is configured. As a result, `agos run auto` works as an execution pipeline, but it does not reliably demonstrate the intended “自主拆分任务 → 多子代理分配 → 本地 agent review → accepted candidate” loop without manual configuration knowledge.

This design turns the current implementation seams into an explicit autonomous-loop capability with configuration validation, richer run evidence, clearer CLI output, and tests that prove the full path.

## Goals / Non-Goals

**Goals:**

- Make `agos run auto` a reliable entrypoint for autonomous task decomposition, worker/subagent assignment, candidate validation, local agent review, candidate decision, and optional guarded apply.
- Provide a documented `.agos/agos.yaml` configuration pattern for planner-enabled multi-worker execution and local LLM CLI review.
- Preserve dry-run as the default safe mode; source mutation remains explicit via `--apply`.
- Add diagnostics so users can see whether AGOS used an LLM planner or deterministic fallback, which workers were assigned, which reviewers ran, and which stage blocked acceptance.
- Add tests for the complete local loop using deterministic fake adapters, plus opt-in real CLI smoke guidance for Codex/Claude/Multica/OpenHands.

**Non-Goals:**

- Do not make real Codex, Claude, Multica, or OpenHands calls mandatory in normal test runs.
- Do not introduce new required runtime dependencies.
- Do not replace the existing manual `agos candidate ...` and `agos review ...` workflows.
- Do not auto-apply candidate patches unless the user explicitly passes `--apply`.
- Do not solve hosted/multi-user orchestration or Dashboard action authorization in this change.

## Decisions

### Decision 1: Keep `agos run auto` as the product entrypoint

`agos run auto` already composes the relevant services, and the v1 hardening design names it as the automatic flow. We will harden and document this path rather than adding another command.

Alternative considered: create a new `agos autonomous run` command. Rejected because it would duplicate pipeline semantics and increase CLI surface area before the existing command is complete.

### Decision 2: Treat planner output as optional but observable

Planner-backed decomposition remains opt-in through `orchestration.planner.enabled`. If the planner is disabled or unavailable, AGOS MUST fall back to a deterministic plan, but the result MUST report the planner mode used.

Alternative considered: require an LLM planner for autonomous mode. Rejected because AGOS must remain usable offline and in CI without external agent credentials.

### Decision 3: Require configured reviewers for automatic acceptance by default

`run_auto_execution()` already blocks candidate acceptance when no reviewers are configured unless `--allow-missing-review` is set. This behavior should remain the default. The implementation will make the block easier to diagnose and document the reviewer configuration needed for a true local-agent-review loop.

Alternative considered: auto-create a clean empty review when no reviewers exist. Rejected except under the explicit `--allow-missing-review` escape hatch, because it weakens the governance claim.

### Decision 4: Make worker assignment explicit in artifacts

Each generated or accepted `ExecutionPlan` subtask already names `worker.adapter`. The autonomous loop should surface this in CLI output, run result JSON, and ledger/evidence summaries so users can verify that subagent allocation happened.

Alternative considered: infer workers only from logs. Rejected because logs are harder to validate and do not provide a stable contract for tests or UI consumers.

### Decision 5: Use deterministic tests for default CI, opt-in smoke for real agents

The core loop will be tested with fake/local adapters to keep default tests stable. Real Codex/Claude/Multica/OpenHands smoke tests remain opt-in but should be documented as release validation.

Alternative considered: make real agent smoke tests required. Rejected because they require local credentials, installed tools, daemons, or endpoints that are not universally available.

## Proposed Flow

```text
active task
  |
  v
create_execution_plan()
  |-- planner enabled + valid JSON --> multi-subtask plan
  |-- planner unavailable ----------> deterministic fallback plan
  v
ExecutionService.execute_plan_model()
  |-- validate task id / workers / scopes
  |-- worker.prepare() creates isolated workspaces
  v
ExecutionRuntime.tick()
  |-- worker.start()
  |-- worker.poll()
  |-- retry / timeout / persist status
  v
submit_candidate()
  |-- worker.export_candidate()
  |-- patch hash + write-scope validation
  v
test_candidate()
  |-- patch applies
  |-- locked gates
  v
run_candidate_review()
  |-- configured local Codex/Claude/manual/fake reviewers
  |-- ParallelReviewOrchestrator
  |-- ReviewReport + raw reviewer refs
  v
decide_candidate()
  |-- accept only if tests passed and review is current/clean
  v
apply_candidate() only when --apply
```

## Configuration Shape

The implementation should document and validate a configuration similar to:

```yaml
workers:
  codex_impl:
    type: codex_cli
    command: codex.cmd
    timeout_seconds: 900
    artifact_globs:
      - .agos-worker/*.json
  claude_docs:
    type: claude_code
    command: claude
    timeout_seconds: 900

reviewers:
  codex_review:
    type: codex_cli
    executor: codex_cli
    role: security_reviewer
    required: true
    command: codex.cmd

orchestration:
  backend: native_async
  max_parallel: 2
  max_retries: 1
  worker_timeout_seconds: 900
  fallback_write_scope:
    - README.md
    - src/agos
    - tests
    - docs
  planner:
    enabled: true
    executor: codex_cli
    command: codex.cmd
    timeout_seconds: 60
```

## Implementation Approach

1. Extend `AutoExecutionResult` with stage observability:
   - planner mode: `llm`, `planner_json`, or `fallback`;
   - subtask IDs and worker assignments;
   - reviewer IDs that ran;
   - blocked stage and reason when no candidate is accepted.
2. Make `create_execution_plan()` return or expose plan provenance without weakening fallback behavior.
3. Add `doctor` checks for autonomous-loop readiness:
   - planner enabled but command unavailable;
   - no workers configured;
   - no reviewers configured;
   - configured reviewer command unavailable;
   - `max_parallel` greater than available worker capacity is allowed but warned.
4. Add README and config examples for:
   - minimal local fallback;
   - planner-enabled Codex worker plus Codex reviewer;
   - multi-worker Codex/Claude split;
   - opt-in real integration smoke tests.
5. Keep `--allow-missing-review` explicit and clearly marked as non-production.

## Risks / Trade-offs

- **Risk: LLM planner returns invalid or over-broad plans** → Validate with `ExecutionPlan`, reject unknown workers, enforce write-scope rules, and fall back only for unavailable/non-JSON planner output.
- **Risk: Users think dry-run changed files** → Keep `dry_run=true` in JSON, improve human output, and report that apply requires `--apply`.
- **Risk: Local LLM reviewers can be expensive or slow** → Require explicit reviewer configuration, use configured timeouts, and preserve manual/fake reviewer paths for development.
- **Risk: Multiple workers create conflicting candidates** → Preserve existing candidate/bundle arbitration and guarded apply checks.
- **Risk: CI cannot run real agent smoke tests** → Keep deterministic tests in default CI and document opt-in smoke variables.

## Migration Plan

1. Add tests that describe the desired autonomous loop behavior.
2. Implement provenance/result fields in a backward-compatible way.
3. Add diagnostics and docs.
4. Verify existing manual flows still pass.
5. Roll back by disabling planner/reviewer configuration or using existing manual `agos candidate` commands; no data migration is required.

## Open Questions

- Should `agos init` offer an interactive preset for “autonomous local Codex loop” when `codex` is detected?
- Should `agos run auto` gain a `--require-planner` flag for users who prefer failure over deterministic fallback?
- Should Dashboard expose this loop as a protected dry-run action after the Dashboard action-safety work is complete?
