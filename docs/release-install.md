# AGOS Release And Install

AGOS is packaged as a Python CLI with the console script `agos`.

## Requirements

- Python 3.11 or 3.12
- Git
- Optional local agents: Codex CLI, Claude Code, Multica

## Install From A Release Artifact

Download the `agos-dist` artifact from the GitHub Actions `release` workflow
for a tag such as `v0.1.0`, then install the wheel:

```bash
pip install agos-0.1.0-py3-none-any.whl
agos version
agos --help
```

The release workflow currently builds and uploads `dist/*` as a GitHub Actions
artifact. PyPI publishing is not configured in this repository yet.

## Development Install

```bash
git clone https://github.com/zsr131550/agos.git
cd agos
pip install -e ".[dev]"
agos doctor
```

Optional LangGraph support:

```bash
pip install -e ".[dev,langgraph]"
```

## Release Workflow

1. Update version metadata in `pyproject.toml`.
2. Run local verification:

   ```bash
   python -m ruff check src tests
   python -m compileall -q src tests
   python -m pytest -q
   python -m build
   ```

3. Create and push a tag:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

4. Download the `agos-dist` artifact from the `release` workflow run.
5. Install the built wheel in a clean environment and smoke-test:

   ```bash
   pip install --force-reinstall agos-0.1.0-py3-none-any.whl
   agos version
   agos --help
   agos merge-gate --help
   ```

## Protected Merge Gate

The CI workflow exposes a dedicated status check named `merge-gate`. Configure
GitHub branch protection for `main` to require that check after the repository
plan supports protected branches.

Strict production enforcement should run:

```bash
agos prepare-merge-gate --base "$BASE_SHA" --head "$HEAD_SHA" --anchor-path ".agos/tasks/current/evidence/anchors.json" --issuer "github-actions"
agos merge-gate --require-anchor --anchor-backend file --anchor-path ".agos/tasks/current/evidence/anchors.json" --base "$BASE_SHA" --head "$HEAD_SHA" --json
```

In GitHub Actions, the recommended production path is to let a prepare job
materialize `.agos/tasks/current` plus a file-backend anchor artifact, then let
the `merge-gate` job download and verify that artifact. Without that workflow,
use the local smoke test only:

```bash
python -m pytest tests/ci/test_merge_gate_smoke.py -q
```

## CI Integration Prerequisites

The CI workflow now uses two pull-request jobs:

1. `agos-prepare` runs `agos prepare-merge-gate` on the PR head checkout.
   That command creates a fresh active task, binds the submitted diff into
   candidate evidence, runs candidate gates, and writes
   `.agos/tasks/current/evidence/anchors.json`.
2. `merge-gate` downloads the prepared `.agos/tasks/current` artifact and runs
   the real PR-bound `agos merge-gate --require-anchor --anchor-backend file --base "$BASE_SHA" --head "$HEAD_SHA"`.

The PR-bound merge gate intentionally does **not** use
`--allow-missing-review`. Prepared candidate evidence must include completed
candidate-bound review evidence; missing, stale, dev-only, or blocking review
evidence fails the gate.

Repositories without `.agos/agos.yaml` skip the real binding; the smoke test
still runs and proves the command behaves correctly. This is the fail-closed
contract: a governed repository whose PR head lacks valid prepared AGOS
evidence blocks here, so the PR cannot merge.

For a governed repository, the prepare job itself is now the governance-state
producer. It must run on the PR head checkout:

```bash
agos prepare-merge-gate \
  --base "$BASE_SHA" \
  --head "$HEAD_SHA" \
  --anchor-path ".agos/tasks/current/evidence/anchors.json" \
  --issuer "github-actions"
```

This writes a fresh `.agos/tasks/current` tree and a file trust anchor that the
`merge-gate` job can verify after downloading the artifact. The command is
purpose-built for CI and does not dispatch a real executor run.

Honest boundary: AGOS guarantees that the `merge-gate` command itself fails
closed. It cannot guarantee that GitHub branch protection is configured. The
following platform settings are required separately and cannot be mutated by
AGOS (a stated design non-goal):

- require status checks before merging
- require branches to be up to date before merging
- required check: `merge-gate`
- disallow force pushes
