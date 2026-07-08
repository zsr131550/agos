## Context

AGOS invokes local CLI agents from Python without a shell. On Windows, npm-installed tools such as `codex` are commonly resolved through `.cmd` shims. Multi-line prompts passed as argv through those shims can be truncated at the first line, and prompts passed via `input=` require the shim runner to create a stdin pipe explicitly.

The real automatic loop needs three Codex roles to work with the user's local configuration:

1. planner creates an execution plan,
2. worker edits an isolated worktree and exports a candidate patch,
3. reviewer reads the candidate patch and returns normalized JSON findings.

The observed failure was not planner capability; it was prompt delivery to worker/reviewer.

## Goals / Non-Goals

**Goals:**

- Preserve direct local Codex CLI usage rather than switching to an API.
- Deliver full multi-line worker and reviewer prompts reliably on Windows.
- Keep local Codex config/rules enabled by default.
- Add focused regression tests for the prompt transport path.
- Prove the fix with a real `agos run auto --dry-run --json` run using Codex planner, worker, and reviewer.

**Non-Goals:**

- Do not redesign planner JSON generation.
- Do not add a new orchestration backend.
- Do not force `--ignore-user-config` or `--ignore-rules`.
- Do not apply the generated candidate to `main` as part of the smoke run.

## Decisions

### Decision 1: Use stdin for Codex worker and reviewer prompts

Codex CLI documents that `codex exec -` reads initial instructions from stdin. Using stdin avoids Windows `.cmd` argv newline handling and keeps the prompt intact.

Alternative considered: compress the prompt into a single-line argv value. This would avoid newline truncation but would make prompt readability and diff/review sections harder to reason about.

### Decision 2: Keep prompt content task-first

The non-interactive wrapper now starts with `AGOS task request:` followed by the concrete task, and only then lists the execution contract. This reduces the chance that local skills classify the prompt as an incomplete contract rather than an implementation request.

Alternative considered: keep the contract first and rely only on stdin. Runtime evidence showed local skills could respond to the contract itself before attending to the task, so task-first ordering is safer.

### Decision 3: Match `subprocess.run(input=...)` behavior in `.cmd` shim handling

AGOS already uses a custom `.cmd` shim runner to kill child process trees on timeout. That runner must explicitly set `stdin=subprocess.PIPE` when `input` is provided, matching Python's `subprocess.run` convenience behavior.

Alternative considered: bypass `.cmd` and resolve directly to a Node executable. That would couple AGOS to npm shim internals and weaken the generic command helper.

## Risks / Trade-offs

- Codex local skills may still spend tokens reading local skill files during review → Mitigation: reviewer prompt requires JSON-only output, and the adapter extracts the final JSON object.
- stdin prompt delivery changes command invocation shape → Mitigation: tests assert argv ends with `-` and prompt content is in `input`.
- The real dry-run candidate is not applied to `main` → Mitigation: dry-run still exercises planner, worker, candidate export, gate, reviewer, and decision while avoiding accidental repository mutation.

## Migration Plan

No data migration is required. Deploy by merging the code/test changes. Roll back by reverting the change if local Codex stdin behavior regresses.

## Open Questions

- Should a future CLI option expose a strict “require real planner/worker/reviewer” mode that fails instead of falling back?
