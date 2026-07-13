from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.execution_store import ExecutionStore
from agos.core.repo import repo_paths


runner = CliRunner()


def _init_repo(repo: Path, *, gate_assertion: str) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "ci"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=repo, check=True)
    (repo / "README.md").write_text("# base\n", encoding="utf-8")
    (repo / ".agos").mkdir()
    (repo / ".agos" / "agos.yaml").write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "default_workflow": "feature",
                "workflows": {
                    "feature": {
                        "gates": [
                            {
                                "id": "tests_pass",
                                "stage": ["candidate"],
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    (
                                        "from pathlib import Path; "
                                        f"assert Path('README.md').read_text().startswith({gate_assertion!r})"
                                    ),
                                ],
                            }
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "README.md", ".agos/agos.yaml"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def test_prepare_merge_gate_builds_candidate_evidence_that_merge_gate_accepts(monkeypatch, tmp_path):
    _init_repo(tmp_path, gate_assertion="# changed")
    (tmp_path / "README.md").write_text("# changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=tmp_path, check=True)
    base = _git(tmp_path, "rev-parse", "HEAD~1")
    head = _git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)

    prepare = runner.invoke(
        app,
        [
            "prepare-merge-gate",
            "--base",
            base,
            "--head",
            head,
            "--anchor-path",
            ".agos/tasks/current/evidence/anchors.json",
            "--issuer",
            "github-actions",
        ],
    )

    assert prepare.exit_code == 0, prepare.stderr
    paths = repo_paths(tmp_path)
    store = ExecutionStore(paths)
    candidates = store.read_candidates()
    assert len(candidates) == 1
    assert candidates[0].status == "applied"
    assert candidates[0].review_refs
    assert candidates[0].review_refs[-1].state == "completed"
    assert candidates[0].decision_ref is not None
    decisions = store.read_decisions(candidates[0].id)
    assert len(decisions) == 1
    assert decisions[0].decision == "accepted"
    required_refs = {
        candidates[0].patch_ref,
        *candidates[0].test_refs,
        candidates[0].review_refs[-1].report_ref,
    }
    assert required_refs <= set(decisions[0].evidence_refs)
    assert (paths.current_task / "evidence" / "anchors.json").is_file()

    gate = runner.invoke(
        app,
        [
            "merge-gate",
            "--json",
            "--require-anchor",
            "--anchor-backend",
            "file",
            "--anchor-path",
            ".agos/tasks/current/evidence/anchors.json",
            "--base",
            base,
            "--head",
            head,
        ],
    )

    assert gate.exit_code == 0, gate.stderr
    payload = json.loads(gate.stdout)
    assert payload["passed"] is True


def test_prepare_merge_gate_fails_closed_when_candidate_gate_fails(monkeypatch, tmp_path):
    _init_repo(tmp_path, gate_assertion="# expected-other")
    (tmp_path / "README.md").write_text("# changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=tmp_path, check=True)
    base = _git(tmp_path, "rev-parse", "HEAD~1")
    head = _git(tmp_path, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)

    prepare = runner.invoke(
        app,
        [
            "prepare-merge-gate",
            "--base",
            base,
            "--head",
            head,
            "--anchor-path",
            ".agos/tasks/current/evidence/anchors.json",
            "--issuer",
            "github-actions",
        ],
    )

    assert prepare.exit_code == 1
    assert "tests_pass" in prepare.stderr
    assert not (tmp_path / ".agos" / "tasks" / "current" / "evidence" / "anchors.json").exists()


def test_prepare_merge_gate_help_is_exposed():
    result = runner.invoke(app, ["prepare-merge-gate", "--help"])

    assert result.exit_code == 0
