# AGOS v0.1 — Design

**status:** Draft (pending user review)
**Date:** 2026-06-21
**Author:** AGOS project

## context

This design implements AGOS — an executor-agnostic governance layer for ai coding agents — with [`multica`](https://github.com/zsr131550/multica) as the first executor adapter backend
The governing design reference is `E:\agos_github_module_mapping.md` ("the mapping doc"). Relevant background:

- AGOS is a **governance** layer, not another coding agent. Thesis: *Agent writes. AGOS verifies. CI enforces.*
- multica is a Managed Agents platform. Its **local daemon** does **not** run the agent CLI in the caller's working tree — it creates an **isolated per-task directory** under `MULTICA_WORKSPACES_ROOT` (default `~/multica_workspaces/`) and spawns the agent CLI there. There is no `--workdir` / `--repo` flag on `multica issue create`; an issue is a server-side record, not a filesystem binding. multica is the **executor**, not the governance layer
- This has a first-order consequence AGOS must not hide: the agent's commits happen inside the daemon's isolated workspace, **not** in the governed repo's working tree. Therefore the governed repo's local `pre-commit`/`pre-push` hooks **never fire on the agent's work**. Local git hooks can only gate a *human developer's* commits in the governed repo. Enforcement that covers the agent's output must happen **server-side, at the merge/PR gate** — where the agent's diff re-enters the governed repo
- Per the mapping doc, AGOS v0.1 = lightweight, local, file-system-first: Typer + Pydantic + YAML/JSON + jsonl ledger + sha256 hash chain + pre-commit hook. No database
- v0.1 scope (locked in brainstorming): `init`, `start`, `checkpoint`, `ci --local`. `review`/`resolve`/`closeout` deferred to v0.2. A server-side merge-gate command is **out of v0.1 scope**; v0.1 ships the local gate + ledger + evidence plumbing that the future merge-gate will verify

## locked decisions

1. **Lifecycle owner** — AGOS owns task/workflow/gate/checkpoint/proof lifecycle in its own schema + jsonl ledger. multica is one executor adapter: AGOS dispatches work to it and ingests its execution runs as evidence. AGOS never adopts multica's issue as source of truth
2. **Enforcement is two-tier, and v0.1 ships only the weaker tier** — (a) *Local advisory tier*: git hooks (`pre-commit`/`pre-push`) in the **governed repo** gate a **human developer's** commits. (b) *Server-side authoritative tier*: the agent's output re-enters the governed repo as a PR/diff; a merge-gate CI job verifies ledger integrity + gate states + evidence→diff binding and blocks merge. **Only tier (b) is non-bypassable.** Because multica runs the agent in an isolated daemon workspace (`~/multica_workspaces/`), tier (a) **cannot** gate the agent's own commits — they never touch the governed repo's hooks. v0.1 implements tier (a) and the ledger/gate/evidence plumbing that tier (b) will verify; the merge-gate command itself is v0.2. `agos ci --local` is explicitly a **development-time feedback** tool, not a merge gate
3. **v0.1 scope** — core loop only: `init`, `start`, `checkpoint`, `ci --local`. Review/resolve/closeout deferred to v0.2
4. **multica interface** — define `ExecutorAdapter` interface now; the multica adapter is the first impl, backed by shelling out to the installed `multica` CLI (`--output json`). No HTTP, no auth-token management in AGOS
5. **Checkpoint model** (Approach A) — AGOS polls `multica issue run-messages <task> --since N` and appends streamed messages as evidence. Incremental checkpoints with real captured agent activity (tool calls, file edits, errors), not single-shot
6. **Data store** — `.agos/` at repo root, single active task (`.agos/tasks/current/`) for v0.1. ledger + evidence are co-located with the code they govern and committable
7. **Gate evaluator + sha256 hash chain verification** — `agos ci --local` re-derives the chain from line 1 and compares to stored hashes. **Scope of this guarantee:** it detects *accidental or naïve* tampering (hand-edits, partial rewrites, a record that forgot to recompute its hash). It does **not** provably resist the governed agent itself: `status.json` and `ledger.jsonl` live in the same tree the agent can write, so a determined agent could rewrite the whole ledger and recompute a clean chain. A real trust anchor (out-of-band signed head hash, or server-side independent recompute) lands in v0.2 — see *Trust anchor (v0.1 limitation)*
8. **Two trees, one ledger** — the multica daemon works in its own isolated workspace; the governed repo is a different tree. AGOS never assumes they are the same. Checkpoint evidence binds the *multica run's reported activity* to a *governed-repo HEAD at capture time*; it does not claim the agent edited the governed-repo working tree. Gate evaluation operates on the governed-repo working tree (for a human developer's staged diff) — it is not asserted over the agent's isolated workspace

