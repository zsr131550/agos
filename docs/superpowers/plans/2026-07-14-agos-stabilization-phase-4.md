# AGOS Stabilization Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AGOS state writes recoverable across processes, make agent and Dashboard permissions safe by default, and finish a reproducible open-source release path without breaking existing CLI or `.agos` data.

**Architecture:** Put a small cross-platform file-lock boundary below the hash-chained ledger, then treat `status.json` as a repairable cache replayed from verified ledger events. Centralize Codex/Claude permission argv construction so legacy executors and candidate workers share one explicit safe/dangerous policy. Keep the Dashboard standard-library HTTP server, but add an ephemeral loopback token, explicit remote token requirement, Bearer authentication, and same-origin checks. Complete packaging and GitHub workflows with source-controlled policy tests and offline artifact inspection.

**Tech Stack:** Python 3.11+ standard library, Pydantic v2, Typer, stdlib HTTP server, GitHub Actions, pytest, setuptools/build.

## Global Constraints

- Preserve all existing command names, options, Dashboard request fields, and readable v0.1 `.agos` files.
- Existing configs that omit new permission fields load with safe defaults; dangerous behavior remains available through one explicit compatibility boolean.
- No new runtime or development dependency may be downloaded or added.
- Required tests and local release verification make no network or model-provider request.
- Ledger locking must work on POSIX and Windows with standard-library primitives.
- `status.json` remains an atomic derived cache; verified ledger events remain the state truth source.
- Loopback remains the Dashboard default; non-loopback bind requires an explicit token.
- Candidate patch scope remains an evidence guard and is not described as an operating-system sandbox.
- Maintain the 90 percent coverage threshold.

---

### Task 1: Cross-Process Ledger Locking

**Files:**
- Create: `src/agos/core/file_lock.py`
- Modify: `src/agos/core/ledger.py`
- Modify: `tests/core/test_ledger.py`

**Interfaces:**
- Produces `exclusive_file_lock(target: Path) -> Iterator[None]` using `fcntl.flock` on POSIX and `msvcrt.locking` on Windows.
- `Ledger.append()` holds the lock from its fresh tail read through line append, flush, and `os.fsync`.
- `Ledger.read_all()`, `head_hash()`, and `verify_chain()` never observe a partially appended line.
- `append_repo_record()` uses the same durable append boundary while retaining plain JSONL format.

- [x] **Step 1: Add a failing multi-process append test**

Add a module-level multiprocessing worker that waits on a shared start event and appends numbered payloads through independent `Ledger` instances. Start at least four spawned processes, assert every process exits zero, assert every payload is present exactly once, assert sequences are exactly `1..N`, and call `verify_chain()`.

```python
def test_concurrent_process_appends_preserve_one_chain(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes = [
        context.Process(target=_append_batch, args=(path, worker, 30, start))
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0

    records = Ledger(path).read_all()
    assert [record["seq"] for record in records] == list(range(1, 121))
    assert len({(record["worker"], record["index"]) for record in records}) == 120
    Ledger(path).verify_chain()
```

- [x] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/core/test_ledger.py::test_concurrent_process_appends_preserve_one_chain -q
```

Expected: FAIL against the unlocked read-tail/append race with duplicate sequence/hash ancestry or a broken chain.

- [x] **Step 3: Implement the lock and durable append**

`exclusive_file_lock()` creates a sibling lock file, takes an exclusive blocking lock, yields, and always unlocks in `finally`. Keep platform imports inside private POSIX/Windows helpers so importing AGOS works on both operating systems. In `Ledger`, bypass any cached tail while inside the append lock, append exactly one JSON line, call `flush()` and `os.fsync()`, and only then release the lock. Remove process-stale tail caching or refresh it after every locked read.

- [x] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest tests/core/test_ledger.py tests/core/test_executor_seam.py -q
.venv/bin/python -m ruff check src/agos/core/file_lock.py src/agos/core/ledger.py tests/core/test_ledger.py
git add src/agos/core/file_lock.py src/agos/core/ledger.py tests/core/test_ledger.py
git commit -m "fix: serialize durable ledger appends"
```

