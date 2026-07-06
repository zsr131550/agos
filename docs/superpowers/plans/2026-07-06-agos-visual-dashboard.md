# AGOS Visual Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Chinese AGOS dashboard that reads `.agos/` state, exposes read-only JSON APIs, and renders runs, workflow, subagent nodes, candidates, reviews, merge-gate status, evidence, and self-distillation summaries in a browser.

**Architecture:** Add a lightweight `agos.web` package using Python standard-library HTTP serving plus static HTML/CSS/JS. API functions convert existing AGOS core files into safe read-only dictionaries; the server routes `/api/*` and static assets; the CLI command `agos dashboard` starts the local server.

**Tech Stack:** Python 3.11+, Typer, Pydantic models already present in AGOS, `http.server`, `importlib.resources`, vanilla HTML/CSS/JavaScript, pytest.

---

## Scope Check

The approved spec covers one cohesive subsystem: a local visual companion for current AGOS state. This plan keeps the first implementation read-only and avoids action buttons that mutate the repository. The action surface, DAG graph, and automatic self-distillation writes remain later iterations.

## File Structure

- Create `src/agos/web/__init__.py`
  - Package marker and exported version string for the dashboard web surface.
- Create `src/agos/web/evidence.py`
  - Safe `.agos/tasks/current` and `.agos/tasks/current/evidence` evidence resolver.
  - Rejects absolute paths, path traversal, Windows drive paths, and unknown task-relative roots.
- Create `src/agos/web/api.py`
  - Read-only dashboard API payload builders.
  - Config redaction helpers.
  - Aggregates status, runs, ledger, execution, candidates, reviews, merge-gate, and self-distillation summaries.
- Create `src/agos/web/server.py`
  - Standard-library HTTP server.
  - Routes static `index.html`, `/api/health`, `/api/config`, `/api/status`, `/api/runs`, `/api/runs/current`, `/api/runs/current/ledger`, `/api/runs/current/execution`, `/api/runs/current/candidates`, `/api/runs/current/reviews`, and `/api/runs/current/evidence?ref=...`.
- Create `src/agos/web/static/index.html`
  - Chinese single-page dashboard.
  - Polls read-only APIs.
  - Shows left run list and right detail panel.
- Create `src/agos/cli/cmd_dashboard.py`
  - Typer command wrapper for `agos dashboard`.
- Modify `src/agos/cli/main.py`
  - Import and register `dashboard_command`.
- Modify `pyproject.toml`
  - Include `src/agos/web/static/index.html` in package data.
- Create `tests/web/test_static_resources.py`
  - Verifies static dashboard is package-readable.
- Create `tests/web/test_evidence.py`
  - Verifies safe evidence reads and traversal rejection.
- Create `tests/web/test_api.py`
  - Verifies DTOs on initialized and uninitialized repositories.
- Create `tests/web/test_server.py`
  - Starts real local HTTP server on port `0` and calls JSON/static endpoints.
- Create `tests/cli/test_dashboard.py`
  - Verifies CLI registration and command wiring without blocking forever.
- Modify `README.md`
  - Add a short Chinese “可视化控制台” section with commands and security boundary.

---

### Task 1: Add the web package and package static assets

**Files:**
- Create: `tests/web/test_static_resources.py`
- Create: `src/agos/web/__init__.py`
- Create: `src/agos/web/static/index.html`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write the failing static-resource test**

Create `tests/web/test_static_resources.py`:

```python
from __future__ import annotations

from importlib import resources


def test_dashboard_static_index_is_packaged() -> None:
    index = resources.files("agos.web").joinpath("static", "index.html")

    text = index.read_text(encoding="utf-8")

    assert '<main id="app">' in text
    assert "AGOS 控制台" in text
    assert "data-agos-dashboard" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m pytest tests/web/test_static_resources.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agos.web'` or missing resource.

- [ ] **Step 3: Create the web package marker**

Create `src/agos/web/__init__.py`:

```python
"""Local read-only web dashboard for AGOS repositories."""
from __future__ import annotations

DASHBOARD_TITLE = "AGOS 控制台"
```

- [ ] **Step 4: Create the initial static HTML**

Create `src/agos/web/static/index.html`:

```html
<!doctype html>
<html lang="zh-CN" data-agos-dashboard>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AGOS 控制台</title>
</head>
<body>
  <main id="app">
    <h1>AGOS 控制台</h1>
    <p id="boot-message">正在连接本地 AGOS Dashboard API。</p>
  </main>
</body>
</html>
```

- [ ] **Step 5: Package static HTML with the wheel**

Modify `pyproject.toml` package-data section:

```toml
[tool.setuptools.package-data]
"agos.hooks.templates" = ["*.sh"]
"agos.web" = ["static/*.html"]
```

- [ ] **Step 6: Run the resource test**

Run:

```bash
python -m pytest tests/web/test_static_resources.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add pyproject.toml src/agos/web/__init__.py src/agos/web/static/index.html tests/web/test_static_resources.py
git commit -m "feat: add dashboard web package shell"
```

---

### Task 2: Implement the safe evidence resolver

**Files:**
- Create: `tests/web/test_evidence.py`
- Create: `src/agos/web/evidence.py`

- [ ] **Step 1: Write failing evidence resolver tests**

Create `tests/web/test_evidence.py`:

```python
from __future__ import annotations

import pytest

from agos.core.repo import repo_paths
from agos.web.evidence import EvidenceResolutionError, read_evidence_text, resolve_evidence_ref


def test_resolves_task_relative_evidence_ref(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "tests_pass.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("gate ok\n", encoding="utf-8")

    resolved = resolve_evidence_ref(paths, "evidence/gates/tests_pass.log")

    assert resolved == target.resolve()
    assert read_evidence_text(paths, "evidence/gates/tests_pass.log")["text"] == "gate ok\n"


def test_resolves_bare_evidence_ref_inside_evidence_dir(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "tests_pass.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("gate ok\n", encoding="utf-8")

    resolved = resolve_evidence_ref(paths, "gates/tests_pass.log")

    assert resolved == target.resolve()


@pytest.mark.parametrize(
    "ref",
    [
        "../README.md",
        "evidence/../../README.md",
        "/tmp/secret.txt",
        "C:/Users/ZR/.ssh/id_rsa",
        "reviews/../task.yaml",
        "",
    ],
)
def test_rejects_unsafe_evidence_refs(tmp_repo, ref: str) -> None:
    paths = repo_paths(tmp_repo)

    with pytest.raises(EvidenceResolutionError):
        resolve_evidence_ref(paths, ref)


def test_rejects_unknown_task_relative_root(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    (paths.current_task / "private.txt").parent.mkdir(parents=True, exist_ok=True)
    (paths.current_task / "private.txt").write_text("no\n", encoding="utf-8")

    with pytest.raises(EvidenceResolutionError):
        resolve_evidence_ref(paths, "private.txt")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/web/test_evidence.py -q
```

Expected: FAIL because `agos.web.evidence` does not exist.

- [ ] **Step 3: Implement resolver**

Create `src/agos/web/evidence.py`:

```python
"""Safe evidence reference resolution for the local AGOS dashboard."""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from agos.core.repo import AgosPaths


class EvidenceResolutionError(ValueError):
    """Raised when a dashboard evidence ref is unsafe or unreadable."""


TASK_RELATIVE_ROOTS = {
    "ledger.jsonl",
    "status.json",
    "task.yaml",
    "proof.json",
    "proof.md",
    "execution",
    "evidence",
    "reviews",
    "orchestration",
}

TEXT_EXTENSIONS = {
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".patch",
    ".txt",
    ".yaml",
    ".yml",
}


def resolve_evidence_ref(paths: AgosPaths, ref: str) -> Path:
    """Resolve a dashboard evidence ref to an existing file inside the active AGOS task."""

    clean_ref = _normalize_ref(ref)
    parts = PurePosixPath(clean_ref).parts
    root = parts[0]

    if root in TASK_RELATIVE_ROOTS:
        candidate = paths.current_task.joinpath(*parts)
    else:
        candidate = paths.evidence.joinpath(*parts)

    resolved = candidate.resolve()
    current_root = paths.current_task.resolve()
    evidence_root = paths.evidence.resolve()
    if not _is_relative_to(resolved, current_root):
        raise EvidenceResolutionError(f"evidence ref escapes active task: {ref}")
    if root not in TASK_RELATIVE_ROOTS and not _is_relative_to(resolved, evidence_root):
        raise EvidenceResolutionError(f"bare evidence ref escapes evidence dir: {ref}")
    if not resolved.is_file():
        raise EvidenceResolutionError(f"evidence ref not found: {ref}")
    return resolved


def read_evidence_text(paths: AgosPaths, ref: str, *, max_bytes: int = 262_144) -> dict[str, object]:
    """Read a small text evidence file for the dashboard."""

    path = resolve_evidence_ref(paths, ref)
    suffix = path.suffix.lower()
    data = path.read_bytes()
    truncated = len(data) > max_bytes
    if suffix not in TEXT_EXTENSIONS:
        raise EvidenceResolutionError(f"evidence ref is not a supported text file: {ref}")
    text = data[:max_bytes].decode("utf-8", errors="replace")
    return {
        "ref": ref,
        "path": path.name,
        "text": text,
        "truncated": truncated,
        "size_bytes": len(data),
    }


def _normalize_ref(ref: str) -> str:
    value = ref.strip().replace("\\", "/")
    if not value:
        raise EvidenceResolutionError("evidence ref is empty")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise EvidenceResolutionError(f"absolute evidence ref is not allowed: {ref}")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise EvidenceResolutionError(f"path traversal is not allowed in evidence ref: {ref}")
    return posix.as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
```

- [ ] **Step 4: Run evidence tests**

Run:

```bash
python -m pytest tests/web/test_evidence.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/agos/web/evidence.py tests/web/test_evidence.py
git commit -m "feat: add safe dashboard evidence resolver"
```

---

### Task 3: Build read-only dashboard API payloads

**Files:**
- Create: `tests/web/test_api.py`
- Create: `src/agos/web/api.py`

- [ ] **Step 1: Write failing API payload tests**

