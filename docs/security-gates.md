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
agos merge-gate --require-anchor --anchor-backend git-ref --base "$BASE_SHA" --head "$HEAD_SHA"
```

For GitHub pull requests, set `BASE_SHA` to the pull request base SHA and
`HEAD_SHA` to the submitted head SHA. The file trust-anchor backend is intended
for local development and tests; if you use it, pass `--anchor-path`.

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
inside a temporary repository. Production enforcement needs the same trust
anchor flow for real PRs; otherwise the merge gate should fail closed.

## CI Job Behavior

The `merge-gate` job in `.github/workflows/ci.yml` runs the real PR-bound
`agos merge-gate --require-anchor --base --head --json` on pull request events,
but only when the repository carries `.agos/` governance state. On a governed
repository the PR head must have an active task and an anchor published by a
prior `agos checkpoint` (set `trust_anchor.auto_publish_on_checkpoint: true`).
On a repository without `.agos/` the real binding is skipped and the smoke test
still proves the command; see `docs/release-install.md` for the full
prerequisite checklist.

Fail-closed outcomes:

- no `.agos/agos.yaml` or no active task on the PR head: the `initialized`
  check fails, the command exits non-zero, and the PR is blocked
- anchor missing or not matching the current ledger/repo head: the
  `trust_anchor` check fails and the PR is blocked
- non-PR events (push to `main`): only the smoke test runs, so the gate does
  not block mainstream development

AGOS guarantees the command fails closed; it cannot configure GitHub branch
protection. Require the `merge-gate` status check in branch protection
separately.
