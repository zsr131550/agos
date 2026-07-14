from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from rich.text import Text
import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger
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
    assert candidates[0].status == "tested"
    assert candidates[0].provenance is not None
    assert candidates[0].provenance.source == "ci_reconstructed"
    assert candidates[0].review_refs == []
    assert candidates[0].decision_ref is None
    assert store.read_decisions(candidates[0].id) == []
    assert not any(
        record["type"]
        in {"candidate_review_completed", "candidate_decision_recorded", "candidate_applied"}
        for record in Ledger(paths.ledger).read_all()
    )
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
    assert payload["provenance_state"] == "unprovenanced"

    required_gate = runner.invoke(
        app,
        [
            "merge-gate",
            "--json",
            "--provenance-policy",
            "required",
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

    assert required_gate.exit_code == 1
    required_payload = json.loads(required_gate.stdout)
    assert required_payload["provenance_state"] == "unprovenanced"
    provenance = next(
        check for check in required_payload["checks"] if check["name"] == "provenance"
    )
    assert provenance["state"] == "block"
    assert "ci_reconstructed" in "; ".join(provenance["details"])


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
    help_text = Text.from_ansi(result.stdout).plain

    assert result.exit_code == 0
    assert "--trusted-config" in help_text


def test_prepare_merge_gate_uses_trusted_config_instead_of_subject_config(
    monkeypatch,
    tmp_path,
):
    _init_repo(tmp_path, gate_assertion="# changed")
    (tmp_path / "README.md").write_text("# changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "change"], cwd=tmp_path, check=True)
    base = _git(tmp_path, "rev-parse", "HEAD~1")
    head = _git(tmp_path, "rev-parse", "HEAD")
    trusted_config = tmp_path.parent / "trusted" / ".agos" / "agos.yaml"
    trusted_config.parent.mkdir(parents=True)
    trusted_config.write_text(
        (tmp_path / ".agos" / "agos.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / ".agos" / "agos.yaml").write_text("not: [valid", encoding="utf-8")
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
            "--trusted-config",
            str(trusted_config),
        ],
    )

    assert prepare.exit_code == 0, prepare.stderr
    candidate = ExecutionStore(repo_paths(tmp_path)).read_candidates()[0]
    assert candidate.provenance is not None
    assert candidate.provenance.source == "ci_reconstructed"


def test_prepare_merge_gate_missing_trusted_config_does_not_fall_back(
    monkeypatch,
    tmp_path,
):
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
            "--trusted-config",
            str(tmp_path.parent / "missing" / "agos.yaml"),
        ],
    )

    assert prepare.exit_code == 1
    assert "No such file" in prepare.stderr or "not found" in prepare.stderr