## architecture

```
        ┌─────────────────────────────────────────────────────────────┐
        │                         AGOS CLI (Typer)                       │
        │   agos init · start · checkpoint · ci --local                 │
        └──────────┬───────────────────────────────────────────┬────────┘
                   │                                           │
         ┌─────────▼────────┐                       ┌───────────▼──────────┐
         │ Governance Core  │                       │  ExecutorAdapter     │
         │ (executor-       │                       │   (interface)        │
         │  agnostic)       │                       │   ┌───────────────┐  │
         │                  │                       │   │ MulticaAdapter│  │
         │ · task/workflow  │                       │   │ (shells out   │  │
         │ · gate engine    │                       │   │  to `multica`)│  │
         │ · JSONL ledger   │                       │   └───────┬───────┘  │
         │ · hash chain     │                       └───────────┼──────────┘
         │ · evidence store │                                   │ subprocess / JSON
         └────────┬─────────┘                                   │
                  │ reads/writes                                │ dispatch + poll only
         ┌────────▼─────────┐                  ┌────────────────▼────────────────┐
         │  .agos/ (files)   │                  │ multica daemon                    │
         │  ledger.jsonl     │◄──evidence────── │ issue create → run → run-messages │
         │  task.yaml        │   (agent         │ → spawns agent CLI in ISOLATED    │
         │  evidence/*       │   activity,     │ per-task dir under ~/multica_     │
         │                   │   NOT this tree)│ workspaces/  ← agent's commits     │
         └───────────────────┘                  │ happen HERE, not in governed repo │
                  ▲                            └────────────────┬────────────────────┘
                  │                                               │ agent's diff re-enters
         ┌────────┴─────────┐                                     │ governed repo as PR
         │ GOVERNED repo    │◄────────────────────────────────────┘
         │ local git hooks  │   tier (a) advisory: gates a HUMAN dev's commit
         │ (pre-commit/     │   (agent's own commits never touch these hooks —
         │  pre-push)       │    they happen in the daemon workspace)
         └──────────────────┘
                  │
                  │ tier (b) authoritative: server-side merge-gate CI (v0.2)
                  │ verifies ledger + gates + evidence→diff binding, blocks merge
                  ▼
         ┌──────────────────┐
         │  PR merge gate    │
         └──────────────────┘
```

### Layer responsibilities

| Layer | Owns | Knows about multica? |
|---|---|---|
| **Governance Core** | task/workflow/gate state, JSONL ledger + hash chain, evidence store, gate evaluation | No — only `ExecutorAdapter` + `Event` |
| **ExecutorAdapter** | the seam: `start` / `stream_events` / `status` | No — defines the contract |
| **MulticaAdapter** | translating AGOS task → multica CLI calls + multica messages → Event | Yes — only v0.1 impl |

### Key invariants

> The Governance Core never imports multica. It only ever sees the `ExecutorAdapter` interface and `Event` objects. Adding a second executor (bare codex CLI) later requires zero core changes: a second `CodexCliAdapter` implementing the same 3 methods and emitting the same `Event` types. Gates, ledger, evidence store — all untouched when a second adapter lands.
>
> The governed repo and the executor's workspace are **two different trees**. The multica daemon works in `~/multica_workspaces/<per-task>/`; AGOS's ledger and gates live in the governed repo. Evidence binds the agent's *reported activity* to a *governed-repo HEAD*, never claiming the agent edited the governed working tree directly. Enforcement over the agent's output is server-side (merge gate), not local hooks

## data model

`.agos/` at repo root, single active task for v0.1:

```
<repo>/
├── .agos/
│   ├── agos.yaml              # repo-level config: default workflow, gates, executor
│   ├── repo_ledger.jsonl      # repo-level events (init, hook install) — plain log (see §ledger)
│   ├── tasks/
│   │   └── current/           # the single active task (v0.1)
│   │       ├── task.yaml      # task def: title, intent, acceptance, workflow, gates
│   │       ├── status.json    # current phase + gate states + executor binding
│   │       ├── ledger.jsonl   # append-only, hash-chained task event log
│   │       └── evidence/      # captured artifacts
│   │           ├── runs/      #   <multica_task_id>.json   (run metadata)
│   │           ├── messages/  #   <multica_task_id>.jsonl  (streamed run-messages)
│   │           ├── agent_diff/#   diff/patch the agent REPORTED (executor-side tree)
│   │           ├── repo_anchor/# governed-repo HEAD + status at capture time (anchor, NOT the agent's tree)
│   │           └── gates/     #   <gate_id>-<ts>.log       (gate command stdout/stderr)
│   └── hooks/                 # agos-generated hook scripts (copied to .git/hooks)
├── .git/hooks/
│   ├── pre-commit             # runs `agos ci --local --stage pre-commit`  (human dev only)
│   └── pre-push               # runs `agos ci --local --stage pre-push`          (human dev only)
```

Two ledgers: `repo_ledger.jsonl` (repo-level setup events like `init`) and per-task `tasks/current/ledger.jsonl` (task lifecycle, checkpoints, gate evaluations). `init` writes to the repo ledger; `start`/`checkpoint`/`ci` write to the task ledger. The task ledger is the one gates verify.

**Two trees, made explicit in the evidence layout.** The agent runs in multica's isolated workspace; the governed repo is a separate tree. The evidence store keeps them distinct:
- `agent_diff/` — what the *executor reported* (multica run activity / any diff the agent produced). This is executor-side evidence, captured via the adapter, never via `git` in the governed repo.
- `repo_anchor/` — the governed repo's `git rev-parse HEAD` + `git status` at capture time. This is an **anchor** (a point-in-time snapshot of the repo the ledger lives in), **not** a claim that the agent edited this tree.

Checkpoint evidence binds *agent activity* ↔ *repo anchor*, so a future merge-gate can check "does the PR diff match the activity the ledger recorded?" It deliberately does **not** assert "the agent edited the governed working tree."

### Three file types, three roles

**`task.yaml`** — human-readable task definition (Pydantic-validated). Written at `agos start`, rarely changes:

```yaml
id: agos-01J...                # ULID
title: "Add login rate limiting"
intent: "Brute-force protection on /login"
acceptance:
  - "5 failed attempts → 15min lockout"
  - "tests cover lockout + unlock"
workflow: feature              # resolves gate set from agos.yaml
gates: [tests_pass, no_secrets_in_diff]  # resolved set is frozen to ledger at `start` (gates_locked)
executor:
  adapter: multica
  agent: "Lambda"              # multica agent name to assign to
```

**`status.json`** — machine-readable live state. The gate (git hook) reads this for pass/block state. Rewritten on every checkpoint (derived from ledger, not independent source of truth):

```json
{
  "task_id": "agos-01J...",
  "phase": "executing",
  "executor_run": { "adapter": "multica", "run_id": "5fb87ac7...", "issue_id": "MUL-42" },
  "gates": {
    "tests_pass":         { "state": "unknown", "last_evaluated": null },
    "no_secrets_in_diff": { "state": "unknown", "last_evaluated": null }
  },
  "ledger_head_hash": "ab12ef..."
}
```

