from __future__ import annotations

import yaml

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
