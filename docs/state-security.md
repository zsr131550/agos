# State Consistency and Local Security

This document describes the state, agent-permission, and Dashboard boundaries
introduced by the Phase 4 stabilization work. Existing task YAML and command
names remain compatible.

## Ledger concurrency and durability

Each hash-chained task ledger uses a sibling lock file such as:

```text
.agos/tasks/current/.ledger.jsonl.lock
```

AGOS holds an exclusive cross-process lock from the fresh tail read through the
JSONL append, flush, and `fsync`. POSIX uses `fcntl.flock`; Windows uses
`msvcrt.locking`. A permanent empty/single-byte lock file is expected and is
not governance evidence. Do not copy it as a substitute for `ledger.jsonl`.

Concurrent CLI and Dashboard processes can still finish cache writes in a
different order. This is safe because `status.json` is only a derived cache.

## Status recovery

Every `load_status()` operation with an active task:

1. Reads the cache if it is valid.
2. Reads and verifies one complete ledger snapshot.
3. Compares the cached `ledger_head_hash` and task ID with the verified head.
4. Replays executor dispatch, checkpoint cursor, gate results, Dashboard
   lifecycle state, and terminal execution state when the cache is stale.
5. Atomically replaces `status.json` with the recovered view.

This recovers a crash after the ledger append but before the cache replace. A
missing or invalid `status.json` is also rebuilt when task and ledger evidence
are available. A hash-chain failure raises `LedgerTamperError`; AGOS does not
overwrite the cache from tampered evidence.

To exercise recovery without changing the ledger:

```bash
rm .agos/tasks/current/status.json
agos status --json
```

Deleting `status.json` is optional and should not be part of normal operation.
Never delete or edit ledger lines to repair a cache.

## Agent permission defaults

Configs that omit permission policy now use safe, non-interactive defaults:

- Codex: `--sandbox workspace-write -c 'approval_policy="never"'`
- Claude Code: `--safe-mode --permission-mode dontAsk`

`workspace-write` lets a worker modify its AGOS-created Git worktree while
denying broader filesystem access according to the provider CLI's sandbox.
`never`/`dontAsk` prevents an unattended run from waiting for interactive
approval; a denied operation is returned to the agent as a failure.

The former bypass behavior remains available only through an explicit
compatibility field:

```yaml
executor:
  name: codex_cli
  agent: codex
  command: codex
  dangerously_bypass_permissions: true

workers:
  legacy_compat:
    type: claude_code
    command: claude
    dangerously_bypass_permissions: true
```

`agos doctor` reports `agent_permissions: warning` and names every configured
Codex/Claude executor or worker with the bypass enabled. The warning does not
change the doctor exit code, preserving existing automation, but the setting is
not a production-safe default.

The compatibility field does not apply to command workers. A command worker is
trusted local code and receives exactly the configured argv/environment.

## Patch scope versus sandboxing

Candidate write scope validates the files represented by exported Git patch
evidence. It does not prevent a trusted executable from changing ignored files,
other repositories, user configuration, processes, or network state.

Provider sandbox flags are the operating-system side-effect boundary for Codex
and Claude. For deterministic offline automation, use a command worker whose
executable is itself network-isolated and limit its environment explicitly.
AGOS does not claim that a clean candidate patch proves the absence of non-Git
side effects.

## Dashboard authentication

Loopback remains the default:

```bash
agos dashboard --host 127.0.0.1 --port 8788 --open
```

When no token is supplied on loopback, AGOS creates an ephemeral token and
injects it only into the no-store Dashboard response. The page sends the token
as a Bearer credential for mutations. Existing browser use remains automatic.

A non-loopback bind fails before opening the socket unless an explicit token is
provided. Generate one locally and pass it through the environment:

```bash
export AGOS_DASHBOARD_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
agos dashboard --host 0.0.0.0 --port 8788 --no-open
```

Open the remote page with the token in the URL fragment:

```text
http://SERVER:8788/#token=TOKEN
```

The fragment is not sent in the HTTP request. The page moves it to
`sessionStorage` and removes it from the visible URL. Explicit remote tokens are
never embedded in served HTML or JSON responses, and request logging is
disabled.

Security rules:

- Every `/api/` POST requires the Bearer token and an `Origin` matching the
  request host.
- Remote-bound `/api/` GET requests also require the token.
- Loopback GET requests remain unauthenticated for compatibility.
- JSON request bodies larger than 64 KiB are rejected before parsing.
- The server is plain HTTP; put an authenticated TLS reverse proxy in front of
  it when traffic leaves a trusted host/network boundary.

## Fully offline candidate verification

The provider-free closed-loop test uses:

- `candidate/source_code` execution;
- a structured-argv command worker;
- planner disabled with deterministic fallback;
- a fake required reviewer with `allow_fake_reviewer: true`;
- real patch, gate, decision, guarded apply, ledger, and merge-gate checks.

The fake reviewer is development-only evidence. Production acceptance needs a
real automatic reviewer or an explicit human workflow. See
[`execution-modes.md`](execution-modes.md) for the full configuration.