---

### Task 2: Status Replay and Cache Repair

**Files:**
- Modify: `src/agos/core/status.py`
- Modify: `tests/core/test_status.py`
- Modify: `tests/cli/test_status_command.py`
- Modify only on regression: `src/agos/web/api.py`

**Interfaces:**
- Produces `replay_status(task: Task, records: list[dict], *, cached: Status | None = None) -> Status`.
- `load_status(paths)` returns an unchanged compatible cache when its head equals the verified ledger head.
- A missing, invalid, or stale cache is rebuilt from a verified ledger and saved atomically.
- Replay restores executor dispatch, checkpoint sequence, gate states, Dashboard phases, and terminal task state.

- [x] **Step 1: Add failing replay tests**

Cover these independent cases:

```python
def test_load_status_repairs_cache_after_crash_between_ledger_and_cache(tmp_repo):
    paths, task, cached = _started_status(tmp_repo)
    final = Ledger(paths.ledger).append(
        {"type": "task_execution_completed", "task_id": task.id,
         "mode": "candidate", "run_id": "candidate-1", "state": "completed"}
    )

    recovered = load_status(paths)

    assert recovered.phase == "done"
    assert recovered.executor_run.run_id == "candidate-1"
    assert recovered.ledger_head_hash == final["hash"]
    assert load_status(paths) == recovered


def test_load_status_rebuilds_missing_cache_from_legacy_events(tmp_repo):
    paths, task, ledger = _replay_task(tmp_repo, gates=["tests_pass"])
    ledger.append(
        {"type": "executor_dispatched", "task_id": task.id, "adapter": "multica",
         "run_id": "legacy-1", "issue_id": "AGO-1"}
    )
    ledger.append({"type": "checkpoint", "run_id": "legacy-1", "last_seq": 7})
    ledger.append(
        {"type": "gate_evaluated", "gate": "tests_pass", "state": "pass",
         "stage": "pre-push"}
    )
    final = ledger.append(
        {"type": "executor_blocked", "run_id": "legacy-1", "state": "failed"}
    )
    paths.status_json.unlink(missing_ok=True)

    recovered = load_status(paths)

    assert recovered.phase == "blocked"
    assert recovered.executor_run.run_id == "legacy-1"
    assert recovered.last_event_seq == 7
    assert recovered.gates["tests_pass"].state == "pass"
    assert recovered.ledger_head_hash == final["hash"]
```

Also assert an invalid cache is repaired, a current old cache is returned without rewriting, and a tampered ledger raises `LedgerTamperError` without replacing the cache.

- [x] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/core/test_status.py tests/cli/test_status_command.py -q
```

Expected: FAIL because `load_status()` currently trusts the cache and `derive_status()` always selects `executing`.

- [x] **Step 3: Implement deterministic event replay**

Replay rules, in ledger order:

- `executor_dispatched` sets `ExecutorRunInfo` and phase `executing`.
- `checkpoint.last_seq` restores the executor event cursor.
- `gate_evaluated` restores the named `GateState` and its record timestamp.
- explicit Dashboard `phase` records restore `executing`, `blocked`, or `done`.
- `executor_completed`, `closeout_completed`, `dashboard_archived`, and completed task execution restore `done`.
- executor/dispatch/task failures restore `blocked`; a task execution event whose state is `running` restores `executing`.
- candidate task execution records restore `candidate_pipeline` as the adapter.

Initialize configured gates from `task.gates`. Use cached executor/gate values only as a compatibility fallback when old ledgers lack the corresponding historical event. Verify the full chain before using it, set the actual ledger head, and repair through existing `save_status()`.

- [x] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest tests/core/test_status.py tests/cli/test_status_command.py \
  tests/cli/test_checkpoint.py tests/web/test_api.py -q
git add src/agos/core/status.py tests/core/test_status.py tests/cli/test_status_command.py
git commit -m "fix: recover stale status from ledger"
```

---

### Task 3: Safe Agent Permission Defaults and Diagnostics

