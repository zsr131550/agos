# AGOS

Executor-agnostic governance layer for AI coding agents. *Agent writes. AGOS verifies. CI enforces.*

The current CLI ships local advisory gates, hash-chained task ledgers, candidate
execution, trust anchors, and a CI-oriented merge gate. See
`docs/superpowers/specs/2026-06-24-agos-v1-hardening-design.md` for the current
production-hardening design.

## Install

From a release wheel:

```bash
pip install agos-0.1.0-py3-none-any.whl
```

For local development:

```bash
pip install -e ".[dev]"
```

Optional LangGraph support:

```bash
pip install -e ".[langgraph]"
```

## Commands (current CLI)

```
agos init [--executor multica] [--agent "Lambda"]
agos doctor [--json]
agos config show [--json]
agos config validate [--json]
agos status [--json]
agos start --title "..." [--intent "..."] [--workflow feature] [--gate tests_pass,...]
agos checkpoint [--follow] [--once]
agos anchor publish [--backend file|git-ref] [--path anchor.json] --issuer <issuer>
agos anchor verify [--backend file|git-ref] [--path anchor.json] [--json]
agos review --packet-only
agos review --ingest findings.json --review-id review-...
agos run --plan execution-plan.yaml
agos run auto [--dry-run] [--apply] [--json]
agos run start --plan execution-plan.yaml [--json]
agos run status <run-id> [--json]
agos run resume <run-id> [--json]
agos run cancel <run-id> [--json]
agos candidate list
agos candidate submit <subtask-id> [--summary "..."]
agos candidate test <candidate-id> [--gate tests_pass]
agos candidate review <candidate-id> [--packet-only]
agos candidate review <candidate-id> --ingest findings.json --review-id review-...
agos candidate decide <candidate-id> --decision accepted|rejected|superseded|needs-changes --reason "..."
agos candidate apply <candidate-id>
agos resolve <finding-id> --status resolved --evidence <ref> --rationale "..."
agos closeout
agos ci --local --stage <pre-commit|pre-push>
agos merge-gate [--require-anchor] [--anchor-backend git-ref|file] [--anchor-path anchor.json] [--base <base> --head <head>] [--json]
agos task status
agos task clear --force
agos worker doctor [--worker <name>] [--json]
```

`agos execute-plan ...` remains available as the compatibility name for `agos run ...`.
`agos run run ...` is also accepted as a compatibility alias for `agos run start ...`.

## Quickstart Loops

Check a repository before doing work:

```bash
agos doctor
agos config validate
agos status
```

Run the local governance loop:

```bash
agos init --agent "Lambda"
agos start --title "Implement feature"
agos checkpoint --once
agos ci --local --stage pre-commit
agos closeout
```

Run the multi-agent execution loop:

```bash
agos run start --plan execution-plan.yaml --json
agos run status <run-id> --json
agos candidate list
agos candidate merge preview
```

Run the autonomous execution loop:

```bash
agos run auto --dry-run --json
agos run auto --apply --json
```

`--dry-run` prepares and evaluates AGOS execution artifacts without applying
candidate patches to the governed working tree. `--apply` uses the same guarded
candidate apply path as the manual flow.

Run the CI merge gate:

```bash
agos anchor verify --backend git-ref --json
agos merge-gate --require-anchor --anchor-backend git-ref --base origin/main --head HEAD --json
```

## The v0.1 loop

```
init -> start -> checkpoint --once -> ci --local
```

The v0.2 review loop adds evidence-backed review findings:
`review --packet-only -> external or human review -> review --ingest -> resolve -> closeout`.
Blocking findings prevent closeout until they are resolved with evidence or explicitly accepted by a human.

The v0.3 execution loop adds isolated candidate worktrees and guarded apply:
`execute-plan -> candidate submit -> candidate test -> candidate review -> candidate decide -> candidate apply`.

The v0.4 orchestration seam keeps the same AGOS run graph portable across
backends:
`native_async` is the semantic reference backend, `external` serializes the
normalized run for a remote orchestrator, and `langgraph` can compile the same
DAG when the optional LangGraph dependency is installed.

### Production Orchestration Config