Create `tests/web/test_api.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import yaml

from agos.core.adapter import ExecutorRun
from agos.core.config import AGOSConfig, WorkerConfig
from agos.core.execution import CandidatePatch, ExecutionPlan, ExecutionSubtask, ExecutionWorker
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.web.api import (
    config_payload,
    current_run_payload,
    evidence_payload,
    health_payload,
    runs_payload,
)


def _write_active_dashboard_fixture(repo: Path):
    paths = repo_paths(repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    config = AGOSConfig.default(
        executor="codex_cli",
        agent="codex",
        command="codex",
        workers={
            "docs_agent": WorkerConfig(type="codex_cli", command="codex", token="secret-token"),
        },
    )
    config.save(paths.agos_yaml)
    task = Task(
        id="agos-dashboard-01",
        title="构建可视化控制台",
        intent="展示 AGOS 流水线",
        workflow="feature",
        gates=["tests_pass"],
        executor=ExecutorBinding(adapter="codex_cli", agent="codex"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    started = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    locked = ledger.append(
        {
            "type": "gates_locked",
            "task_id": task.id,
            "gates": [{"id": "tests_pass", "stage": ["candidate"], "argv": ["pytest", "-q"]}],
        }
    )
    status = TaskStatus.for_started_task(
        task=task,
        run=ExecutorRun(adapter="codex_cli", run_id="run-01", issue_id=None),
        ledger_head_hash=locked["hash"],
    )
    save_status(status, paths)
    store = ExecutionStore(paths)
    plan = ExecutionPlan(
        id="plan-01",
        task_id=task.id,
        max_parallel=1,
        subtasks=[
            ExecutionSubtask(
                id="docs",
                title="文档节点",
                intent="更新 README",
                write_scope=["README.md"],
                worker=ExecutionWorker(adapter="docs_agent", role="docs_writer"),
                status="completed",
            )
        ],
    )
    store.write_plan(plan)
    store.write_subtask(plan.subtasks[0])
    patch_ref, patch_sha = store.write_candidate_patch("candidate-01", b"diff --git a/README.md b/README.md\n")
    store.write_candidate(
        CandidatePatch(
            id="candidate-01",
            task_id=task.id,
            subtask_id="docs",
            source_agent="docs_agent",
            workspace_ref="execution/workspaces/docs.json",
            patch_ref=patch_ref,
            patch_sha256=patch_sha,
            base_commit="abc123",
            summary="更新 README",
            status="tested",
        )
    )
    (paths.evidence / "gates").mkdir(parents=True, exist_ok=True)
    (paths.evidence / "gates" / "tests_pass.log").write_text("ok\n", encoding="utf-8")
    return paths, started


def test_health_payload_handles_uninitialized_repo(tmp_repo) -> None:
    payload = health_payload(tmp_repo)

    assert payload["ok"] is True
    assert payload["initialized"] is False
    assert payload["repo_root"] == str(tmp_repo)


def test_config_payload_redacts_sensitive_values(tmp_repo) -> None:
    _write_active_dashboard_fixture(tmp_repo)

    payload = config_payload(tmp_repo)

    worker = payload["config"]["workers"]["docs_agent"]
    assert worker["token"] == "***REDACTED***"
    assert worker["type"] == "codex_cli"


def test_runs_and_current_run_payload_include_pipeline_state(tmp_repo) -> None:
    _write_active_dashboard_fixture(tmp_repo)

    runs = runs_payload(tmp_repo)
    current = current_run_payload(tmp_repo)

    assert runs["runs"][0]["id"] == "agos-dashboard-01"
    assert runs["runs"][0]["title"] == "构建可视化控制台"
    assert current["run"]["task"]["workflow"] == "feature"
    assert current["run"]["execution"]["plan"]["id"] == "plan-01"
    assert current["run"]["execution"]["subtasks"][0]["worker"]["adapter"] == "docs_agent"
    assert current["run"]["candidates"][0]["id"] == "candidate-01"
    assert current["run"]["pipeline"]["candidates"]["count"] == 1


def test_evidence_payload_reads_safe_ref(tmp_repo) -> None:
    _write_active_dashboard_fixture(tmp_repo)

    payload = evidence_payload(tmp_repo, "evidence/gates/tests_pass.log")

    assert payload["ok"] is True
    assert payload["evidence"]["text"] == "ok\n"


def test_current_run_payload_is_json_serializable(tmp_repo) -> None:
    _write_active_dashboard_fixture(tmp_repo)

    encoded = json.dumps(current_run_payload(tmp_repo), ensure_ascii=False, sort_keys=True)

    assert "构建可视化控制台" in encoded
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m pytest tests/web/test_api.py -q
```

Expected: FAIL because `agos.web.api` does not exist.

- [ ] **Step 3: Implement API payload functions**

Create `src/agos/web/api.py`:

```python
"""Read-only dashboard API payload builders."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agos.core.config import load_config
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger, LedgerTamperError
from agos.core.merge_gate import verify_merge_gate
from agos.core.repo import AgosPaths, git_head, repo_paths
from agos.core.review_store import ReviewStore
from agos.core.status import load_status
from agos.core.task import load_task
from agos.web.evidence import EvidenceResolutionError, read_evidence_text


SENSITIVE_FIELD = re.compile(r"(token|secret|password|api_key|access_key|private_key)", re.IGNORECASE)


class DashboardApiError(RuntimeError):
    """Structured dashboard API error."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def payload(self) -> dict[str, object]:
        error: dict[str, object] = {"code": self.code, "message": self.message}
        if self.hint:
            error["hint"] = self.hint
        return {"ok": False, "error": error}


def health_payload(repo_root: Path) -> dict[str, object]:
    paths = repo_paths(repo_root)
    return {
        "ok": True,
        "service": "agos-dashboard",
        "repo_root": str(repo_root),
        "initialized": paths.agos_yaml.is_file(),
        "active_task": paths.task_yaml.is_file(),
        "git_head": _safe_git_head(repo_root),
    }


def config_payload(repo_root: Path) -> dict[str, object]:
    paths = _initialized_paths(repo_root)
    config = load_config(repo_root)
    return {
        "ok": True,
        "config": _redact(config.model_dump(mode="json")),
        "config_path": str(paths.agos_yaml),
    }


def status_payload(repo_root: Path) -> dict[str, object]:
    paths = _initialized_paths(repo_root)
    status = load_status(paths)
    return {
        "ok": True,
        "repo_root": str(repo_root),
        "initialized": True,
        "active_task": status is not None,
        "status": status.model_dump(mode="json") if status is not None else None,
        "git_head": _safe_git_head(repo_root),
        "ledger": _ledger_summary(paths),
    }


def runs_payload(repo_root: Path) -> dict[str, object]:
    paths = _initialized_paths(repo_root)
    status = load_status(paths)
    if status is None or not paths.task_yaml.is_file():
        return {"ok": True, "runs": []}
    task = load_task(paths.task_yaml)
    return {
        "ok": True,
        "runs": [
            {
                "id": task.id,
                "title": task.title,
                "workflow": task.workflow,
                "phase": status.phase,
                "ledger_head_hash": status.ledger_head_hash,
                "last_event_seq": status.last_event_seq,
                "current": True,
            }
        ],
    }


def current_run_payload(repo_root: Path) -> dict[str, object]:
    paths = _initialized_paths(repo_root)
    status = load_status(paths)
    if status is None or not paths.task_yaml.is_file():
        raise DashboardApiError("no_active_task", "当前仓库没有 active AGOS task", "运行 agos start")
    task = load_task(paths.task_yaml)
    ledger = ledger_payload(repo_root)["ledger"]
    execution = execution_payload(repo_root)["execution"]
    candidates = candidates_payload(repo_root)["candidates"]
    reviews = reviews_payload(repo_root)["reviews"]
    merge_gate = _merge_gate_summary(paths)
    return {
        "ok": True,
        "run": {
            "task": task.model_dump(mode="json"),
            "status": status.model_dump(mode="json"),
            "ledger": ledger,
            "execution": execution,
            "candidates": candidates,
            "reviews": reviews,
            "merge_gate": merge_gate,
            "pipeline": _pipeline_summary(execution, candidates, reviews, merge_gate),
            "distillation": _distillation_summary(paths),
        },
    }


def ledger_payload(repo_root: Path) -> dict[str, object]:
    paths = _initialized_paths(repo_root)
    return {"ok": True, "ledger": _ledger_summary(paths)}


def execution_payload(repo_root: Path) -> dict[str, object]:
    paths = _initialized_paths(repo_root)
    store = ExecutionStore(paths)
    if not store.plan_path.is_file():
        return {"ok": True, "execution": {"plan_present": False, "plan": None, "subtasks": []}}
    plan = store.read_plan()
    subtasks = []
    for subtask in plan.subtasks:
        try:
            persisted = store.read_subtask(subtask.id)
        except FileNotFoundError:
            persisted = subtask
        subtasks.append(persisted.model_dump(mode="json"))
    return {
        "ok": True,
        "execution": {
            "plan_present": True,
            "plan": plan.model_dump(mode="json"),
            "subtasks": subtasks,
        },
    }


def candidates_payload(repo_root: Path) -> dict[str, object]:
    paths = _initialized_paths(repo_root)
    store = ExecutionStore(paths)
    candidates = []
    for candidate in store.read_candidates():
        item = candidate.model_dump(mode="json")
        item["tests"] = [run.model_dump(mode="json") for run in store.read_test_runs(candidate.id)]
        item["decisions"] = [decision.model_dump(mode="json") for decision in store.read_decisions(candidate.id)]
        try:
            item["patch_exists"] = store.patch_path(candidate.patch_ref).is_file()
        except ValueError:
            item["patch_exists"] = False
        candidates.append(item)
    return {"ok": True, "candidates": candidates}


def reviews_payload(repo_root: Path) -> dict[str, object]:
    paths = _initialized_paths(repo_root)
    store = ReviewStore(paths)
    reports = [report.model_dump(mode="json") for report in store.read_reports()]
    packets = []
    if paths.reviews.exists():
        for packet_path in sorted(paths.reviews.glob("*/packet.json")):
            packets.append(_json_file_summary(packet_path, paths))
    return {"ok": True, "reviews": {"reports": reports, "packets": packets}}


def evidence_payload(repo_root: Path, ref: str) -> dict[str, object]:
    paths = _initialized_paths(repo_root)
    try:
        evidence = read_evidence_text(paths, ref)
    except EvidenceResolutionError as exc:
        raise DashboardApiError("invalid_evidence_ref", str(exc)) from exc
    return {"ok": True, "evidence": evidence}


def error_payload(exc: Exception) -> dict[str, object]:
    if isinstance(exc, DashboardApiError):
        return exc.payload()
    return {"ok": False, "error": {"code": "internal_error", "message": str(exc)}}


def _initialized_paths(repo_root: Path) -> AgosPaths:
    paths = repo_paths(repo_root)
    if not paths.agos_yaml.is_file():
        raise DashboardApiError("agos_not_initialized", "当前仓库尚未初始化 AGOS", "运行 agos init")
    return paths


def _ledger_summary(paths: AgosPaths) -> dict[str, object]:
    ledger = Ledger(paths.ledger)
    records = ledger.read_all()
    try:
        ledger.verify_chain()
        verified = True
        error = None
    except LedgerTamperError as exc:
        verified = False
        error = str(exc)
    return {
        "path": str(paths.ledger),
        "exists": paths.ledger.is_file(),
        "verified": verified,
        "error": error,
        "head_hash": ledger.head_hash(),
        "count": len(records),
        "records": records[-100:],
    }


def _merge_gate_summary(paths: AgosPaths) -> dict[str, object]:
    result = verify_merge_gate(paths, allow_missing_review=True)
    return result.model_dump(mode="json")


def _pipeline_summary(
    execution: dict[str, object],
    candidates: list[dict[str, object]],
    reviews: dict[str, object],
    merge_gate: dict[str, object],
) -> dict[str, object]:
    subtasks = execution.get("subtasks", [])
    reports = reviews.get("reports", [])
    return {
        "plan": {"state": "ready" if execution.get("plan_present") else "missing"},
        "workers": {"count": len(subtasks) if isinstance(subtasks, list) else 0},
        "candidates": {"count": len(candidates)},
        "review": {"count": len(reports) if isinstance(reports, list) else 0},
        "gate": {"passed": bool(merge_gate.get("passed"))},
    }


def _distillation_summary(paths: AgosPaths) -> dict[str, object]:
    records = Ledger(paths.ledger).read_all()
    useful_types = {
        "closeout_completed",
        "review_completed",
        "finding_opened",
        "finding_resolved",
        "candidate_decided",
        "candidate_apply_blocked",
    }
    lessons = [record for record in records if record.get("type") in useful_types]
    return {
        "records": lessons[-25:],
        "summary": "第一版展示 closeout、review、candidate decision 和失败模式记录，不自动改写 workflow 或 skill。",
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            redacted[key] = "***REDACTED***" if SENSITIVE_FIELD.search(str(key)) else _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _json_file_summary(path: Path, paths: AgosPaths) -> dict[str, object]:
    return {
        "ref": path.resolve().relative_to(paths.current_task.resolve()).as_posix(),
        "name": path.name,
        "size_bytes": path.stat().st_size,
    }


def _safe_git_head(repo_root: Path) -> str | None:
    try:
        return git_head(repo_root)
    except Exception:
        return None
```

- [ ] **Step 4: Run API tests**

Run:

```bash
python -m pytest tests/web/test_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/agos/web/api.py tests/web/test_api.py
git commit -m "feat: add read-only dashboard api payloads"
```

---

### Task 4: Add the local HTTP server

**Files:**
- Create: `tests/web/test_server.py`
- Create: `src/agos/web/server.py`

- [ ] **Step 1: Write failing HTTP server tests**

Create `tests/web/test_server.py`:

```python
from __future__ import annotations

import json
import threading
import urllib.request

from agos.web.server import create_dashboard_server


def test_dashboard_server_serves_static_index(tmp_repo) -> None:
    server = create_dashboard_server(tmp_repo, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8")
        assert "AGOS 控制台" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_server_serves_health_json(tmp_repo) -> None:
    server = create_dashboard_server(tmp_repo, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/health"
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["service"] == "agos-dashboard"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
```

- [ ] **Step 2: Run server tests to verify failure**

Run:

```bash
python -m pytest tests/web/test_server.py -q
```

Expected: FAIL because `agos.web.server` does not exist.

- [ ] **Step 3: Implement server routes**

Create `src/agos/web/server.py`:

```python
"""Local standard-library HTTP server for the AGOS dashboard."""
from __future__ import annotations

import json
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from agos.web.api import (
    config_payload,
    current_run_payload,
    error_payload,
    evidence_payload,
    health_payload,
    ledger_payload,
    status_payload,
    runs_payload,
    execution_payload,
    candidates_payload,
    reviews_payload,
)


class DashboardHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying AGOS repo context."""

    def __init__(self, server_address: tuple[str, int], handler, *, repo_root: Path) -> None:
        super().__init__(server_address, handler)
        self.repo_root = repo_root


def create_dashboard_server(repo_root: Path, *, host: str, port: int) -> DashboardHTTPServer:
    return DashboardHTTPServer((host, port), DashboardRequestHandler, repo_root=repo_root)


def serve_dashboard_forever(
    repo_root: Path,
    *,
    host: str,
    port: int,
    open_browser: bool,
) -> str:
    server = create_dashboard_server(repo_root, host=host, port=port)
    url = f"http://{host}:{server.server_port}"
    print(f"AGOS dashboard: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return url


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server: DashboardHTTPServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_html(_index_html())
            return
        if parsed.path.startswith("/api/"):
            self._send_json(self._api_payload(parsed.path, parse_qs(parsed.query)))
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args) -> None:
        return

    def _api_payload(self, path: str, query: dict[str, list[str]]) -> dict[str, object]:
        routes: dict[str, Callable[[Path], dict[str, object]]] = {
            "/api/health": health_payload,
            "/api/config": config_payload,
            "/api/status": status_payload,
            "/api/runs": runs_payload,
            "/api/runs/current": current_run_payload,
            "/api/runs/current/ledger": ledger_payload,
            "/api/runs/current/execution": execution_payload,
            "/api/runs/current/candidates": candidates_payload,
            "/api/runs/current/reviews": reviews_payload,
        }
        try:
            if path == "/api/runs/current/evidence":
                ref = query.get("ref", [""])[0]
                return evidence_payload(self.server.repo_root, ref)
            handler = routes.get(path)
            if handler is None:
                return {"ok": False, "error": {"code": "not_found", "message": path}}
            return handler(self.server.repo_root)
        except Exception as exc:
            return error_payload(exc)

    def _send_json(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _index_html() -> str:
    return resources.files("agos.web").joinpath("static", "index.html").read_text(encoding="utf-8")
```

- [ ] **Step 4: Run server tests**

Run:

```bash
python -m pytest tests/web/test_server.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/agos/web/server.py tests/web/test_server.py
git commit -m "feat: serve dashboard over local http"
```

---

### Task 5: Add the `agos dashboard` CLI command

**Files:**
- Create: `tests/cli/test_dashboard.py`
- Create: `src/agos/cli/cmd_dashboard.py`
- Modify: `src/agos/cli/main.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/cli/test_dashboard.py`:

```python
from __future__ import annotations

from typer.testing import CliRunner

from agos.cli.main import app


runner = CliRunner()


def test_dashboard_command_is_registered() -> None:
    result = runner.invoke(app, ["dashboard", "--help"])

    assert result.exit_code == 0
    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--open" in result.stdout


def test_dashboard_command_invokes_server(monkeypatch, tmp_repo) -> None:
    called = {}

    def fake_serve(repo_root, *, host: str, port: int, open_browser: bool) -> str:
        called["repo_root"] = repo_root
        called["host"] = host
        called["port"] = port
        called["open_browser"] = open_browser
        return "http://127.0.0.1:9999"

    monkeypatch.chdir(tmp_repo)
    monkeypatch.setattr("agos.cli.cmd_dashboard.serve_dashboard_forever", fake_serve)

    result = runner.invoke(app, ["dashboard", "--host", "127.0.0.1", "--port", "0", "--no-open"])

    assert result.exit_code == 0
    assert called["repo_root"] == tmp_repo
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 0
    assert called["open_browser"] is False
```

- [ ] **Step 2: Run CLI tests to verify failure**

Run:

```bash
python -m pytest tests/cli/test_dashboard.py -q
```

Expected: FAIL because the command is not registered.

- [ ] **Step 3: Implement CLI command**

Create `src/agos/cli/cmd_dashboard.py`:

```python
"""`agos dashboard` command."""
from __future__ import annotations

import typer

from agos.core.repo import find_repo_root
from agos.web.server import serve_dashboard_forever


def dashboard_command(
    host: str = typer.Option("127.0.0.1", "--host", help="Host interface to bind."),
    port: int = typer.Option(8788, "--port", min=0, help="Port to bind; 0 selects a free port."),
    open_browser: bool = typer.Option(False, "--open/--no-open", help="Open the dashboard URL in a browser."),
) -> None:
    """Start the local read-only AGOS dashboard."""

    try:
        repo_root = find_repo_root()
        serve_dashboard_forever(repo_root, host=host, port=port, open_browser=open_browser)
    except KeyboardInterrupt:
        raise typer.Exit(code=0) from None
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
```

- [ ] **Step 4: Register CLI command**

Modify `src/agos/cli/main.py` by adding the import:

```python
from agos.cli.cmd_dashboard import dashboard_command
```

Register the command near other top-level commands:

```python
app.command("dashboard")(dashboard_command)
```

- [ ] **Step 5: Run CLI tests**

Run:

```bash
python -m pytest tests/cli/test_dashboard.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/agos/cli/cmd_dashboard.py src/agos/cli/main.py tests/cli/test_dashboard.py
git commit -m "feat: add dashboard cli command"
```

