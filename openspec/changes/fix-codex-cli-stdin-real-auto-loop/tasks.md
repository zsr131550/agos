## 1. Prompt transport

- [x] 1.1 Add regression coverage that non-interactive prompts lead with the concrete task before the execution contract.
- [x] 1.2 Update non-interactive prompt wrapping to put the task request first.
- [x] 1.3 Add regression coverage that Codex worker prompts are passed through stdin with `codex exec -`.
- [x] 1.4 Update the Codex worker adapter to pass the wrapped prompt through stdin.
- [x] 1.5 Add regression coverage that Codex reviewer prompts are passed through stdin with `codex exec -`.
- [x] 1.6 Update the Codex reviewer adapter to pass the review prompt through stdin.

## 2. Windows command shim support

- [x] 2.1 Add regression coverage for `.cmd` shim execution with `input=...`.
- [x] 2.2 Update the tree-aware `.cmd` shim runner to set `stdin=PIPE` when input is provided.

## 3. Verification

- [x] 3.1 Run focused adapter, reviewer, planner, and auto-run tests.
- [x] 3.2 Run a real AGOS `run auto --dry-run --json` using local Codex planner, Codex worker, and Codex reviewer.
- [x] 3.3 Validate OpenSpec artifacts for the change.