```yaml
workers:
  codex:
    type: codex_cli
    command: codex
    timeout_seconds: 120
    poll_interval_seconds: 2
    artifact_globs:
      - .agos-worker/*.json
  multica:
    type: multica
    command: multica
    agent: Lambda
    timeout_seconds: 120
  openhands:
    type: openhands
    endpoint: http://openhands.local
    token: ${OPENHANDS_TOKEN}
    timeout_seconds: 120

reviewers:
  security:
    type: manual
    role: security_reviewer
    required: true

orchestration:
  backend: native_async
  max_parallel: 2
  max_retries: 1
  worker_timeout_seconds: 900
  retry_backoff_seconds: 5

trust_anchor:
  backend: git-ref
  auto_publish_on_checkpoint: true
  issuer: ci
```

Runtime commands can be read by humans or tools:

```bash
agos run start --plan plan.json --json
agos run auto --dry-run --json
agos run status <run-id> --json
agos run resume <run-id> --json
agos run cancel <run-id> --json
```

### Trust Anchors And Merge Gate

`agos anchor publish` records the expected ledger head outside the task ledger.
The file backend is for local development and tests and requires `--path`;
protected Git refs or a trusted CI publisher should be used for real
enforcement.

When `trust_anchor.auto_publish_on_checkpoint` is enabled, `agos checkpoint`
publishes the latest verified ledger head after each checkpoint. File anchor
paths in `trust_anchor.path` are resolved relative to the governed repository;
omitting the path uses `.agos/tasks/current/evidence/anchors.json`.

`agos merge-gate` is the server-side verifier. It checks the task ledger,
`gates_locked`, optional trust anchor, candidate patch hashes, test evidence,
review evidence, mergeability state, and submitted-diff binding when `--base`
and `--head` are provided. Local hooks are still useful feedback, but CI is the
enforcement point.

### Security Gates

The default `feature` workflow stays lightweight. Production security scans can
be enabled with typed gates such as `opa`, `semgrep`, `trufflehog`, and `codeql`.
See `docs/security-gates.md` for examples and scanner boundary guidance.

### Protected CI Merge Gate

The repository CI adds a dedicated `merge-gate` job for branch protection. Use
`docs/security-gates.md` for the required status-check shape and
`docs/release-install.md` for release and install paths.

### Merge Strategies

| Strategy | Automatic Apply | Meaning |
|---|---:|---|
| `single_candidate` | Yes | One accepted candidate passes all guards. |
| `non_overlapping_bundle` | Yes | Multiple accepted candidates touch disjoint paths. |
| `ordered_patch_stack` | Yes, after stack dry-run | Multiple accepted candidates have explicit order and apply cleanly in a temporary stack workspace. |
| `manual_merge_required` | No | Dirty paths, conflicts, missing review/test evidence, or ambiguous ordering require human action. |

### External Orchestrator Backend

AGOS sends a versioned orchestration payload to a remote backend with an
idempotency key equal to the AGOS run id:

```json
{
  "schema_version": "agos.orchestration.v1",
  "idempotency_key": "execution-run-01",
  "spec": {
    "run_id": "execution-run-01",
    "task_id": "agos-01",
    "backend": "external",
    "nodes": []
  }
}
```

Required remote endpoints:

- `POST /runs`
- `GET /runs/{run_id}`
- `POST /runs/{run_id}/cancel`
- `GET /runs/{run_id}/artifacts`

- The agent runs in multica's isolated workspace (`~/multica_workspaces/<per-task>/`), not in your repo.
- `agos checkpoint` streams the agent's reported activity into an evidence ledger and records a governed-repo anchor (HEAD + status) at capture time. It does not claim the agent edited your working tree.
- `agos ci --local` gates a human developer's commit/push only (advisory and bypassable with `--no-verify`). The agent's own commits never pass through these hooks. Agent output is gated server-side by `agos merge-gate`.
- Gate commands may use shell-style `command: "pytest -q"` for compatibility or structured `argv: ["pytest", "-q"]` for cross-platform execution without a shell. New configs prefer `argv`.
- `agos run auto` falls back to a conservative write scope covering `README.md`, `src/agos`, `tests`, and `docs` when an external planner is unavailable. Directory entries allow child paths without opening the entire repository.
- `agos task status` prints the active task cache, and `agos task clear --force` clears a stale `.agos/tasks/current` directory after manual review.

## Trust model

The ledger hash chain is tamper-evident. A trust anchor makes it CI-verifiable by
recording the expected ledger head outside the mutable task ledger. For local
development, the file anchor backend is convenient but not a security boundary.
For production, publish anchors from trusted automation or a protected Git ref
and require them in `agos merge-gate --anchor-backend git-ref`.

