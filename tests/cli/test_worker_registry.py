from __future__ import annotations

import yaml

from agos.cli.worker_registry import register_configured_worker_adapters
from agos.core.execution_service import ExecutionService
from agos.core.repo import repo_paths


def test_register_configured_worker_adapters_uses_agos_yaml(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    paths.agos_yaml.write_text(
        yaml.safe_dump(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "workers": {
                    "local_worktree": {"type": "local_worktree"},
                    "codex": {"type": "codex_cli", "command": "codex"},
                    "multica": {"type": "multica", "command": "multica", "agent": "Lambda"},
                    "openhands": {
                        "type": "openhands",
                        "endpoint": "http://openhands.local",
                        "token": "secret",
                    },
                },
                "workflows": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    service = ExecutionService(paths)

    register_configured_worker_adapters(service)

    assert set(service.worker_adapter_names()) == {
        "codex",
        "local_worktree",
        "multica",
        "openhands",
    }


def test_register_configured_worker_adapters_defaults_to_local_worktree(tmp_repo):
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
    service = ExecutionService(paths)

    register_configured_worker_adapters(service)

    assert service.worker_adapter_names() == ["local_worktree"]
