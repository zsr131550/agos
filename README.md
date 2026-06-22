# AGOS

Executor-agnostic governance layer for AI coding agents. *Agent writes. AGOS verifies. CI enforces.*

v0.1 ships the local advisory gate + hash-chained ledger + evidence plumbing. See `docs/superpowers/specs/2026-06-21-agos-multica-executor-backend-design.md` for the full design.

## Install (dev)

```bash
pip install -e ".[dev]"
```

## Commands (v0.3 execution)

```
agos init [--executor multica] [--agent "Lambda"]
agos start --title "..." [--intent "..."] [--workflow feature] [--gate tests_pass,...]
agos checkpoint [--follow] [--once]
agos review --packet-only
agos review --ingest findings.json --review-id review-...
agos execute-plan --plan execution-plan.yaml
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
agos task status
agos task clear --force
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

- The agent runs in multica's isolated workspace (`~/multica_workspaces/<per-task>/`), not in your repo.
- `agos checkpoint` streams the agent's reported activity into an evidence ledger and records a governed-repo anchor (HEAD + status) at capture time. It does not claim the agent edited your working tree.
- `agos ci --local` gates a human developer's commit/push only (advisory and bypassable with `--no-verify`). The agent's own commits never pass through these hooks. Agent output is gated server-side at the merge gate, which lands in v0.2.
- Gate commands may use shell-style `command: "pytest -q"` for compatibility or structured `argv: ["pytest", "-q"]` for cross-platform execution without a shell. New configs prefer `argv`.
- `agos task status` prints the active task cache, and `agos task clear --force` clears a stale `.agos/tasks/current` directory after manual review.

## Trust model (v0.1 limitation)

The ledger hash chain is tamper-evident, not tamper-proof. It detects accidental edits and naive agents that edit a record without recomputing its hash. A determined agent that rewrites the whole ledger and recomputes every hash is not defended against in v0.1; a real out-of-band trust anchor lands in v0.2.
