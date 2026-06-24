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

Recommended CI shape:

```bash
python -m pytest --cov=agos --cov-report=term-missing -q
python -m ruff check src tests
agos merge-gate --require-anchor --anchor-backend git-ref --base "$BASE_SHA" --head "$HEAD_SHA"
```

For GitHub pull requests, set `BASE_SHA` to the pull request base SHA and
`HEAD_SHA` to the submitted head SHA. The file trust-anchor backend is intended
for local development and tests; if you use it, pass `--anchor-path`.
