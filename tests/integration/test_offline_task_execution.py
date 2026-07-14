from __future__ import annotations

import json
import sys

import pytest
import yaml
from typer.testing import CliRunner

from agos.cli.main import app
from agos.core.execution_store import ExecutionStore
from agos.core.ledger import Ledger
from agos.core.merge_gate import verify_merge_gate
from agos.core.repo import repo_paths


pytestmark = pytest.mark.integration
runner = CliRunner()


def _write_offline_config(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "unused"},
                "default_workflow": "feature",
                "task_execution": {
                    "mode": "candidate",
                    "output_contract": "source_code",
                },
                "workers": {
                    "offline": {
                        "type": "command",
                        "argv": [
                            sys.executable,
                            "-c",
                            (
                                "from pathlib import Path; "
                                "Path('README.md').write_text('# offline candidate\\n', "
                                "encoding='utf-8')"
                            ),
                        ],
                        "timeout_seconds": 10,
                    }
                },
                "reviewers": {
                    "clean": {
                        "type": "fake",
                        "role": "code_review",
                        "required": True,
                    }
                },
                "allow_fake_reviewer": True,
                "orchestration": {
                    "backend": "native_async",
                    "max_parallel": 1,
                    "max_tick_iterations": 4,
                    "fallback_write_scope": ["README.md"],
                    "planner": {"enabled": False},
                },
                "workflows": {
                    "feature": {
                        "gates": [
                            {
                                "id": "readme_changed",
                                "stage": ["candidate"],
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    (
                                        "from pathlib import Path; "
                                        "assert Path('README.md').read_text(encoding='utf-8') "
                                        "== '# offline candidate\\n'"
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


def test_offline_candidate_start_runs_guarded_closed_loop(monkeypatch, tmp_repo) -> None:
    _write_offline_config(tmp_repo)
    monkeypatch.chdir(tmp_repo)

    def unexpected_provider_call(*_args, **_kwargs):
        raise AssertionError("offline candidate execution contacted a provider")

    monkeypatch.setattr("agos.cli.executor_registry.MulticaAdapter.start", unexpected_provider_call)
    monkeypatch.setattr(
        "agos.cli.task_execution_registry.configured_planner_adapter",
        unexpected_provider_call,
    )

    started = runner.invoke(
        app,
        [
            "start",
            "--title",
            "Apply a deterministic offline patch",
            "--mode",
            "candidate",
            "--json",
        ],
    )

    assert started.exit_code == 0, started.stderr
    payload = json.loads(started.stdout)
    assert payload["mode"] == "candidate"
    assert payload["state"] == "completed"
    assert payload["candidate_ids"] == payload["applied_candidate_ids"]
    assert payload["candidate_ids"]
    assert (tmp_repo / "README.md").read_text(encoding="utf-8") == "# offline candidate\n"
    assert not (tmp_repo / "outputs" / payload["task_id"]).exists()

    paths = repo_paths(tmp_repo)
    store = ExecutionStore(paths)
    candidates = store.read_candidates()
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.id == payload["candidate_ids"][0]
    assert candidate.status == "applied"
    assert candidate.provenance.source == "worker_export"
    assert store.patch_path(candidate.patch_ref).read_bytes().startswith(b"diff --git")

    test_runs = store.read_test_runs(candidate.id)
    assert {run.gate_id for run in test_runs} == {"patch_applies", "readme_changed"}
    assert all(run.state == "passed" for run in test_runs)
    assert candidate.review_refs[-1].state == "completed"
    assert candidate.review_refs[-1].report_ref is not None
    decisions = store.read_decisions(candidate.id)
    assert decisions[-1].decision == "accepted"

    ledger = Ledger(paths.ledger)
    ledger.verify_chain()
    event_types = [record["type"] for record in ledger.read_all()]
    assert "candidate_patch_created" in event_types
    assert "candidate_review_completed" in event_types
    assert "candidate_decision_recorded" in event_types
    assert "candidate_applied" in event_types
    assert event_types[-1] == "task_execution_completed"

    merge_gate = verify_merge_gate(paths)
    assert merge_gate.passed is True
    assert all(check.state == "pass" for check in merge_gate.checks)
