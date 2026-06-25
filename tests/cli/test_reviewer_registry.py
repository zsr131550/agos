from __future__ import annotations

import pytest
import yaml

from agos.adapters.reviewers import FakeReviewerAdapter, LlmCliReviewerAdapter
from agos.cli.reviewer_registry import configured_reviewer_adapters, configured_reviewer_specs
from agos.core.repo import repo_paths


def test_configured_reviewer_adapters_uses_agos_yaml(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "reviewers": {
                    "security": {
                        "type": "manual",
                        "role": "security_reviewer",
                        "required": True,
                    },
                    "tests": {
                        "type": "fake",
                        "role": "test_reviewer",
                        "required": False,
                    },
                },
                "allow_fake_reviewer": True,
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    adapters = configured_reviewer_adapters(tmp_repo)
    specs = configured_reviewer_specs(tmp_repo)

    assert set(adapters) == {"security", "tests"}
    assert [spec.id for spec in specs] == ["security", "tests"]
    assert specs[0].required is True
    assert specs[1].required is False


def test_configured_reviewer_adapters_defaults_to_empty(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    assert configured_reviewer_adapters(tmp_repo) == {}
    assert configured_reviewer_specs(tmp_repo) == []


def test_configured_reviewer_adapters_builds_llm_cli_reviewers(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "reviewers": {
                    "codex": {
                        "type": "codex_cli",
                        "role": "security_reviewer",
                        "required": True,
                        "timeout_seconds": 90,
                        "blocking_severity": "high",
                    },
                    "claude": {
                        "type": "claude_code",
                        "executor": "claude_code",
                        "role": "design_reviewer",
                        "required": False,
                        "blocking_severity": "medium",
                    },
                },
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    adapters = configured_reviewer_adapters(tmp_repo)

    assert set(adapters) == {"codex", "claude"}
    assert isinstance(adapters["codex"], LlmCliReviewerAdapter)
    assert adapters["codex"].executor == "codex_cli"
    assert adapters["codex"].role == "security_reviewer"
    assert adapters["codex"].timeout_seconds == 90
    assert adapters["codex"].blocking_severity == "high"
    assert adapters["codex"].review_store is not None
    assert isinstance(adapters["claude"], LlmCliReviewerAdapter)
    assert adapters["claude"].executor == "claude_code"
    assert adapters["claude"].role == "design_reviewer"
    assert adapters["claude"].blocking_severity == "medium"


def test_configured_reviewer_adapters_rejects_unknown_type(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "reviewers": {
                    "bogus": {
                        "type": "unsupported_thing",
                        "role": "reviewer",
                    },
                },
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported reviewer adapter type"):
        configured_reviewer_adapters(tmp_repo)


def test_configured_reviewer_adapters_rejects_fake_by_default(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "reviewers": {"clean": {"type": "fake", "role": "reviewer"}},
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="fake reviewer is not allowed in production config"):
        configured_reviewer_adapters(tmp_repo)


def test_configured_reviewer_adapters_allows_fake_with_flag(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "reviewers": {"clean": {"type": "fake", "role": "reviewer"}},
                "allow_fake_reviewer": True,
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    adapters = configured_reviewer_adapters(tmp_repo)

    assert set(adapters) == {"clean"}
    assert isinstance(adapters["clean"], FakeReviewerAdapter)
