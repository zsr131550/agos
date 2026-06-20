# AGOS

Executor-agnostic governance layer for AI coding agents. *Agent writes. AGOS verifies. CI enforces.*

v0.1 ships the local advisory gate + hash-chained ledger + evidence plumbing. See `docs/superpowers/specs/2026-06-21-agos-multica-executor-backend-design.md` for the full design.

## Install (dev)

```bash
pip install -e ".[dev]"
```

## Commands (v0.1)

```
agos init [--executor multica] [--agent "Lambda"]
agos start --title "..." [--intent "..."] [--workflow feature] [--gate tests_pass,...]
agos checkpoint [--follow] [--once]
agos ci --local --stage <pre-commit|pre-push>
```
