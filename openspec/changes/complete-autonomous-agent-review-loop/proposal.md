## Why

AGOS already has most of the building blocks for automatic planning, worker execution, candidate validation, and reviewer orchestration, but the default repository setup does not yet deliver a dependable end-to-end autonomous loop. Users still need to know which planner, workers, and local review agents to configure before `agos run auto` behaves like “自主拆分任务 → 分配子代理 → 启动本地 agent review → 产出可治理候选补丁”.

This change closes that gap by turning the existing seams into an explicit, documented, configurable, and verifiable autonomous execution-and-review loop.

## What Changes

- Add a first-class autonomous agent review loop capability that defines the expected behavior of `agos run auto`.
- Provide a guided configuration path for enabling planner-backed task splitting, multi-worker/subagent assignment, and local LLM CLI reviewers.
- Harden the automatic pipeline so it reports whether it used an LLM plan or deterministic fallback, which workers were assigned, which reviewer agents ran, and why candidates were accepted, blocked, or skipped.
- Add validation and doctor diagnostics for incomplete autonomous-loop configuration.
- Add focused tests for:
  - planner-enabled multi-subtask plans;
  - multi-worker assignment;
  - local Codex/Claude reviewer dispatch;
  - missing-reviewer blocking behavior;
  - dry-run/apply separation;
  - evidence and ledger records for each loop stage.
- Preserve current manual execution, candidate, review, and apply flows.

## Capabilities

### New Capabilities
- `autonomous-agent-review-loop`: Defines the end-to-end automatic loop from active task planning through worker/subagent execution, candidate creation, local agent review, candidate decision, and optional guarded apply.

### Modified Capabilities
- None. `openspec/specs/` currently has no existing capability specs to modify.

## Impact

- Affected code areas:
  - `src/agos/core/execution_planner.py`
  - `src/agos/core/execution_pipeline.py`
  - `src/agos/core/execution_service.py`
  - `src/agos/cli/cmd_execute_plan.py`
  - `src/agos/cli/worker_registry.py`
  - `src/agos/cli/reviewer_registry.py`
  - `src/agos/cli/cmd_doctor.py`
  - `src/agos/core/config.py`
- Affected user-facing commands:
  - `agos run auto`
  - `agos run auto --dry-run`
  - `agos run auto --apply`
  - `agos doctor`
  - `agos config validate`
- Affected docs/config:
  - `.agos/agos.yaml` examples for planner, workers, reviewers, and orchestration policy.
  - README autonomous workflow section.
- No new required runtime dependencies. Real Codex/Claude/Multica/OpenHands integration remains opt-in for smoke tests and local operation.
