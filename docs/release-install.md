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