**Files:**
- Create: `src/agos/adapters/agent_permissions.py`
- Modify: `src/agos/core/config.py`
- Modify: `src/agos/adapters/workers/codex_cli.py`
- Modify: `src/agos/adapters/workers/claude_code.py`
- Modify: `src/agos/adapters/local_cli_executor.py`
- Modify: `src/agos/cli/worker_registry.py`
- Modify: `src/agos/cli/executor_registry.py`
- Modify: `src/agos/cli/cmd_doctor.py`
- Modify: `tests/core/test_config.py`
- Modify: `tests/adapters/test_worker_adapters.py`
- Modify: `tests/adapters/test_claude_code_worker.py`
- Modify: `tests/adapters/test_local_cli_executor.py`
- Modify: `tests/cli/test_worker_registry.py`
- Modify: `tests/cli/test_executor_registry.py`
- Modify: `tests/cli/test_doctor.py`
- Modify: `tests/cli/test_init.py`

**Interfaces:**
- Adds `dangerously_bypass_permissions: bool = False` to `ExecutorConfig` and `WorkerConfig`.
- Produces `codex_permission_args(dangerous: bool) -> list[str]` and `claude_permission_args(dangerous: bool) -> list[str]`.
- Safe Codex argv uses `--sandbox workspace-write -c 'approval_policy="never"'`; the supported `codex exec` parser accepts this form after the subcommand.
- Safe Claude argv uses `--safe-mode --permission-mode dontAsk`.
- Explicit dangerous mode preserves Codex `--dangerously-bypass-approvals-and-sandbox` and Claude `--permission-mode bypassPermissions`.
- Adds a doctor check named `agent_permissions`; dangerous config is a warning, not an exit-code change.

- [x] **Step 1: Add failing default and compatibility tests**

```python
def test_codex_worker_defaults_to_workspace_write_without_bypass(monkeypatch, tmp_path):
    argv = _capture_successful_codex_start(monkeypatch)
    adapter = CodexWorkerAdapter(command="codex")
    adapter.start(_worker_request(tmp_path))
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    sandbox_index = argv.index("--sandbox")
    approval_index = argv.index("-c")
    assert argv[sandbox_index:sandbox_index + 2] == ["--sandbox", "workspace-write"]
    assert argv[approval_index:approval_index + 2] == ["-c", 'approval_policy="never"']


def test_explicit_dangerous_codex_worker_preserves_legacy_flag(monkeypatch, tmp_path):
    argv = _capture_successful_codex_start(monkeypatch)
    adapter = CodexWorkerAdapter(command="codex", dangerously_bypass_permissions=True)
    adapter.start(_worker_request(tmp_path))
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
```

Add equivalent Claude, legacy executor, registry, config-default, init-output, and doctor JSON assertions. Doctor detail must name each dangerous executor/worker without echoing environment values or tokens.

- [x] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/adapters/test_worker_adapters.py \
  tests/adapters/test_claude_code_worker.py tests/adapters/test_local_cli_executor.py \
  tests/cli/test_worker_registry.py tests/cli/test_executor_registry.py \
  tests/cli/test_doctor.py tests/cli/test_init.py tests/core/test_config.py -q
```

- [x] **Step 3: Centralize permission argv and pass config through registries**

Construct provider argv only through `agent_permissions.py`. Keep prompt handling, ignore-user-config/rules, polling, and output parsing unchanged. Include the effective dangerous boolean in worker health metadata. Pass the config field through worker and executor factories. `agos init` may omit the field in YAML because omission now means safe; no provider health call is added.

- [x] **Step 4: Add doctor permission reporting**

Append `agent_permissions` in initialized, invalid-config, and uninitialized doctor flows. Return `passed` when every Codex/Claude executor and worker uses safe defaults. Return `warning` with stable sorted identifiers when an explicit bypass is active. Warnings continue to leave overall doctor exit code zero.

- [x] **Step 5: Verify and commit**

```bash
.venv/bin/python -m pytest tests/adapters tests/cli/test_worker_registry.py \
  tests/cli/test_executor_registry.py tests/cli/test_doctor.py tests/cli/test_init.py \
  tests/core/test_config.py -q
