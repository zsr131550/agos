# Execution Modes and Offline Operation

AGOS supports two task entry modes while preserving existing CLI and `.agos/`
state. A repository can use both modes: configure a default, then override one
run with `agos start --mode ...` or the Dashboard selector.

## Compatibility defaults

Existing repositories do not need a migration before upgrading.

- A config without `task_execution` resolves to `legacy/legacy` and emits a
  machine-readable compatibility warning in the normalized result.
- An archived task without `execution_mode` or `output_contract` remains a
  legacy task. AGOS does not rewrite old task YAML.
- Human-readable legacy `agos start` output remains the executor issue ID when
  one exists, otherwise the executor run ID.
- `agos run auto` remains available for dry runs and advanced/manual control.

To make the default explicit:

```yaml
task_execution:
  mode: legacy
  output_contract: legacy
```

## Legacy mode

`legacy` creates the task, locks its gates, and dispatches the configured
`ExecutorAdapter` directly. It preserves the v0.1 executor, checkpoint, status,
and output-directory workflow.

```bash
agos start --title "Legacy task" --mode legacy
agos start --title "Legacy task" --mode legacy --json
```

Use this mode for existing Multica flows, integrations that depend on issue IDs,
or standalone deliverables that already write `outputs/<task-id>/`.

## Candidate mode

`candidate` routes the task through the governed candidate pipeline:

1. Create an isolated Git worktree.
2. Run the selected worker.
3. Export and scope-check a non-empty patch.
4. Run patch applicability and locked candidate gates.
5. Run all configured required automatic reviewers.
6. Persist an accepted decision only when tests and review are clean.
7. Apply the accepted patch through guarded apply.
8. Persist one normalized `execution/task-execution.json` result.

```bash
agos start \
  --title "Update public examples" \
  --intent "Change README and its verification" \
  --mode candidate \
  --json
```

`agos start --mode candidate` always requests guarded apply. It has no option to
skip candidate tests, review, decision, or apply guards. Use the retained
advanced command when a dry run is required:

```bash
agos run auto --dry-run --json
agos run auto --apply --json
```

Candidate readiness is checked before publishing a new task. The check validates
the worker/reviewer structure and local executable availability only; it does
not contact a provider.

## Output contracts

`task_execution.output_contract` defines what counts as a business result.

| Contract | Completion requirement |
| --- | --- |
| `legacy` | Preserve the historical `outputs/<task-id>/` requirement. |
| `source_code` | Require a valid governed source change or candidate patch; no output directory is created. |
| `standalone` | Require files below `outputs/<task-id>/`. |

New Codex CLI and Claude Code initialization selects
`candidate/source_code` and configures one required CLI reviewer. An
initialization that cannot provide an automatic reviewer, such as a Multica-only
selection, writes `legacy/legacy` and prints the reason.

## Deterministic offline command worker

The `command` worker runs an explicit argv list in the isolated worktree. AGOS
uses `shell=False`, closes stdin, captures stdout/stderr, and never interprets
the command through a shell.

```yaml
task_execution:
  mode: candidate
  output_contract: source_code

workers:
  offline_edit:
    type: command
    argv:
      - python
      - -c
      - >-
        from pathlib import Path;
        Path("README.md").write_text("# offline\n", encoding="utf-8")
    timeout_seconds: 30

orchestration:
  backend: native_async
  max_parallel: 1
  fallback_write_scope: [README.md]
  planner:
    enabled: false
```

AGOS itself performs no network request in this configuration. The executable
named in `argv` is still trusted code and may use the network on its own; choose
an offline command and environment when network isolation is required.

## Reviewer boundary

Candidate mode requires an automatic reviewer.

- `codex_cli` and `claude_code` reviewers may be offline only when their
  underlying CLI/model configuration is offline. AGOS does not claim or enforce
  that provider boundary.
- `fake` is deterministic and provider-free, but is development-only. It
  requires `allow_fake_reviewer: true`; merge-gate records a dev-only warning.
- `manual` is suitable for offline production approval, but it is not an
  automatic reviewer and therefore does not satisfy `agos start` candidate
  readiness by itself.

A provider-free development/test loop can use:

```yaml
reviewers:
  clean:
    type: fake
    role: code_review
    required: true

allow_fake_reviewer: true
```

Do not use this fake-reviewer configuration as production approval evidence.

## Dashboard and resume behavior

`POST /api/runs` and the Dashboard task form accept optional `mode`. An omitted
value uses the repository default. Responses retain `run_id`, `issue_id`, `run`,
and `current`, and add the normalized `execution_result`.

Legacy resume/restart redispatches its executor as before. Candidate
resume/restart reuses the persisted candidate runtime and never redispatches the
legacy executor. A completed or failed candidate cannot be safely restarted in
place; archive it and create a new task.

## Migration examples

Keep a repository on historical behavior:

```yaml
task_execution:
  mode: legacy
  output_contract: legacy
```

Adopt candidate mode for source changes:

```yaml
task_execution:
  mode: candidate
  output_contract: source_code
```

Adopt corrected standalone output semantics without changing dispatch mode:

```yaml
task_execution:
  mode: legacy
  output_contract: standalone
```

Override only one run without changing the config:

```bash
agos start --title "Compatibility run" --mode legacy
agos start --title "Governed patch run" --mode candidate --json
```
