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
agos merge-gate --require-anchor --anchor-backend git-ref --base "$BASE_SHA" --head "$HEAD_SHA" --json
```

This requires a current active AGOS task and a trust anchor published to
`refs/agos/anchors/<task-id>` by trusted automation. Without that workflow, use
the local smoke test only:

```bash
python -m pytest tests/ci/test_merge_gate_smoke.py -q
```

## CI Integration Prerequisites

The `merge-gate` job in `.github/workflows/ci.yml` runs the real PR-bound
`agos merge-gate --require-anchor --base "$BASE_SHA" --head "$HEAD_SHA"` on
pull requests, but only when the repository carries AGOS governance state
(`.agos/agos.yaml` plus an active task and a published anchor on the PR head).
Repositories without `.agos/` skip the real binding; the smoke test still runs
and proves the command behaves correctly. This is the fail-closed contract: a
governed repository whose PR head lacks a matching anchor or task state blocks
here, so the PR cannot merge.

For a governed repository, ensure the PR head has governance state before the
`merge-gate` job runs. The recommended path (the plan's option A) is a prepare
job that runs on the PR head checkout:

```bash
agos start --title "Describe the governed change"
agos checkpoint          # with trust_anchor.auto_publish_on_checkpoint: true
git add .agos
git commit -m "publish AGOS checkpoint anchor"
```

This publishes the anchor to `refs/agos/anchors/<task-id>` and commits the
`.agos/` state so the `merge-gate` job can verify it. AGOS cannot create a task
from nothing; the PR author (or trusted automation) must start and checkpoint
the task first.

Honest boundary: AGOS guarantees that the `merge-gate` command itself fails
closed. It cannot guarantee that GitHub branch protection is configured. The
following platform settings are required separately and cannot be mutated by
AGOS (a stated design non-goal):

- require status checks before merging
- require branches to be up to date before merging
- required check: `merge-gate`
- disallow force pushes