.venv/bin/python -m ruff check src tests
git add src/agos/adapters/agent_permissions.py src/agos/core/config.py \
  src/agos/adapters/workers/codex_cli.py src/agos/adapters/workers/claude_code.py \
  src/agos/adapters/local_cli_executor.py src/agos/cli/worker_registry.py \
  src/agos/cli/executor_registry.py src/agos/cli/cmd_doctor.py \
  tests/core/test_config.py tests/adapters tests/cli/test_worker_registry.py \
  tests/cli/test_executor_registry.py tests/cli/test_doctor.py tests/cli/test_init.py
git commit -m "fix: default agent CLIs to safe permissions"
```

---

### Task 4: Authenticated Dashboard Mutations

**Files:**
- Modify: `src/agos/web/server.py`
- Modify: `src/agos/web/static/index.html`
- Modify: `src/agos/cli/cmd_dashboard.py`
- Modify: `tests/web/test_server.py`
- Modify: `tests/web/test_static_resources.py`
- Modify: `tests/cli/test_dashboard.py`

**Interfaces:**
- `create_dashboard_server(repo_root: Path, *, host: str, port: int, token: str | None = None)` generates an ephemeral token only for loopback when omitted.
- A non-loopback host without an explicit non-empty token raises before bind.
- Every `/api/` POST requires `Authorization: Bearer <token>` and an HTTP `Origin` matching the request host.
- Remote-bound `/api/` GET requests also require the token; loopback reads remain compatible.
- The loopback index receives its generated token through a no-store bootstrap value; an explicit remote token is accepted from the URL fragment and kept only in session storage.
- `agos dashboard` adds `--token` with `AGOS_DASHBOARD_TOKEN` environment support.

- [x] **Step 1: Add failing server security tests**

```python
def test_non_loopback_bind_requires_explicit_token(tmp_repo):
    with pytest.raises(ValueError, match="token"):
        create_dashboard_server(tmp_repo, host="0.0.0.0", port=0)


def test_mutation_rejects_missing_or_wrong_bearer_token(tmp_repo):
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_json(
            server,
            "/api/runs/current/pause",
            {},
            token="wrong-token",
            origin=_server_origin(server),
        )
    assert status == 401
    assert payload["error"]["code"] == "unauthorized"


def test_mutation_rejects_cross_origin_even_with_valid_token(tmp_repo):
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_json(
            server,
            "/api/runs/current/pause",
            {},
            token=server.auth_token,
            origin="https://attacker.invalid",
        )
    assert status == 403
    assert payload["error"]["code"] == "origin_forbidden"


def test_mutation_accepts_valid_token_and_same_origin(tmp_repo):
    write_dashboard_config(tmp_repo)
    with running_dashboard_server(tmp_repo) as server:
        status, payload = post_json(
            server,
            "/api/runs",
            {"title": "Authenticated task"},
            token=server.auth_token,
            origin=_server_origin(server),
        )
    assert status == 201
    assert payload["ok"] is True
```

Also test remote GET authentication, loopback GET compatibility, the existing 64 KiB body limit, token redaction from error bodies and printed base URL, static bootstrap behavior, and CLI token forwarding.

- [x] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/web/test_server.py tests/web/test_static_resources.py \
  tests/cli/test_dashboard.py -q
```

- [x] **Step 3: Implement authentication and same-origin validation**

Use `secrets.token_urlsafe(32)` for an omitted loopback token and `hmac.compare_digest()` for verification. Validate `Origin` as `http`/`https` with the same normalized host and port as the request `Host`; reject missing, null, malformed, or cross-origin values for mutations. Perform access checks before reading a request body. Keep default request logging suppressed and ensure no payload includes the token.

The static `fetchJson()` adds the Bearer header when a bootstrap/fragment token exists. Remove the token fragment with `history.replaceState()` after moving it to session storage. Do not display the token in page text.

- [x] **Step 4: Verify and commit**

