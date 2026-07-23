# State Consistency and Local Security

This document describes the state, agent-permission, and Dashboard boundaries
introduced by the Phase 4 stabilization work. Existing task YAML and command
names remain compatible.

## Ledger authority, concurrency, and durability

Each hash-chained task ledger uses a sibling lock file such as:

```text
.agos/tasks/current/.ledger.jsonl.lock
```

The Task Ledger is the only authoritative source for Task State. `status.json`
is a derived cache and is not governance evidence.

AGOS holds an exclusive cross-process lock while `TaskState.current()` verifies
and replays the ledger. `TaskState.record()` extends the same serialization
across full-ledger verification, revision checks, ordered JSONL appends, flush
and `fsync`, Status projection, and the cache replacement. POSIX uses
`fcntl.flock`; Windows uses `msvcrt.locking`. A permanent empty/single-byte lock
file is expected and is not governance evidence. Do not copy it as a substitute
for `ledger.jsonl`.

All production Task State writers, including CLI and Dashboard operations, use
`TaskState.record()`. Concurrent ordinary facts are retained in one ledger
order. Lifecycle transitions use the optimistic revision rules described
below.

Merge Gate, Trust Anchor, and ledger-specific verification continue to read and
verify the Ledger directly. These are audit reads, not alternate write paths.

See
[`ADR 0001`](adr/0001-ledger-authoritative-task-state.md)
for the complete decision and trade-offs.

## Task State reads and Status recovery

For an active Task, `TaskState.current()`:

1. Acquires the Task State cross-process lock.
2. Reads the Task and verifies the complete ledger, including JSON records,
   sequence numbers, hash links, and Task identity.
3. Replays every registered event from the Task's initial state, including any
   one-time legacy baseline.
4. Preserves a historical unknown event as a projection no-op and adds a
   warning to the returned `TaskSnapshot`.
5. Compares the complete projected Status with `status.json`, not only its Task
   ID and `ledger_head_hash`.
6. Atomically replaces a missing, invalid, stale, or divergent cache, except
   for a baseline-eligible legacy cache retained until its first successful
   Task State write.

This recovers a crash after the ledger append but before the cache replace. A
missing or invalid `status.json` is also rebuilt when task and ledger evidence
are available. A cache replacement failure leaves the in-memory snapshot
usable and reports a non-fatal warning; the next read can attempt the repair
again. A hash-chain failure raises `LedgerTamperError`; Task identity failures
also fail closed. AGOS does not overwrite the cache from invalid evidence.

A baseline-eligible cache is intentionally retained during the one-time
migration window. `current()` still returns the verified ledger projection and
a warning; it does not make the cache authoritative. Do not delete or overwrite
that cache while the warning is present. A successful `record()` writes
`task_state_baselined` before its requested event, after which normal cache
repair applies.

`load_status()` remains a compatibility adapter over `TaskState.current()` and
returns the projected `Status`. `save_status()` remains available to legacy
callers and test fixtures, but production state changes must not use it.

Run the following only when no baseline-migration warning is present; it is not
a way to migrate legacy-only cache facts. To exercise recovery without changing
the ledger:

```bash
rm .agos/tasks/current/status.json
agos status --json
```

Deleting `status.json` is optional and should not be part of normal operation.
Never delete or edit ledger lines to repair a cache.

## Task State writes and recovery

`TaskState.record()` accepts one or more immutable `TaskEvent` values. Before
writing, it validates the complete batch against the internal event registry.
A new, unknown event name or invalid event facts are rejected without an
append. Historical unknown events are tolerated only during replay and remain
visible through snapshot warnings.

Revision policy depends on the event kind:

- Task initialization must provide `expected=TaskRevision.empty()`.
- A lifecycle transition must provide the `TaskRevision` from a fresh snapshot
  or preceding commit. A missing or stale revision raises a conflict before
  any append.
- An ordinary fact may omit `expected`; concurrent facts are serialized and
  all remain in the Ledger.

Every caller-supplied new event must contain the active Task's `task_id`; a
missing or mismatched value is rejected before append. Historical ledger records
remain replay-compatible when they omit `task_id`, but a present mismatched
value fails closed. When Status has an active executor run, `checkpoint`,
`executor_completed`, and `executor_blocked` must name that active `run_id`.
An observation from a replaced run is rejected rather than appended; terminal
events still need their normal fresh transition revision.

When the first new event is recorded for a legacy Task whose compatible cache
contains state that pure ledger replay cannot reproduce, Task State first
appends one `task_state_baselined` event. The baseline makes that legacy state
durable and replayable. Subsequent reads and writes do not use the cache as a
projection seed.

If Dashboard starts an external executor but cannot durably reconcile it as
`executor_dispatched`, it records `executor_dispatch_unreconciled` as a
non-projecting audit fact. The fact contains the Task, adapter, run, trigger,
stage, and evidence reference and never claims that Status changed to that run.
Dashboard exposes unresolved facts and blocks another dispatch until an operator
can inspect the evidence. This covers initial starts and redispatches, including
evidence-write failures, confirmed ledger-write failures, and indeterminate
ledger commits; archive is also blocked because it is not reconciliation or
external-run cancellation. Dashboard lifecycle handlers also use a process-local
lock for start, archive, and restore; this reduces same-process races, while the
Task State cross-process lock remains the correctness mechanism across
processes.

Use the result or exception type to recover correctly:

- A normal `TaskCommit` identifies the committed records, latest snapshot, and
  revision. If `cache_synced=False`, the Ledger commit still succeeded. Do not
  reissue the event to repair the cache; call `current()` and let it rebuild
  `status.json`.
- `TaskStateCommitIndeterminate` means AGOS could not prove whether the last
  append became durable. Do not retry blindly. Obtain a fresh verified
  snapshot, inspect the Ledger for the intended event, and then decide whether
  a follow-up write is needed.
- `TaskStateBatchInterrupted` identifies a confirmed committed prefix and the
  events that were not processed. Keep the confirmed records, obtain a fresh
  snapshot, and retry only the unprocessed events. Any transition retry must
  use the new current revision.

Never truncate, delete, or rewrite confirmed Ledger records as recovery. Batch
writes deliberately expose prefix-commit semantics because the append-only
JSONL backend cannot provide rollback after `fsync`.

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
