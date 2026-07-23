# ADR 0001: Make the Task Ledger Authoritative for Task State

- Status: Accepted
- Date: 2026-07-22

## Context

Task state was previously updated by several Core, CLI, and Dashboard call
sites. Each call site coordinated a ledger append, an in-memory `Status`
mutation, and a `status.json` write. That distributed protocol made the result
depend on write ordering and duplicated concurrency, projection, and failure
handling rules.

The task ledger is append-only, hash-chained, and durably flushed. It already
contains the governance evidence used by Merge Gate and Trust Anchor checks.
`status.json`, by contrast, exists to make the current view convenient to
consume and can be reconstructed.

Existing tasks create one migration complication: a legacy `status.json` may
contain state written by the old dual-write protocol that its ledger cannot
reconstruct. The new model must preserve that state without continuing to
treat the cache as an authority.

## Decision

### Task State Interface

Task state reads and production writes go through one deep module with two
operations:

```python
TaskState.current() -> TaskSnapshot | None
TaskState.record(
    event: TaskEvent,
    *more: TaskEvent,
    expected: TaskRevision | None = None,
) -> TaskCommit
```

The ledger is the only authoritative source for Task State. `status.json` is a
derived cache and never an independent governance fact.

`current()` verifies the complete ledger and replays every record. It compares
the complete projection with the cache, not only the cached head hash, and
ordinarily atomically replaces a missing, invalid, stale, or divergent cache.
Ledger integrity or Task identity failures fail closed and do not overwrite the
cache.

One migration exception preserves a baseline-eligible legacy cache: it is
parseable, belongs to the active Task, names the verified ledger head, has no
prior `task_state_baselined` record, and contains Status facts pure replay
cannot recover. In that case `current()` returns the pure-ledger snapshot with a
warning but leaves the cache on disk. The cache is only migration input, never a
projector seed; the next successful `record()`, including from another
`TaskState` instance, journals the baseline before the caller event and resumes
normal cache derivation.

The internal event registry owns fact validation, Status projection, and the
revision policy for each event. A new event whose name is not registered is
rejected before any append. An unknown event already present in a historical
ledger remains part of the verified chain, is a projection no-op, and produces
a warning on the returned snapshot.

### Revision Policy

Lifecycle transitions use optimistic concurrency and require the
`TaskRevision` returned by `current()` or the preceding commit. The revision
must still match the verified ledger head before any event is appended. Task
initialization requires `TaskRevision.empty()`. Ordinary facts may be appended
without a revision so concurrent producers do not discard independent facts;
the shared cross-process lock still gives those facts a single ledger order.

### Event Identity and Executor Observations

Every caller-supplied new event must contain the active Task's `task_id`; a
missing or mismatched value is rejected before an append. Historical records
remain replay-compatible when they omit `task_id`, but a present mismatched
value fails closed.

When Status has an active executor run, `checkpoint`, `executor_completed`, and
`executor_blocked` must name that same `run_id`. Observations from an older run
after redispatch raise a conflict and are not appended. A terminal event still
requires the fresh revision required by its transition policy.

### Legacy Baseline

On the first write to a legacy Task, Task State compares the compatible cache
with a projection built only from the verified ledger. If the cache contains
state that pure replay cannot recover, Task State appends one
`task_state_baselined` event before the caller's events. The event captures the
minimum legacy view needed by subsequent replay. Until that write succeeds,
`current()` preserves the baseline-eligible cache on disk even though it returns
only the verified ledger projection; this preserves the migration input across
Task State instances.

After that append, the baseline is a ledger fact and the cache resumes its
strictly derived role. The baseline event is written at most once. Older AGOS
versions can continue to verify its hash-chain record even if they treat its
unknown event type as a projection no-op.

### Unreconciled Dispatches

If a Dashboard-started external executor run cannot be durably reconciled as
`executor_dispatched`, AGOS appends `executor_dispatch_unreconciled` as a
non-projecting audit fact with its Task, adapter, run, trigger, stage, and
evidence reference. It does not claim that Status changed to the new run.
Dashboard exposes the fact and refuses another dispatch until an operator
inspects and reconciles the evidence. This applies to initial starts as well as
redispatches, including evidence-write failures and confirmed or indeterminate
ledger failures; archive is not treated as reconciliation. A future Execution Run stage must provide
the explicit reconcile or cancel operation; this Task State change intentionally
does not guess at external process cancellation.

### Durability and Failure Results

Events are validated as a complete batch before writing, then appended and
`fsync`ed in order. Every append confirmed durable is committed; the JSONL
backend does not pretend to provide an all-or-nothing batch transaction.

After confirmed ledger commits, a cache replacement failure does not turn the
operation into a failed commit. `record()` returns the committed records and
snapshot with `cache_synced=False` and a warning. A later `current()` call can
repair the cache.

If Task State cannot determine whether an attempted append became durable, it
raises `TaskStateCommitIndeterminate`. The caller must obtain a fresh verified
snapshot and establish whether the event is present before deciding to retry.

If a batch stops after a confirmed prefix, Task State raises
`TaskStateBatchInterrupted`. The exception identifies the confirmed records
and the unprocessed events. The confirmed prefix remains authoritative and
must not be rolled back by editing the ledger; recovery starts from a fresh
revision and only reissues events known to be unprocessed.

### Audit Reads and Persistence

Merge Gate, Trust Anchor, and ledger-specific verification retain direct,
verified ledger reads. They are audit consumers and do not participate in Task
State writes.

The implementation continues to use the existing local files, cross-process
lock, append plus `fsync`, and atomic cache replacement directly. There is only
one real persistence adapter, so this decision does not introduce a public
persistence port. A second concrete backend would be evidence for that seam;
the current design does not create a hypothetical one.

## Consequences

- Production callers share one write protocol and one test surface.
- A verified full replay, rather than cached content, determines current state.
- Cache availability can affect freshness and warnings but cannot revoke a
  confirmed ledger commit.
- Lifecycle races are explicit revision conflicts; concurrent facts are
  serialized and retained.
- Batch callers must handle confirmed prefixes and indeterminate commits
  explicitly.
- Reads are `O(n)` in ledger length until a separately justified, trusted
  checkpoint design is introduced.
- The file implementation remains simpler than a premature persistence
  abstraction, at the cost of intentionally supporting only the current local
  backend.

## Rejected Alternatives

### Keep Ledger and Status Co-Authoritative

This preserves the distributed dual-write protocol and leaves crash recovery
unable to determine which file wins. It is incompatible with deterministic
replay and fail-closed integrity checks.

### Seed Normal Replay from Status

Using the cache as a continuing projector seed would hide missing ledger facts
and make deleting `status.json` change Task State. The one-time baseline event
provides migration compatibility without retaining that ambiguity.

### Add a Persistence Port Now

There is no second backend with different constraints. A port today would add
interface surface without concentrating implementation complexity.

### Promise Atomic Batches

The append-only JSONL backend can confirm records one at a time but cannot
roll back durable lines. Reporting the confirmed prefix is more accurate and
recoverable than claiming transaction semantics the storage does not provide.