```bash
.venv/bin/python -m pytest tests/web tests/cli/test_dashboard.py -q
.venv/bin/python -m ruff check src/agos/web src/agos/cli/cmd_dashboard.py tests/web tests/cli/test_dashboard.py
git add src/agos/web/server.py src/agos/web/static/index.html \
  src/agos/cli/cmd_dashboard.py tests/web tests/cli/test_dashboard.py
git commit -m "fix: protect dashboard mutations with local auth"
```

---

### Task 5: Open-Source Release Supply Chain

**Files:**
- Create: `LICENSE`
- Create: `.github/dependabot.yml`
- Create: `.github/workflows/codeql.yml`
- Create: `scripts/verify_release.py`
- Create: `tests/ci/test_release_policy.py`
- Create: `tests/packaging/test_wheel_contents.py`
- Modify: `.github/workflows/release.yml`
- Modify: `pyproject.toml`
- Modify: `docs/release-install.md`
- Modify: `README.md`

**Interfaces:**
- Source and built wheel/sdist contain the complete MIT license text.
- Dependabot covers `pip` and `github-actions` on a monthly schedule.
- CodeQL analyzes Python on pull requests, `main`, and a weekly schedule with minimal permissions.
- Release verification runs ruff, compileall, coverage tests, build, wheel inspection/install, and CLI smoke without provider credentials.
- Tag releases upload `dist/*` to a GitHub Release and expose a PyPI trusted-publishing job with `id-token: write` and the `pypi` environment.
- `scripts/verify_release.py` performs read-only tag/version and wheel/sdist content checks with no network access.

- [x] **Step 1: Add failing policy and packaging tests**

Assert exact workflow properties rather than running GitHub Actions:

```python
def test_release_workflow_has_github_assets_and_trusted_pypi_publish():
    workflow, text = _load_workflow("release.yml")
    assert "github-release" in workflow["jobs"]
    assert workflow["jobs"]["publish-pypi"]["permissions"]["id-token"] == "write"
    assert "pypa/gh-action-pypi-publish@release/v1" in text
    assert "OPENAI_API_KEY" not in text
    assert "ANTHROPIC_API_KEY" not in text


def test_built_wheel_contains_dashboard_hooks_and_license(tmp_path):
    wheel = _build_wheel_without_isolation(tmp_path)
    names = set(zipfile.ZipFile(wheel).namelist())
    assert "agos/web/static/index.html" in names
    assert "agos/hooks/templates/pre-commit.sh" in names
    assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
```

Also parse Dependabot and CodeQL YAML, require complete MIT phrases in `LICENSE`, and verify release jobs contain the same offline checks as CI.

- [x] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/ci/test_release_policy.py \
  tests/packaging/test_wheel_contents.py -q
```

- [x] **Step 3: Add license, metadata, update automation, and security scan**

Add the standard MIT text with `Copyright (c) 2026 AGOS project`. Declare `license-files = ["LICENSE"]` in project metadata. Configure monthly Dependabot updates for Python packages and GitHub Actions. Configure CodeQL with `contents: read`, `packages: read`, and `security-events: write`, using `github/codeql-action/init@v3` and `analyze@v3` for Python.

- [x] **Step 4: Implement reproducible release verification and publishing**

The build job runs the provider-free verification suite, builds with `python -m build`, calls `scripts/verify_release.py`, installs the wheel, and runs CLI smoke commands. A tag-only `github-release` job downloads the exact build artifact and uses `gh release create` with `contents: write`. A tag-only `publish-pypi` job downloads the same artifact, uses environment `pypi`, grants only `id-token: write`, and invokes the official PyPA trusted-publishing action.

`verify_release.py --tag v0.1.0 --dist dist` must validate that the tag equals the `pyproject.toml` version, that exactly one wheel and one sdist exist, and that both contain the Dashboard asset and license where applicable. It only reads files.

- [x] **Step 5: Document release and branch-protection verification**

Replace statements that PyPI is unconfigured with trusted-publishing prerequisites, GitHub environment setup, tag/version checks, and rollback boundaries. Document required checks `verify`, `autonomous-readiness`, `merge-gate`, and CodeQL. Include the read-only command `gh api --method GET repos/zsr131550/agos/branches/main/protection`; do not add any branch-protection mutation.

- [x] **Step 6: Verify and commit**

```bash
.venv/bin/python -m pytest tests/ci tests/packaging/test_wheel_contents.py -q
PATH="$PWD/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
PYTHONPATH="/Users/zhangrui/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages:$PWD/src" \
.venv/bin/python -m build --no-isolation
.venv/bin/python scripts/verify_release.py --tag v0.1.0 --dist dist
git add LICENSE .github/dependabot.yml .github/workflows/codeql.yml \
  .github/workflows/release.yml scripts/verify_release.py tests/ci/test_release_policy.py \
  tests/packaging/test_wheel_contents.py pyproject.toml docs/release-install.md README.md
