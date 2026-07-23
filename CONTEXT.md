# AGOS Governance

AGOS governs agent-driven work from execution through evidence, review, and merge decisions. This glossary names the lifecycle concepts shared by the CLI, Dashboard, and core modules.

## Language

**Task**:
A governed unit of work with an intended workflow, required gates, and selected Agents.
_Avoid_: Job, ticket

**Ledger**:
The chronological record of governance facts for one Task and the source of truth for that Task's state.
_Avoid_: Log, cache

**Status**:
The current derived view of a Task's state. Status is a convenience view, never an independent source of governance facts.
_Avoid_: State file, source of truth

**Task State**:
The lifecycle state of one Task as established by its verified Ledger and exposed through Status. During the one-time migration window, a compatible legacy cache is only baseline input: it is not authoritative and never seeds normal projection.
_Avoid_: Dashboard state, cached state

**Task Event**:
A governance fact recorded in a Task's Ledger. A new Task Event must carry the active Task's `task_id`; it may change Status or exist only as audit evidence.
_Avoid_: Log message, Status mutation

**Executor Run**:
The active external execution identified by its run ID. Executor observations must name the current run; an unreconciled dispatch is an audit fact that does not alter Status and blocks another Dashboard dispatch pending operator review.
_Avoid_: Background process, inferred state
