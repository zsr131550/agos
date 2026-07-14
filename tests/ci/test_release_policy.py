from __future__ import annotations

from pathlib import Path
import tomllib

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True, {}))


def test_repository_contains_complete_mit_license_and_package_metadata() -> None:
    license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "MIT License" in license_text
    assert "Copyright (c) 2026 AGOS project" in license_text
    assert "Permission is hereby granted, free of charge" in license_text
    assert "THE SOFTWARE IS PROVIDED \"AS IS\"" in license_text
    assert metadata["project"]["license"] == "MIT"
    assert metadata["project"]["license-files"] == ["LICENSE"]


def test_dev_extra_contains_no_isolation_build_backend() -> None:
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    build_requirements = set(metadata["build-system"]["requires"])
    dev_requirements = set(metadata["project"]["optional-dependencies"]["dev"])
    assert build_requirements <= dev_requirements


def test_dependabot_covers_python_and_github_actions_monthly() -> None:
    config = _yaml(PROJECT_ROOT / ".github" / "dependabot.yml")

    assert config["version"] == 2
    updates = {item["package-ecosystem"]: item for item in config["updates"]}
    assert set(updates) == {"pip", "github-actions"}
    for item in updates.values():
        assert item["directory"] == "/"
        assert item["schedule"]["interval"] == "monthly"
        assert item["open-pull-requests-limit"] >= 5


def test_codeql_workflow_scans_python_with_minimal_permissions() -> None:
    workflow = _yaml(WORKFLOWS / "codeql.yml")
    text = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    triggers = _triggers(workflow)
    job = workflow["jobs"]["analyze"]

    assert {"push", "pull_request", "schedule"} <= set(triggers)
    assert job["permissions"] == {
        "contents": "read",
        "packages": "read",
        "security-events": "write",
    }
    assert job["strategy"]["matrix"]["language"] == ["python"]
    assert "github/codeql-action/init@v3" in text
    assert "github/codeql-action/analyze@v3" in text


def test_release_workflow_verifies_and_publishes_the_same_artifact() -> None:
    workflow_path = WORKFLOWS / "release.yml"
    workflow = _yaml(workflow_path)
    text = workflow_path.read_text(encoding="utf-8")
    jobs = workflow["jobs"]

    assert {"build", "github-release", "publish-pypi"} <= set(jobs)
    build_text = yaml.safe_dump(jobs["build"], sort_keys=False)
    for required in (
        "python -m ruff check src tests scripts",
        "python -m compileall -q src tests scripts",
        "python -m pytest --cov=agos --cov-report=term-missing -q",
        "python -m build",
        "scripts/verify_release.py",
        "pip install --no-deps --force-reinstall",
        "agos dashboard --help",
    ):
        assert required in build_text

    github_release = jobs["github-release"]
    assert github_release["needs"] == "build"
    assert github_release["permissions"]["contents"] == "write"
    assert "startsWith(github.ref, 'refs/tags/v')" in github_release["if"]
    assert "gh release create" in yaml.safe_dump(github_release, sort_keys=False)

    pypi = jobs["publish-pypi"]
    assert pypi["needs"] == "build"
    assert pypi["environment"]["name"] == "pypi"
    assert pypi["permissions"] == {"id-token": "write"}
    assert "startsWith(github.ref, 'refs/tags/v')" in pypi["if"]
    assert "pypa/gh-action-pypi-publish@release/v1" in text

    for secret in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MULTICA_API_KEY",
        "AGOS_OPENHANDS_TOKEN",
        "PYPI_API_TOKEN",
    ):
        assert secret not in text


def test_default_ci_lints_and_compiles_release_scripts() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert "python -m ruff check src tests scripts" in text
    assert "python -m compileall -q src tests scripts" in text


def test_release_docs_use_read_only_branch_protection_inspection() -> None:
    text = (PROJECT_ROOT / "docs" / "release-install.md").read_text(encoding="utf-8")

    assert "gh api --method GET repos/zsr131550/agos/branches/main/protection" in text
    assert "gh api --method PUT" not in text
    assert "gh api --method PATCH" not in text
    assert "trusted publishing" in text.lower()
    assert "publish-pypi" in text
