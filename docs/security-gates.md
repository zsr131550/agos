# AGOS Security Gates

AGOS supports lightweight local gates by default and production security gates by opt-in workflow.

Local hooks remain advisory because a developer can bypass them with `--no-verify`. Production enforcement should run in CI with `agos merge-gate` and any external scanner workflows required by the repository.

## Built-In Types

```yaml
workflows:
  production_security:
    gates:
      - id: no_secrets_in_diff
        stage: [pre-commit, pre-push, candidate]
        type: secret_scan

      - id: semgrep_security
        stage: [pre-push, candidate]
        type: semgrep
        options:
          config: p/security-audit

      - id: trufflehog_verified
        stage: [pre-push, candidate]
        type: trufflehog
        options:
          args: ["--only-verified"]

      - id: opa_policy
        stage: [pre-push, candidate]
        type: opa
        options:
          policy: policy/agos.rego
          input: policy/input.json

      - id: codeql_custom
        stage: [pre-push, candidate]
        type: codeql
        options:
          database: .codeql-db
          query: security-queries
```

## Behavior

Typed external gates are fail-closed:

- missing executable blocks
- non-zero exit blocks
- stdout and stderr are written as evidence
- gate `options` are included in `gates_locked`, so changing scanner config after task start blocks verification

Do not add heavy external gates to the default `feature` workflow unless every developer and CI runner has the tools installed.

## CI Boundary

Use AGOS gates for governance evidence and use scanner-native GitHub Actions where they are the stronger control. CodeQL, for example, is usually best enforced with GitHub code scanning while AGOS records whether the task's candidate evidence is complete and mergeable.

Recommended strict CI shape:

```bash
python -m pytest --cov=agos --cov-report=term-missing -q
python -m ruff check src tests
agos prepare-merge-gate --base "$BASE_SHA" --head "$HEAD_SHA" --anchor-path ".agos/tasks/current/evidence/anchors.json" --issuer "github-actions"
agos merge-gate --require-anchor --anchor-backend file --anchor-path ".agos/tasks/current/evidence/anchors.json" --base "$BASE_SHA" --head "$HEAD_SHA"
```

For GitHub pull requests, set `BASE_SHA` to the pull request base SHA and
`HEAD_SHA` to the submitted head SHA. In GitHub Actions, the recommended
production path is to use the file trust-anchor backend plus an uploaded
`.agos/tasks/current` artifact, because job-local git refs do not persist across
jobs.

## GitHub Protected Check

The workflow `.github/workflows/ci.yml` publishes a dedicated job named
`merge-gate`. That is the status check to require in branch protection for
`main`.

Minimum branch protection settings:

- require status checks before merging
- require branches to be up to date before merging
- required check: `merge-gate`
- disallow force pushes
- require conversation resolution if PR review is used

For private repositories, GitHub may require a paid plan to enable protected
branches. If the protection API returns `Upgrade to GitHub Pro or make this
repository public to enable this feature.`, the repository must be made public
or moved to a plan that supports protected branches before AGOS can enforce the
check at GitHub's merge button.

The CI smoke test exercises strict `--require-anchor --anchor-backend git-ref`
inside a temporary repository. Production enforcement in GitHub Actions uses a
prepare artifact plus `--anchor-backend file`; otherwise the merge gate should
fail closed.

## CI Job Behavior

The GitHub Actions flow in `.github/workflows/ci.yml` is split into two jobs on
pull requests:

- `agos-prepare` runs `agos prepare-merge-gate` on the PR head checkout. It
  creates a fresh active AGOS task under `.agos/tasks/current`, binds the
  submitted PR diff into candidate evidence, runs candidate gates, and writes a
  file trust anchor to `.agos/tasks/current/evidence/anchors.json`.
- `merge-gate` downloads that prepared `.agos/tasks/current` artifact and runs
  `agos merge-gate --require-anchor --anchor-backend file --base --head --json`.

The production CI path must not use `--allow-missing-review`. Review evidence
is part of the candidate binding that `agos prepare-merge-gate` materializes,
and the merge gate blocks missing, stale, dev-only, or open-blocking review
evidence.

On a repository without `.agos/agos.yaml` the real binding is skipped and the
smoke test still proves the command; see `docs/release-install.md` for the full
prerequisite checklist.

Fail-closed outcomes:

- no `.agos/agos.yaml` on the repository: the real PR binding is skipped, so
  AGOS is not enforcing that repository yet
- prepare job cannot materialize candidate evidence or candidate gates fail:
  `agos-prepare` exits non-zero and the PR is blocked
- anchor missing or not matching the prepared ledger/repo head: the
  `trust_anchor` check fails and the PR is blocked
- non-PR events (push to `main`): only the smoke test runs, so the gate does
  not block mainstream development

AGOS guarantees the command fails closed; it cannot configure GitHub branch
protection. Require the `merge-gate` status check in branch protection
separately.
