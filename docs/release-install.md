# AGOS Release And Install

AGOS is packaged as a Python CLI with the console script `agos`.

## Requirements

- Python 3.11 or newer
- Git
- Optional local agents: Codex CLI, Claude Code, Multica, or OpenHands

## Install A Published Release

After the PyPI trusted publisher has been configured for this repository:

```bash
python -m pip install agos
agos version
agos --help
```

Every `v*` tag also creates a GitHub Release containing the exact wheel and
sdist sent to the publishing jobs. To install a downloaded wheel instead:

```bash
python -m pip install agos-0.1.0-py3-none-any.whl
agos version
agos dashboard --help
```

## Development Install

```bash
git clone https://github.com/zsr131550/agos.git
cd agos
python -m pip install -e ".[dev]"
agos doctor
```

Optional LangGraph support:

```bash
python -m pip install -e ".[dev,langgraph]"
```

## Local Release Verification

The local release checks are provider-free. With the repository's development
environment already installed, they do not need network access:

```bash
python -m ruff check src tests scripts
python -m compileall -q src tests scripts
python -m pytest --cov=agos --cov-report=term-missing -q
python -m build --no-isolation
python scripts/verify_release.py --tag v0.1.0 --dist dist
```

`verify_release.py` is read-only. It checks the tag against project metadata,
requires exactly one wheel and one sdist, and verifies that the Dashboard,
hook templates, and complete MIT license are present in the distributions.

Install and smoke-test the built wheel before tagging:

```bash
python -m pip install --no-deps --force-reinstall dist/agos-0.1.0-py3-none-any.whl
agos version
agos --help
agos doctor --help
agos dashboard --help
agos merge-gate --help
```

## Trusted Publishing Setup

The `publish-pypi` job uses PyPI trusted publishing. It does not read a PyPI API
token. Before the first release, a PyPI project owner must create a trusted
publisher with these exact values:

- Owner/repository: `zsr131550/agos`
- Workflow: `release.yml`
- Environment: `pypi`

Create the matching GitHub environment named `pypi`; its optional reviewers
become the final publication approval. A missing or mismatched publisher causes
the PyPI job to fail closed. Do not add `PYPI_API_TOKEN` as a workaround.

## Release Workflow

1. Update `pyproject.toml` and `src/agos/__init__.py` to the same version.
2. Run the local verification above.
3. Create the matching tag and push it:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

4. The `build` job runs the full provider-free checks once and uploads
   `agos-dist`.
5. `github-release` and `publish-pypi` download that same immutable Actions
   artifact. The first creates GitHub release assets; the second exchanges the
   GitHub OIDC identity for a short-lived PyPI publishing credential.

`workflow_dispatch` runs build and artifact verification but does not publish,
because it does not run from a `refs/tags/v*` ref. If PyPI publication fails,
do not reuse or move the tag after artifacts have been released. Correct the
environment/publisher configuration, delete an incomplete release only when no
artifact was consumed, and rerun the original immutable tag workflow.

## Protected Merge Gate

The pull-request workflow uses a protected-base checkout for the verifier and
trusted configuration. `agos-prepare` creates deterministic
`ci_reconstructed` evidence for the submitted diff. Under the compatible
`optional` provenance policy this is validated and reported as
`unprovenanced`; it is never given a fabricated review, decision, or applied
event. `required` policy rejects reconstructed evidence and needs real governed
candidate evidence plus a valid signed anchor.

Repositories without `.agos/agos.yaml` on the protected base skip AGOS diff
binding. Governed repositories fail closed when preparation or verification
evidence is missing.

Recommended branch-protection requirements for `main`:

- all six `verify` matrix checks (Linux, macOS, Windows on Python 3.11/3.12)
- `autonomous-readiness`
- `merge-gate`
- `CodeQL / python`
- require the branch to be up to date and disallow force pushes

AGOS does not mutate repository protection. Inspect the current settings with
this read-only command:

```bash
gh api --method GET repos/zsr131550/agos/branches/main/protection
```

The real-agent smoke workflow remains opt-in/scheduled and is not part of the
provider-free merge or release checks.
