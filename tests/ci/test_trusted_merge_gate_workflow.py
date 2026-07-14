from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> tuple[dict, str]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def _job_text(job: dict) -> str:
    return yaml.safe_dump(job, sort_keys=False)


def test_pr_jobs_checkout_protected_base_and_subject_separately() -> None:
    workflow, _text = _workflow()

    for job_name in ("agos-prepare", "merge-gate"):
        job = workflow["jobs"][job_name]
        checkouts = [
            step
            for step in job["steps"]
            if step.get("uses") == "actions/checkout@v4"
        ]
        checkout_pairs = {
            (step.get("with", {}).get("ref"), step.get("with", {}).get("path"))
            for step in checkouts
        }
        assert (
            "${{ github.event.pull_request.base.sha }}",
            "trusted",
        ) in checkout_pairs
        assert (
            "${{ github.event.pull_request.head.sha }}",
            "subject",
        ) in checkout_pairs
        assert all(step.get("with", {}).get("fetch-depth") == 0 for step in checkouts)


def test_pr_jobs_install_and_configure_only_from_protected_base() -> None:
    workflow, _text = _workflow()

    for job_name in ("agos-prepare", "merge-gate"):
        text = _job_text(workflow["jobs"][job_name])
        assert "./trusted[dev]" in text
        assert "--trusted-config" in text
        assert "$GITHUB_WORKSPACE/trusted/.agos/agos.yaml" in text
        assert "pip install -e \".[dev]\"" not in text
        assert "pip install -e \"./subject" not in text
        assert "if [ ! -f .agos/agos.yaml ]" not in text


def test_pr_jobs_exchange_only_subject_task_evidence() -> None:
    workflow, _text = _workflow()
    prepare_text = _job_text(workflow["jobs"]["agos-prepare"])
    merge_text = _job_text(workflow["jobs"]["merge-gate"])

    assert "subject/.agos/tasks/current" in prepare_text
    assert "subject/.agos/tasks/current" in merge_text
    assert "path: .agos/tasks/current" not in prepare_text
    assert "path: .agos/tasks/current" not in merge_text


def test_pr_merge_gate_jobs_have_no_model_provider_secrets() -> None:
    workflow, _text = _workflow()
    jobs_text = "\n".join(
        _job_text(workflow["jobs"][name]) for name in ("agos-prepare", "merge-gate")
    )

    for token in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MULTICA_API_KEY",
        "AGOS_OPENHANDS_TOKEN",
    ):
        assert token not in jobs_text
