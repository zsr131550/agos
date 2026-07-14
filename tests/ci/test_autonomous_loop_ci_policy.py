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


def test_default_ci_is_offline_and_real_agent_smokes_are_opt_in() -> None:
    ci_path = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    ci_text = ci_path.read_text(encoding="utf-8")
    ci = yaml.safe_load(ci_text)

    assert "autonomous-readiness" in ci["jobs"]
    assert "real-agent-smoke" not in ci["jobs"]
    assert "--allow-missing-review" not in ci_text
    for token in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MULTICA_API_KEY",
        "AGOS_OPENHANDS_TOKEN",
    ):
        assert token not in ci_text

    smoke_path = PROJECT_ROOT / ".github" / "workflows" / "real-agent-smoke.yml"
    smoke_text = smoke_path.read_text(encoding="utf-8")
    smoke = yaml.safe_load(smoke_text)
    triggers = smoke.get("on", smoke.get(True))
    assert "workflow_dispatch" in triggers
    assert "schedule" in triggers

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
