from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_repository_autonomous_config_enables_planner_multi_worker_and_required_reviewer() -> None:
    config_path = PROJECT_ROOT / ".agos" / "agos.yaml"
    assert config_path.is_file(), "repository must track an AGOS governance config"

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    planner = config.get("orchestration", {}).get("planner", {})
    workers = config.get("workers", {})
    reviewers = config.get("reviewers", {})

    assert planner.get("enabled") is True
    assert planner.get("executor") in {"codex_cli", "claude_code"}
    assert len(workers) >= 2
    assert config.get("orchestration", {}).get("max_parallel", 1) >= 2
    assert any(
        reviewer.get("required") is True
        and reviewer.get("type") in {"codex_cli", "claude_code"}
        for reviewer in reviewers.values()
    )


def test_ci_policy_runs_autonomous_readiness_and_real_agent_smokes_without_missing_review_escape() -> None:
    workflow_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    jobs = workflow["jobs"]

    assert "autonomous-readiness" in jobs
    assert "real-agent-smoke" in jobs
    assert "--allow-missing-review" not in workflow_text

    smoke_text = yaml.safe_dump(jobs["real-agent-smoke"], sort_keys=False)
    required_tokens = [
        "AGOS_PLANNER_SMOKE",
        "AGOS_REVIEWER_SMOKE",
        "AGOS_CODEX_WORKER_SMOKE",
        "AGOS_CLAUDE_WORKER_SMOKE",
        "AGOS_MULTICA_WORKER_SMOKE",
        "AGOS_OPENHANDS_WORKER_SMOKE",
        "tests/integration/test_planner_cli_opt_in.py",
        "tests/integration/test_reviewer_cli_opt_in.py",
        "tests/integration/test_worker_adapters_opt_in.py",
    ]
    for token in required_tokens:
        assert token in smoke_text