---

### Task 6: Replace the static shell with the Chinese dashboard UI

**Files:**
- Modify: `src/agos/web/static/index.html`
- Modify: `tests/web/test_static_resources.py`

- [ ] **Step 1: Extend static-resource test for real UI anchors**

Modify `tests/web/test_static_resources.py`:

```python
from __future__ import annotations

from importlib import resources


def test_dashboard_static_index_is_packaged() -> None:
    index = resources.files("agos.web").joinpath("static", "index.html")

    text = index.read_text(encoding="utf-8")

    assert '<main id="app">' in text
    assert "AGOS 控制台" in text
    assert "data-agos-dashboard" in text
    assert "任务批次" in text
    assert "Subagent 节点" in text
    assert "证据文件" in text
    assert "自我蒸馏" in text
    assert "fetchJson('/api/runs/current')" in text
```

- [ ] **Step 2: Run static-resource test to verify failure**

Run:

```bash
python -m pytest tests/web/test_static_resources.py -q
```

Expected: FAIL because the initial shell lacks the full UI anchors.

- [ ] **Step 3: Implement the full single-page UI**

Replace `src/agos/web/static/index.html` with a vanilla HTML page containing these fixed IDs and data flow:

```html
<!doctype html>
<html lang="zh-CN" data-agos-dashboard>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AGOS 控制台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef2f7;
      --panel: #ffffff;
      --line: #dbe3ef;
      --text: #0f172a;
      --muted: #64748b;
      --blue: #2563eb;
      --green: #16a34a;
      --red: #dc2626;
      --gray: #64748b;
      --amber: #d97706;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    header {
      padding: 18px 24px;
      background: #0f172a;
      color: white;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    header h1 { margin: 0; font-size: 22px; }
    header p { margin: 4px 0 0; color: #cbd5e1; }
    button {
      border: 1px solid var(--line);
      background: white;
      color: var(--text);
      border-radius: 10px;
      padding: 8px 12px;
      cursor: pointer;
      font-weight: 600;
    }
    button.primary { background: var(--blue); color: white; border-color: var(--blue); }
    button:disabled { opacity: .45; cursor: not-allowed; }
    #app {
      display: grid;
      grid-template-columns: 310px minmax(0, 1fr);
      gap: 16px;
      padding: 16px;
      min-height: calc(100vh - 82px);
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 12px 32px rgba(15, 23, 42, .06);
    }
    .sidebar { padding: 14px; overflow: auto; }
    .content { padding: 16px; overflow: auto; }
    .section { margin-top: 16px; }
    .section h2, .section h3 { margin: 0 0 10px; }
    .muted { color: var(--muted); }
    .run-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      margin-top: 10px;
      background: #f8fafc;
    }
    .run-card.active { border-color: var(--blue); background: #eff6ff; }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 700;
      background: #e2e8f0;
      color: #334155;
    }
    .badge.pass { background: #dcfce7; color: #166534; }
    .badge.block { background: #fee2e2; color: #991b1b; }
    .badge.running { background: #dbeafe; color: #1d4ed8; }
    .badge.warn { background: #fef3c7; color: #92400e; }
    .grid-5 { display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; }
    .metric { border: 1px solid var(--line); border-radius: 12px; padding: 12px; background: #f8fafc; }
    .metric strong { display: block; font-size: 20px; margin-top: 6px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border-bottom: 1px solid var(--line); padding: 9px 8px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; }
    pre {
      margin: 0;
      padding: 12px;
      border-radius: 12px;
      background: #0f172a;
      color: #e2e8f0;
      overflow: auto;
      max-height: 360px;
    }
    .tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
    .empty { padding: 22px; text-align: center; color: var(--muted); border: 1px dashed var(--line); border-radius: 12px; }
    .error { background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; padding: 10px 12px; border-radius: 12px; margin-bottom: 12px; }
    @media (max-width: 980px) {
      #app { grid-template-columns: 1fr; }
      .grid-5 { grid-template-columns: repeat(2, minmax(120px, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>AGOS 控制台</h1>
      <p>本地只读 Dashboard：业务 workflow → subagent 节点 → evidence → merge-gate → 自我蒸馏</p>
    </div>
    <button class="primary" onclick="refreshAll()">刷新</button>
  </header>

  <main id="app">
    <aside class="panel sidebar">
      <h2>任务批次 / Runs</h2>
      <p class="muted" id="repo-root">正在读取仓库。</p>
      <div id="run-list" class="section"></div>
    </aside>

    <section class="panel content">
      <div id="error-box"></div>
      <div id="run-header" class="section"></div>
      <div id="pipeline" class="section"></div>
      <div id="subagents" class="section"></div>
      <div id="candidates" class="section"></div>
      <div id="reviews" class="section"></div>
      <div id="merge-gate" class="section"></div>
      <div id="evidence" class="section"></div>
      <div id="distillation" class="section"></div>
    </section>
  </main>

  <script>
    const state = { health: null, runs: [], current: null, selectedEvidence: null };

    async function fetchJson(path) {
      const response = await fetch(path, { cache: 'no-store' });
      return await response.json();
    }

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
    }

    function badge(text, kind) {
      return `<span class="badge ${kind || ''}">${escapeHtml(text)}</span>`;
    }

    function showError(message) {
      document.getElementById('error-box').innerHTML = message ? `<div class="error">${escapeHtml(message)}</div>` : '';
    }

    async function refreshAll() {
      showError('');
      const health = await fetchJson('/api/health');
      state.health = health;
      document.getElementById('repo-root').textContent = health.repo_root || '未知仓库';
      const runs = await fetchJson('/api/runs');
      if (!runs.ok) {
        state.runs = [];
        renderRuns();
        renderNoActive(runs.error);
        return;
      }
      state.runs = runs.runs || [];
      renderRuns();
      await refreshSelected();
    }

    async function refreshSelected() {
      const current = await fetchJson('/api/runs/current');
      if (!current.ok) {
        state.current = null;
        renderNoActive(current.error);
        return;
      }
      state.current = current.run;
      renderCurrent();
    }

    function renderRuns() {
      const target = document.getElementById('run-list');
      if (!state.runs.length) {
        target.innerHTML = '<div class="empty">当前没有 active AGOS task。</div>';
        return;
      }
      target.innerHTML = state.runs.map(run => `
        <div class="run-card active">
          <strong>${escapeHtml(run.title)}</strong>
          <p class="muted">${escapeHtml(run.id)}</p>
          <p>${badge(run.phase, run.phase === 'done' ? 'pass' : 'running')} ${badge(run.workflow, '')}</p>
        </div>
      `).join('');
    }

    function renderNoActive(error) {
      document.getElementById('run-header').innerHTML = `
        <h2>没有可展示的 active run</h2>
        <p class="muted">${escapeHtml(error?.message || '请先初始化并启动 AGOS task。')}</p>
        <pre>${escapeHtml(error?.hint || 'agos init\\nagos start --title \"Your task\"')}</pre>
      `;
      for (const id of ['pipeline','subagents','candidates','reviews','merge-gate','evidence','distillation']) {
        document.getElementById(id).innerHTML = '';
      }
    }

    function renderCurrent() {
      const run = state.current;
      const task = run.task;
      const status = run.status;
      document.getElementById('run-header').innerHTML = `
        <h2>${escapeHtml(task.title)}</h2>
        <p class="muted">task: ${escapeHtml(task.id)} · workflow: ${escapeHtml(task.workflow)} · phase: ${escapeHtml(status.phase)}</p>
        <p class="muted">ledger head: ${escapeHtml(status.ledger_head_hash || '')}</p>
        <div class="tabs">
          <button disabled>生成计划</button>
          <button disabled>执行 dry-run</button>
          <button disabled>触发审查</button>
          <button disabled>验证 merge-gate</button>
        </div>
      `;
      renderPipeline(run.pipeline);
      renderSubagents(run.execution);
      renderCandidates(run.candidates);
      renderReviews(run.reviews);
      renderMergeGate(run.merge_gate);
      renderEvidence(run);
      renderDistillation(run.distillation);
    }

    function renderPipeline(pipeline) {
      document.getElementById('pipeline').innerHTML = `
        <h3>Pipeline 摘要</h3>
        <div class="grid-5">
          <div class="metric">Plan<strong>${escapeHtml(pipeline.plan.state)}</strong></div>
          <div class="metric">Workers<strong>${escapeHtml(pipeline.workers.count)}</strong></div>
          <div class="metric">Candidates<strong>${escapeHtml(pipeline.candidates.count)}</strong></div>
          <div class="metric">Review<strong>${escapeHtml(pipeline.review.count)}</strong></div>
          <div class="metric">Gate<strong>${pipeline.gate.passed ? 'pass' : 'block'}</strong></div>
        </div>
      `;
    }

    function renderSubagents(execution) {
      const subtasks = execution.subtasks || [];
      document.getElementById('subagents').innerHTML = `
        <h3>Subagent 节点</h3>
        ${subtasks.length ? `
          <table>
            <thead><tr><th>节点</th><th>Worker</th><th>状态</th><th>依赖</th><th>写入范围</th></tr></thead>
            <tbody>
              ${subtasks.map(item => `
                <tr>
                  <td><strong>${escapeHtml(item.id)}</strong><br>${escapeHtml(item.title)}</td>
                  <td>${escapeHtml(item.worker?.adapter)}<br><span class="muted">${escapeHtml(item.worker?.role)}</span></td>
                  <td>${badge(item.status, item.status === 'completed' ? 'pass' : 'running')}</td>
                  <td>${escapeHtml((item.depends_on || []).join(', ') || '-')}</td>
                  <td>${escapeHtml((item.write_scope || []).join(', '))}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        ` : '<div class="empty">尚未生成 execution plan。</div>'}
      `;
    }

    function renderCandidates(candidates) {
      document.getElementById('candidates').innerHTML = `
        <h3>Candidate Patches</h3>
        ${candidates.length ? `
          <table>
            <thead><tr><th>ID</th><th>Subtask</th><th>状态</th><th>Patch</th><th>测试</th></tr></thead>
            <tbody>
              ${candidates.map(item => `
                <tr>
                  <td>${escapeHtml(item.id)}</td>
                  <td>${escapeHtml(item.subtask_id)}</td>
                  <td>${badge(item.status, item.status === 'applied' ? 'pass' : 'warn')}</td>
                  <td><button onclick="loadEvidence('${escapeHtml(item.patch_ref)}')">${escapeHtml(item.patch_ref)}</button></td>
                  <td>${escapeHtml((item.tests || []).map(test => `${test.gate_id}:${test.state}`).join(', ') || '-')}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        ` : '<div class="empty">暂无 candidate。</div>'}
      `;
    }

    function renderReviews(reviews) {
      const reports = reviews.reports || [];
      document.getElementById('reviews').innerHTML = `
        <h3>Reviews</h3>
        ${reports.length ? `
          <table>
            <thead><tr><th>Review</th><th>Findings</th><th>Blocking</th></tr></thead>
            <tbody>
              ${reports.map(report => {
                const blocking = (report.findings || []).filter(item => item.blocking && item.status === 'open').length;
                return `<tr><td>${escapeHtml(report.review_id)}</td><td>${escapeHtml((report.findings || []).length)}</td><td>${badge(blocking, blocking ? 'block' : 'pass')}</td></tr>`;
              }).join('')}
            </tbody>
          </table>
        ` : '<div class="empty">暂无 review report。</div>'}
      `;
    }

    function renderMergeGate(gate) {
      const checks = gate.checks || [];
      document.getElementById('merge-gate').innerHTML = `
        <h3>Merge Gate</h3>
        <p>${badge(gate.passed ? 'passed' : 'blocked', gate.passed ? 'pass' : 'block')}</p>
        <table>
          <thead><tr><th>Check</th><th>状态</th><th>消息</th></tr></thead>
          <tbody>${checks.map(check => `<tr><td>${escapeHtml(check.name)}</td><td>${badge(check.state, check.state)}</td><td>${escapeHtml(check.message)}</td></tr>`).join('')}</tbody>
        </table>
      `;
    }

    function renderEvidence(run) {
      const refs = ['ledger.jsonl'];
      for (const candidate of run.candidates || []) refs.push(candidate.patch_ref);
      const buttons = refs.map(ref => `<button onclick="loadEvidence('${escapeHtml(ref)}')">${escapeHtml(ref)}</button>`).join('');
      document.getElementById('evidence').innerHTML = `
        <h3>证据文件</h3>
        <div class="tabs">${buttons}</div>
        <pre id="evidence-viewer">选择一个 evidence ref 查看内容。</pre>
      `;
    }

    async function loadEvidence(ref) {
      const payload = await fetchJson('/api/runs/current/evidence?ref=' + encodeURIComponent(ref));
      const viewer = document.getElementById('evidence-viewer');
      if (!payload.ok) {
        viewer.textContent = payload.error?.message || '读取失败';
        return;
      }
      viewer.textContent = payload.evidence.text;
    }

    function renderDistillation(distillation) {
      document.getElementById('distillation').innerHTML = `
        <h3>自我蒸馏</h3>
        <p class="muted">${escapeHtml(distillation.summary)}</p>
        <pre>${escapeHtml(JSON.stringify(distillation.records || [], null, 2))}</pre>
      `;
    }

    refreshAll().catch(error => showError(error.message));
    setInterval(() => refreshAll().catch(error => showError(error.message)), 5000);
  </script>
</body>
</html>
```

- [ ] **Step 4: Run static-resource test**

Run:

```bash
python -m pytest tests/web/test_static_resources.py -q
```

Expected: PASS.

- [ ] **Step 5: Run server static smoke test**

Run:

```bash
python -m pytest tests/web/test_server.py::test_dashboard_server_serves_static_index -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 6**

```bash
git add src/agos/web/static/index.html tests/web/test_static_resources.py
git commit -m "feat: render chinese dashboard ui"
```

---

### Task 7: Document the dashboard in README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README dashboard section**

Add a new table-of-contents item after “工作模型”:

```markdown
- [可视化控制台](#可视化控制台)
```

Add this section after “工作模型”:

```markdown
## 可视化控制台

AGOS 提供本地只读 Dashboard，用浏览器展示当前 `.agos/` 治理状态：

```bash
agos dashboard --port 0 --open
```

默认行为：

- 绑定 `127.0.0.1`，不对外暴露。
- 第一版只读展示，不执行会修改仓库的 action。
- 左侧展示当前 AGOS run，右侧展示 workflow、subagent 节点、candidate、review、merge-gate、ledger evidence 和自我蒸馏摘要。
- evidence viewer 只允许读取 `.agos/tasks/current` 中被允许的 task/evidence refs，拒绝路径穿越和任意文件读取。

常用命令：

```bash
agos dashboard
agos dashboard --host 127.0.0.1 --port 8788
agos dashboard --port 0 --no-open
```

如果页面提示尚未初始化，请先运行：

```bash
agos init
agos start --title "Your task"
```
```

- [ ] **Step 2: Run README grep check**

Run:

```bash
python - <<'PY'
from pathlib import Path
text = Path("README.md").read_text(encoding="utf-8")
assert "## 可视化控制台" in text
assert "agos dashboard --port 0 --open" in text
assert "只读 Dashboard" in text
PY
```

Expected: command exits 0.

- [ ] **Step 3: Commit Task 7**

```bash
git add README.md
git commit -m "docs: document dashboard usage"
```

---

### Task 8: Full local verification and packaging check

**Files:**
- No new files.

- [ ] **Step 1: Run focused dashboard tests**

Run:

```bash
python -m pytest tests/web tests/cli/test_dashboard.py -q
```

Expected: all dashboard tests PASS.

- [ ] **Step 2: Run compile check**

Run:

```bash
python -m compileall -q src tests
```

Expected: exit code 0.

- [ ] **Step 3: Run full pytest**

Run:

```bash
python -m pytest -q
```

Expected: all tests PASS or only documented opt-in integration tests are skipped.

- [ ] **Step 4: Run package build**

Run:

```bash
python -m build
```

Expected: source distribution and wheel are created under `dist/`.

- [ ] **Step 5: Verify built wheel contains static dashboard HTML**

Run:

```bash
python - <<'PY'
from pathlib import Path
import zipfile

wheels = sorted(Path("dist").glob("agos-*.whl"))
assert wheels, "wheel not found"
with zipfile.ZipFile(wheels[-1]) as zf:
    names = set(zf.namelist())
assert "agos/web/static/index.html" in names
PY
```

Expected: command exits 0.

- [ ] **Step 6: Commit verification fixes if any**

If a verification command fails, fix only the failing dashboard-related issue, rerun the failing command, then commit the fix:

```bash
git add src tests README.md pyproject.toml
git commit -m "fix: stabilize dashboard verification"
```

Skip this commit when no files changed.

---

## Self-Review

### Spec coverage

- Local dashboard command: Task 5.
- Static Chinese UI with Input-Kanban-like left runs and right details: Task 6.
- Read-only JSON APIs: Tasks 3 and 4.
- `.agos` status, ledger, execution, candidate, review, merge-gate summaries: Task 3.
- Safe evidence refs and no arbitrary file read: Task 2.
- Default local binding and `--port 0 --open`: Tasks 4 and 5.
- Static packaging and wheel inclusion: Tasks 1 and 8.
- README usage tutorial update: Task 7.
- Verification commands: Task 8.

### Non-goals kept out

- No mutating action API.
- No hosted service.
- No arbitrary file browser.
- No automatic skill or workflow rewriting.
- No DAG graph renderer in the first implementation.

### Implementation constraints

- Use standard-library HTTP serving to avoid new runtime dependencies.
- Keep API functions independent of `BaseHTTPRequestHandler` so they can be unit tested directly.
- Preserve `.agos` as source of truth; the dashboard only reads and summarizes.
- Keep frontend buildless so packaging and local use remain simple.