git commit -m "ci: add trusted open source release pipeline"
```

---

### Task 6: Migration Documentation and Final Offline Verification

**Files:**
- Create: `docs/state-security.md`
- Modify: `README.md`
- Modify: `docs/execution-modes.md`
- Modify: `docs/superpowers/plans/2026-07-14-agos-stabilization-phase-4.md`
- Modify only on regression: implementation/tests from Tasks 1-5.

**Interfaces:**
- Documents lock files, automatic status repair, explicit dangerous permission migration, loopback/remote Dashboard token behavior, and the boundary between patch scope and OS sandboxing.
- Proves the complete repository from a provider-free, network-free local environment.

- [x] **Step 1: Write migration and operations documentation**

Document:

- old config omission now selects safe worker permissions;
- exact `dangerously_bypass_permissions: true` compatibility syntax and doctor warning;
- status cache deletion/staleness recovery and ledger tamper failure behavior;
- `AGOS_DASHBOARD_TOKEN`, same-origin mutation requirements, and remote bind refusal without a token;
- command workers and fake reviewers remain the deterministic offline test path;
- write scope validates Git evidence but cannot prevent arbitrary non-Git side effects from a trusted executable.

- [x] **Step 2: Run focused security/state verification**

```bash
PATH="$PWD/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
PYTHONPATH="/Users/zhangrui/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages:$PWD/src" \
.venv/bin/python -m pytest tests/core/test_ledger.py tests/core/test_status.py \
  tests/adapters/test_worker_adapters.py tests/adapters/test_claude_code_worker.py \
  tests/cli/test_doctor.py tests/web/test_server.py tests/ci/test_release_policy.py \
  tests/packaging/test_wheel_contents.py -q
```

- [x] **Step 3: Run full offline verification**

```bash
PATH="$PWD/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
PYTHONPATH="/Users/zhangrui/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages:$PWD/src" \
.venv/bin/python -m ruff check src tests scripts
PATH="$PWD/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
PYTHONPATH="/Users/zhangrui/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages:$PWD/src" \
.venv/bin/python -m compileall -q src tests scripts
git diff --check
PATH="$PWD/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
PYTHONPATH="/Users/zhangrui/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages:$PWD/src" \
.venv/bin/python -m pytest --cov=agos --cov-report=term-missing -q
rm -rf dist
PATH="$PWD/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
PYTHONPATH="/Users/zhangrui/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages:$PWD/src" \
.venv/bin/python -m build --no-isolation
.venv/bin/python scripts/verify_release.py --tag v0.1.0 --dist dist
```

- [x] **Step 4: Run isolated wheel smoke and inspect the worktree**

Install the wheel with `--no-index --no-deps` into a temporary target, run from outside the checkout with that target first on `PYTHONPATH`, and verify `agos version`, `agos --help`, `agos doctor --help`, and `agos dashboard --help`. Then run:

```bash
git status --short
git diff --check
git log --oneline --decorate -20
```

- [x] **Step 5: Mark the plan complete and commit docs**

```bash
git add README.md docs/execution-modes.md docs/state-security.md \
  docs/superpowers/plans/2026-07-14-agos-stabilization-phase-4.md
git commit -m "docs: complete state security and release stabilization"
```
