## 1. Baseline And Test Coverage

- [x] 1.1 Add focused tests for `create_execution_plan()` reporting whether it used explicit planner JSON, configured LLM planner output, or deterministic fallback.
- [x] 1.2 Add tests proving a planner-produced multi-subtask plan preserves each subtask's configured `worker.adapter`.
- [x] 1.3 Add tests proving unknown planner worker adapters are rejected before any worker starts.
- [x] 1.4 Add tests proving `agos run auto` JSON output includes planner source, subtask IDs, worker assignments, reviewer IDs, accepted candidate IDs, and blocked-stage notes.
- [x] 1.5 Add tests proving no configured reviewer blocks automatic acceptance by default.
- [x] 1.6 Add tests proving `--allow-missing-review` creates an explicit clean candidate review record and marks the override in notes/evidence.
- [x] 1.7 Add tests proving `--dry-run` never applies patches and `--apply` uses the existing guarded apply path.

## 2. Planner Provenance

- [x] 2.1 Extend the planning layer with a small result/provenance model that carries `ExecutionPlan` plus plan source (`planner_json`, `llm`, or `fallback`).
- [x] 2.2 Preserve the current `create_execution_plan()` behavior or add a compatibility wrapper so existing callers continue to receive an `ExecutionPlan`.
- [x] 2.3 Route `run_auto_execution()` through the provenance-aware planner path.
- [x] 2.4 Ensure invalid structured planner output still fails instead of silently falling back.
- [x] 2.5 Ensure unavailable planner command, timeout, non-zero exit, or no JSON object still falls back deterministically.

## 3. Automatic Run Result Observability

- [x] 3.1 Extend `AutoExecutionResult` with planner source, subtask-to-worker assignments, reviewer IDs, candidate-to-review mapping, and blocked-stage notes.
- [x] 3.2 Update human output for `agos run auto` to show planner source, worker assignments, reviewer status, accepted candidates, applied candidates, and blocking reasons.
- [x] 3.3 Update JSON output tests and snapshots to cover the new fields.
- [x] 3.4 Keep new result fields backward-compatible by adding optional/default values rather than removing existing fields.

## 4. Worker/Subagent Assignment Closure

- [x] 4.1 Verify `ExecutionService.execute_plan_model()` records each subtask's configured worker adapter in persisted subtask metadata.
- [x] 4.2 Ensure `ExecutionRuntime` result aggregation includes completed and failed subtasks in a form usable by `run_auto_execution()`.
- [x] 4.3 Add a deterministic fake multi-worker test where two subtasks are assigned to different workers and produce separate candidate patches.
- [x] 4.4 Confirm worker readiness checks still run before native execution starts.
- [x] 4.5 Document the supported worker adapter types and the minimum config required for each.

## 5. Local Agent Review Closure

- [x] 5.1 Verify `run_auto_execution()` passes configured reviewer adapters/specs into `ExecutionService.run_candidate_review()` for every tested candidate.
- [x] 5.2 Add a deterministic configured LLM-style reviewer test that proves a local agent reviewer is started and polled through `ParallelReviewOrchestrator`.
- [x] 5.3 Ensure review raw refs are carried into the candidate review binding and result observability fields.
- [x] 5.4 Ensure required reviewer failure marks the candidate review binding failed and blocks acceptance.
- [x] 5.5 Ensure open blocking findings block acceptance and appear in run notes.
- [x] 5.6 Ensure no-reviewer mode remains blocked unless `--allow-missing-review` is explicitly set.

## 6. Diagnostics And Configuration Guidance

- [x] 6.1 Add `agos doctor` readiness checks for autonomous loop configuration: planner command, worker availability, reviewer availability, and missing reviewer blocking behavior.
- [x] 6.2 Add `agos config validate` or existing config validation coverage for planner/reviewer/worker combinations used by the autonomous loop.
- [x] 6.3 Add README examples for minimal fallback, planner-enabled Codex worker, multi-worker Codex/Claude, and local Codex/Claude reviewer configurations.
- [x] 6.4 Document that real Codex/Claude/Multica/OpenHands smoke tests are opt-in and list the required environment variables.
- [x] 6.5 Document that `--allow-missing-review` is a non-production escape hatch.

## 7. End-To-End Verification

- [x] 7.1 Run focused tests: `python -m pytest tests/core/test_execution_planner.py tests/core/test_execution_pipeline.py tests/cli/test_run_auto.py tests/cli/test_reviewer_registry.py tests/core/test_parallel_review_orchestrator.py tests/core/test_execution_service.py -q`.
- [x] 7.2 Run full unit suite: `python -m pytest -q`.
- [x] 7.3 Run lint: `python -m ruff check src tests`.
- [x] 7.4 Run compile check: `python -m compileall -q src tests`.
- [x] 7.5 Optionally run real planner smoke with `AGOS_PLANNER_SMOKE=1` (documented; verified default opt-in test skips when env is unset).
- [x] 7.6 Optionally run real reviewer smoke with `AGOS_REVIEWER_SMOKE=1` (documented; verified default opt-in test skips when env is unset).
- [x] 7.7 Optionally run real worker smokes with `AGOS_CODEX_WORKER_SMOKE=1`, `AGOS_MULTICA_WORKER_SMOKE=1`, or `AGOS_OPENHANDS_WORKER_SMOKE=1` (documented; verified default opt-in tests skip when env is unset).