**`ledger.jsonl`** — append-only hash chain. One line per event, linked via `prev_hash` (empty string `""`. First record's `prev_hash` field is `""` for clarity — the hash input is `""`, see §hash chain). It is the tamper-**evident** (not tamper-**proof** against a determined agent — see *Trust anchor*); `status.json` is just a cache of it:

```json
{"seq":1,"ts":"2026-06-21T...","type":"task_started","task_id":"agos-01J...","prev_hash":"","hash":"..."}
{"seq":2,"ts":"...","type":"gates_locked","gates":["tests_pass","no_secrets_in_diff"],"prev_hash":"...","hash":"..."}
{"seq":3,"ts":"...","type":"executor_dispatched","run_id":"5fb87ac7...","issue_id":"MUL-42","prev_hash":"...","hash":"..."}
{"seq":4,"ts":"...","type":"checkpoint","evidence_refs":["messages/5fb87ac7.jsonl"],"repo_head":"abc1234","prev_hash":"...","hash":"..."}
{"seq":5,"ts":"...","type":"gate_evaluated","gate":"tests_pass","state":"pass","prev_hash":"...","hash":"..."}
```

Hash chain: `hash_n = sha256(prev_hash || canonical_json(record_n without hash))`, first record's `prev_hash` = `""`. This makes the chain **tamper-evident** — a hand-edit or a record whose hash wasn't recomputed is detected on re-verify. It is **not** tamper-proof against a determined agent that rewrites the whole ledger and recomputes every hash; see *Trust anchor (v0.1 limitation)*. Note `gates_locked` (seq 2): the resolved gate set is frozen at task start, so a gate cannot be silently removed after dispatch to pass `ci`.

## commands (v0.1 core loop)

### `agos init`

```
agos init [--executor multica] [--agent "Lambda"]
```

- Creates `.agos/` structure; writes `agos.yaml` with default workflow (`feature`) and default gates
- **Validates the executor**: for multica, runs `multica daemon status` + `multica workspace list --output json` to confirm the daemon is reachable and a workspace is selected. If unreachable, warn (user may set up later) but continue — `init` must not hard-fail. Note: the multica CLI exposes `daemon start/status` and `workspace list/switch`, not `auth status`/`agent list`
- Installs git hooks (`pre-commit`, `pre-push`) into `.git/hooks/` — small shims that call `agos ci --local --stage <stage>`. These gate **a human developer's** commits in the governed repo only; they never fire on the agent's work (which happens in multica's isolated workspace — see *Two trees*). Preserves existing hooks and chains to them
- Logs a `repo_initialized` event to the **repo ledger** (plain JSONL, not hash-chained — see *repo_ledger.jsonl*)

### `agos start`

```
agos start --title "..." [--intent "..."] [--workflow feature] [--gate tests_pass,...]
```

- Aborts if `.agos/tasks/current/` is non-empty (single active task in v0.1)
- Builds `task.yaml` (Pydantic-validated). Resolves gates from `agos.yaml` workflow→gates map (overridable by `--gate`)
- **Writes a `gates_locked` ledger record** with the resolved gate set (canonical JSON of each gate's id+stage+command/type), hash-chained. The gate set is now frozen for this task — see *Gates are locked*
- Calls `ExecutorAdapter.start(task)` → MulticaAdapter runs `multica issue create --title ... --description ... --assignee "Lambda" --output json`, returns `{issue_id, run_id}`. **No `--workdir`/`--repo` flag exists** — the daemon picks the agent's working directory, not AGOS
- Logs `executor_dispatched` to the ledger; sets phase `executing` in `status.json`
- Prints the issue URL so the user can watch the agent in multica UI. The agent runs in `~/multica_workspaces/<per-task>/`, not in the governed repo

### `agos checkpoint`

```
agos checkpoint [--follow] [--once]
```

- Reads `status.json`'s `executor_run.run_id`
- `MulticaAdapter.stream_events(run_id)`: polls `multica issue run-messages <run_id> --since <last_seq> --output json`. Each new message becomes an `Event` (kind: tool_call / file_edit / text / error / run_complete)
- For each event batch: append to `evidence/messages/<run_id>.jsonl`, then add a `checkpoint` ledger line (batched — one checkpoint = one ledger line with `evidence_ref` + `repo_head`)
- In `--follow`: poll-loop 3s until `run_complete` or Ctrl-C. In `--once`: poll once, write one checkpoint, exit
- Recomputes `status.json` after each checkpoint batch
- **Also captures a repo anchor**: `git rev-parse HEAD` + `git status` of the **governed repo** saved to `evidence/repo_anchor/`. This anchors the checkpoint to a concrete governed-repo state at capture time — it is **not** a claim that the agent edited this tree (the agent works in multica's isolated workspace). Any executor-reported diff/patch lands separately in `evidence/agent_diff/`

### `agos ci --local`

```
agos ci --local --stage <pre-commit|pre-push>
```

- This is **the local gate — advisory tier only**. Called by git hooks on a **human developer's** commit/push, so its exit code controls *that* commit. It does **not** gate the agent's work: the agent commits inside multica's isolated workspace and never passes through these hooks. Agent output is gated server-side at the merge gate (v0.2)
- Reads `status.json`. If no active task → exit 0 (ungoverned commit passes through)
- Evaluates all gates in `task.yaml` that apply to `<stage>`, against the **governed repo's** working-tree diff:
  - `tests_pass`: runs the configured test command in the governed working tree; captures stdout/stderr as evidence; pass/fail
  - `no_secrets_in_diff`: scans `git diff --cached` (pre-commit) or `git diff origin..HEAD` (pre-push) — i.e. the **human developer's** staged diff — for secret patterns
- Each gate evaluation gets a `gate_evaluated` ledger record
- **Exit code**: all gates pass (or no gates) → 0. Any gate blocks → non-zero, prints human-readable reason to stderr, hook blocks the commit/push
- Updates `status.json` gate state to pass/block after each evaluation
- **Trust-anchor caveat**: before trusting `status.json`, `ci` re-verifies the ledger hash chain (see *Trust anchor*). A broken chain = hard error. Note this detects accidental/naïve tampering only — see the limitation section

### The loop

```
init ─▶ start ─▶ checkpoint --follow ─▶ (agent works in multica workspace, evidence streams) ─▶ ci --local
                                                          │                    │
                                          ledger + evidence grow        gates a HUMAN dev's commit
                                          until run_complete             (advisory); agent's own output
                                                                         gated at merge (v0.2)
```

The agent works inside multica's isolated workspace; AGOS streams its reported activity into the ledger. `agos ci --local` gates a *human developer's* commits in the governed repo (advisory, bypassable with `--no-verify`). The agent's output is verified server-side at the merge gate — which in v0.1 is not yet built, so v0.1's enforcement over agent work is **preparation, not enforcement**: the ledger and evidence exist so a future merge-gate can verify them.

## ExecutorAdapter interface & MulticaAdapter

### `ExecutorAdapter` interface (executor-agnostic, in Governance Core)

```python
class ExecutorAdapter(Protocol):
    name: str

    def start(self, task: Task) -> ExecutorRun:
        """Dispatch the task. Returns a handle to poll for events."""
        ...

    def stream_events(self, run_id: str, since: str | None) -> Iterator[Event]:
        """Yield events since the given cursor (seq/timestamp). Blocks/loops as needed."""
        ...

    def status(self, run_id: str) -> RunStatus:
        """Current high-level status: running | completed | failed | blocked."""
        ...
```

Two stable value objects the core sees:

```python
@dataclass
class ExecutorRun:
    adapter: str          # "multica"
    run_id: str           # the executor's native task/run id (multica issue.id)
    issue_id: str | None   # human-readable issue identifier, e.g. "MUL-42" (multica issue.identifier)

@dataclass
class Event:
    seq: int              # monotonic, from executor
    ts: str               # RFC3339
    kind: Literal["tool_call","file_edit","text","error","run_complete"]
    content: str          # raw message text / tool descriptor
    raw: dict             # full original payload, for evidence fidelity
```

The core never sees multica's schema. It sees `ExecutorRun` and `Event`. That's the seam.

### `MulticaAdapter` — the concrete impl

Shells out to the installed `multica` CLI, all `--output json`. **multica offers no working-directory binding**: `issue create` makes a server-side issue; the daemon spawns the agent CLI in its own isolated per-task directory under `MULTICA_WORKSPACES_ROOT` (default `~/multica_workspaces/`). AGOS therefore **cannot** direct the agent at the governed repo's working tree. The adapter's job is dispatch + poll; evidence of *what the agent did* comes from `run-messages`, not from the governed repo's git.

| Interface method | multica CLI call | Translation |
|---|---|---|
| `start(task)` | `multica issue create --title <task.title> --description <task.intent+acceptance> --assignee <task.executor.agent> --output json` | parse JSON → `issue.id` + `issue.identifier`. Use `issue.id` as `run_id`. **No `--workdir`/`--repo` flag exists**; the agent's filesystem location is chosen by the daemon, not AGOS |
| `stream_events(run_id, since)` | `multica issue run-messages <run_id> --since <seq> --output json` (3s loop); if only a short task-id prefix is known, pass `--issue <issue_id>` | each message entry → `Event`. Map `kind` from message structure (tool_call/text/error). Emit `run_complete` when a terminal-status message is present |
| `status(run_id)` | `multica issue runs <issue_id> --output json` (list runs for the issue) or `multica issue get <issue_id>` | map run/issue status (in_progress→running, done→completed, blocked→blocked) |

### Subprocess details

- `multica` resolves workspace via its profile system (`multica workspace list` / `switch`). AGOS uses the profile's current workspace; it does not manage workspace selection — it respects multica's resolution order
- The agent executes in the daemon's isolated per-task directory under `MULTICA_WORKSPACES_ROOT`. AGOS does **not** read or write that directory; it only observes the run via `run-messages`. This is why executor-side evidence (agent activity) is fundamentally separate from governed-repo evidence (repo anchor)
- `--since <seq>` is `run-messages`' incremental cursor. The adapter persists the last-seen `seq` in `status.json` so checkpointing is resumable
- `run-messages` accepts a short task-id prefix only with `--issue <issue_id>`; the adapter always carries the full `issue_id` (stored as `run_id`) to avoid the ambiguity
- CLI exit codes convey failure class (network=2, auth=3, not-found=4, validation=5). The adapter maps 4 (run not found) to `RunStatus(failed)` and 2/3 to a retryable exception — not a silent empty stream

### Why subprocess over HTTP

The multica CLI documents a CLI flow. multica's auth, profiles, and workspace resolution are all built into the CLI. Bypassing it to hit multica's HTTP API directly means AGOS re-implements auth + workspace headers + pagination cursors and chases API drift — exactly the coupling the mapping doc §5 says AGOS should avoid. The CLI is multica's stable, published contract

### What AGOS deliberately cannot do with multica

Because the daemon owns the agent's working directory and exposes no binding flag, AGOS **cannot** (a) point the agent at the governed repo, (b) run the governed repo's hooks on the agent's commits, or (c) capture the agent's diff via the governed repo's `git`. This is the structural reason enforcement over agent output is server-side (merge gate), not local. v0.1 builds the ledger + evidence that a merge gate verifies; it does not pretend to locally gate the agent

## gate engine, hash chain, error handling

### Gate engine (executor-agnostic)

Gates are plain Python callables, declared in `agos.yaml` and selected by `task.yaml`:

```yaml
# .agos/agos.yaml
workflows:
  feature:
    gates:
      - id: tests_pass
        stage: [pre-commit, pre-push]
        command: "pytest -q"
      - id: no_secrets_in_diff
        stage: [pre-commit, pre-push]
        type: secret_scan
      - id: build_clean
        stage: [pre-push]            # cheaper gate only on push
        command: "npm run build"
  docs_only:
    gates: []                        # no gates
```

```python
class Gate(Protocol):
    id: str
    def evaluate(self, ctx: GateContext) -> GateResult: ...

@dataclass
class GateResult:
    state: Literal["pass", "block"]
    reason: str
    evidence_path: str | None   # stdout/stderr saved as evidence
```

Two built-in gate types: **`command`** (run a shell command, exit 0 = pass, capture stdout/stderr to `evidence/gates/`) and **`secret_scan`** (regex scan of diff; trufflehog integration deferred to v0.3). `GateContext` carries repo path, stage, and the diff to scan. Gates cannot reach multica — they see only the governed working tree and the human developer's diff

### Gates are locked into the ledger at `start`

The gate set is **configuration** (`agos.yaml` workflow→gates + `task.yaml` override), and configuration is editable by anyone with write access — including the governed agent. To prevent "delete the failing gate, commit, pass," `start` writes a `gates_locked` ledger record containing the **resolved gate set** (canonical JSON of every gate id + stage + command/type). `agos ci --local` re-reads `task.yaml`/`agos.yaml`, re-resolves the gate set, and compares to the `gates_locked` record on the chain. A mismatch (a gate was added/removed/changed after `start`) = hard block, exit non-zero. Changing gates requires a new task (new `start`), not an in-flight edit.

### Ledger hash chain

```
hash_1 = sha256( "" || canonical_json(record_1_without_hash) )      # first record
hash_n = sha256( hash_{n-1} || canonical_json(record_n_without_hash) )   # n > 1
```

- `canonical_json`: sorted keys, no whitespace, deterministic. The first record's `prev_hash` field is the **empty string `""`** (not `null`) so the `prev_hash || canonical_json(...)` concatenation is defined for every record — no special-cased first-record hash function, no null-stringification ambiguity across implementations
- On every append: build record, compute hash, append line. Store `ledger_head_hash` in `status.json`
- `agos ci --local` **re-verifies the chain** before trusting `status.json` (recompute from line 1, compare to stored hashes). A broken chain = hard error, exit non-zero. This detects *accidental or naïve* tampering (a hand-edit, a partial rewrite, a record whose hash wasn't recomputed). It is **not** proof against a determined agent that rewrites the whole ledger and recomputes a clean chain — see *Trust anchor*
- Repo-anchor binding: each `checkpoint` records the governed repo's `HEAD` at capture time. If a later checkpoint's recorded `HEAD` is not an ancestor of the current `HEAD` (history was rewritten under the task), `ci` logs `repo_history_drift` and blocks. This catches a rewritten branch, not the agent's isolated workspace (which AGOS never inspects via git)

### Trust anchor (v0.1 limitation)

The hash chain is tamper-**evident**, not tamper-**proof** against the governed agent. Both `status.json` and `ledger.jsonl` live in `.agos/`, inside the same tree the agent (or a developer) can write. A determined agent can rewrite the entire ledger and recompute a valid-looking chain; `ledger_head_hash` is stored in the same tree, so the comparison baseline is itself mutable. v0.1's chain therefore defends against:

- accidental edits, merges gone wrong, partial rewrites
- a *naïve* agent that edits a record without recomputing its hash

It does **not** defend against:

- a *sophisticated* agent that rewrites the ledger end-to-end and recomputes every hash
- deletion of the whole `.agos/` and a fresh "clean" task

Real tamper-resistance requires a trust anchor outside the governed tree. Two exits, both deferred to v0.2+:

1. **Out-of-band signed head** — after each checkpoint, the head hash is written to a location the agent cannot write (signed commit to a protected ref, an external append-only log, a remote attestation service). `ci` verifies the signed head matches the local chain.
2. **Server-side independent recompute** — the merge-gate CI clones the PR's `.agos/`, recomputes the chain from scratch, and compares to a head hash published out-of-band at task start. The agent cannot forge this because it runs in CI, not in the agent's workspace.

v0.1 ships the chain mechanics; the anchor that makes them meaningful is v0.2. The spec states this explicitly so v0.1 is not mistaken for "provable."

### repo_ledger.jsonl

The repo-level ledger records setup events (`init`, hook install). Unlike the task ledger, it is **not** hash-chained in v0.1 and **not** verified by `ci` — there is nothing for `ci` to gate against at repo-setup time (no task is active). It is a plain append-only JSONL log. If a verified repo ledger becomes useful later (e.g. auditing hook installation), `agos verify --repo` can be added; until then the hash field is omitted to avoid implying a guarantee that doesn't run. The task ledger is the only chain `ci` verifies.

### Error handling — three failure classes

| Class | Example | Behavior |
|---|---|---|
| **Executor reachability** | multica CLI missing, not authed, daemon down | `start`/`checkpoint` abort with executor-class error message. Gate evaluation (purely local) is unaffected — local gates still block a human dev's commit even if multica is offline |
| **Polling failure** | network timeout (exit 2) mid-`run-messages` | Adapter retries up to 3× with exponential backoff (2s→4s→8s, cap 30s). After retries exhausted: log `poll_error`, keep the last cursor, lose no evidence, print warning to stderr, continue loop (`--follow`) or return partial checkpoint (`--once`) |
| **Gate failure** | tests fail, secret found in diff | non-zero exit, the human dev's commit blocked. The failure itself is ledgered + hashed — nothing lost. Next commit attempt re-evaluates |

**One deliberate non-feature**: AGOS never silently swallows an executor error into a "looks-passing" gate. If `tests_pass` can't run (e.g. the test command itself errors), that's a block, not a pass

## testing

### Unit tests (pytest)

| What | Where | Strategy |
|---|---|---|
| Hash chain | `tests/core/test_ledger.py` | append N records, verify chain re-computes (first record `prev_hash=""`); tamper one record, verify `verify_chain()` raises; **rewrite-all-and-recompute** fixture: a full rewrite + clean recompute does **not** raise (documents the v0.1 trust-anchor limitation, see *Trust anchor*) |
| Gate evaluation | `tests/core/test_gate.py` | `command` gate pass/fail; `secret_scan` finds/clears a fake diff; **gates_locked** — resolved gate set matches locked value (`task.yaml`/`agos.yaml`); assert a gate silently removed after dispatch is detected (block) |
| MulticaAdapter | `tests/adapters/test_multica.py` | **fake the `multica` CLI** via a stub binary / monkeypatched subprocess runner; assert `start` parses issue JSON (no `--workdir` flag passed), `stream_events` maps messages→Event (incl. `--issue` when using short id), exit-code classes map correctly |
| Task/status models | `tests/core/test_task.py` | Pydantic validation of `task.yaml` / `status.json` round-trips |
| Config | `tests/core/test_config.py` | `agos.yaml` workflow→gates resolution, default-merge behavior |

### The seam test

A `tests/core/test_executor_seam.py` that defines a **fake adapter** (not multica) implementing `ExecutorAdapter`, feeds it canned `Event`s, and asserts the Governance Core produces correct ledger/checkpoint/gate behavior from those events alone. This is the test that proves executor-agnosticism: if it passes with a fake adapter, the core truly depends only on the interface

### Integration test (opt-in)

`tests/integration/test_round_trip.py` — skipped unless `AGOS_INTEGRATION=1` and a real `multica` daemon + agent are reachable. Runs `init → start → checkpoint --once → ci --local` end-to-end against a throwaway repo. Not run in CI by default; documented as a manual smoke test

### v0.1 acceptance criteria (from mapping doc §7, adapted to v0.1 scope)

1. A user need becomes an AGOS task (`start` writes `task.yaml`)
2. Each agent execution dispatch yields a checkpoint (`checkpoint` writes a ledger line)
3. Each checkpoint has executor activity (run-messages), a governed-repo anchor (HEAD + status), and a hash. It does **not** claim the agent edited the governed working tree (the agent runs in multica's isolated workspace)
4. Gate evaluation has pass/block outcome, ledgered; the resolved gate set is `gates_locked` at `start` and re-checked at `ci`
5. `agos ci --local` blocks a **human developer's** commit/push with non-zero exit when a gate blocks. It does **not** gate the agent's own commits (those happen in multica's workspace and bypass local hooks); agent output is gated server-side at the merge gate (v0.2)
6. Ledger hash chain verifies; **accidental or naïve** tampering is detected and blocks. A determined agent rewriting the whole ledger is **not** defended against in v0.1 — see *Trust anchor*
7. A second adapter can be added without touching core (seam test)

**Out of v0.1 scope** (deferred, not claimed): server-side merge-gate command, out-of-band signed trust anchor, review/resolve/closeout, proof.md/proof.json. The full *provable governance* proposition lands in v0.2 (signed anchor + server-side enforcement); v0.1 is the local gate + ledger + evidence foundation

## project layout

```
agos/
├── pyproject.toml
├── src/agos/
│   ├── cli/
│   │   ├── main.py            # Typer app
│   │   ├── config.py
│   │   └── cmd_{init,start,checkpoint,ci}.py
│   ├── core/
│   │   ├── adapter.py         # ExecutorAdapter, ExecutorRun, Event
│   │   ├── task.py            # Task, TaskConfig (pydantic)
│   │   ├── config.py          # AGOSConfig, workflow→gates
│   │   ├── ledger.py          # Ledger, hash chain, verify
│   │   ├── gate.py            # Gate, command + secret_scan impls
│   │   ├── evidence.py        # evidence store writer
│   │   ├── status.py          # status.json read/write (derived from ledger)
│   │   └── repo.py            # .agos/ paths, git helpers
│   ├── adapters/
│   │   └── multica.py         # MulticaAdapter (shells out to `multica`)
│   └── hooks/
│       └── templates/         # pre-commit / pre-push shim scripts
├── tests/
│   ├── core/
│   ├── adapters/
│   └── integration/
└── docs/
    └── superpowers/specs/2026-06-21-agos-multica-executor-backend-design.md
```

Dependency direction: `cli → core + adapters`. `core` and `adapters` are independent except `adapters` implements `core.adapter`. The mapping doc's executor-agnosticism is enforced structurally: `core` cannot import `adapters`.

## roadmap

| Version | Adds |
|---|---|
| **v0.1** (this design) | `init` / `start` / `checkpoint` / `ci --local` (advisory local gate), multica adapter, hash-chain ledger (tamper-**evident** only — no trust anchor yet), `gates_locked`, command + secret_scan gates, git hooks (gate **human dev** commits only) |
| **v0.2** | **server-side merge-gate CI command** (verifies ledger + gates + evidence→diff binding, blocks merge — first non-bypassable enforcement over agent output) · **out-of-band signed trust anchor** (head hash written off-governed-tree) · `review` (prompt generation + ingest, finding schema) · `resolve` (evidence-backed finding close) · `closeout` (`proof.md` + `proof.json`) |
| **v0.3** | second executor adapter (bare `codex` CLI) to prove the seam · OPA policy backend · trufflehog/semgrep security gates · `act` local-CI backend |
| **v1.0** | executor-agnostic governance protocol — stable task/workflow/gate/checkpoint/review/proof schema, multiple adapters, **CI-enforced via server-side merge gate + signed ledger** |

## open questions (none blocking v0.1)

- Whether `checkpoint --follow` should also emit a desktop notification on `run_complete` (nice-to-have, defer)
- Whether the secret_scan gate ships a built-in pattern set or requires config in v0.1 (decision: small built-in set + config override)
