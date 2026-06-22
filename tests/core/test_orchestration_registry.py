from __future__ import annotations

import pytest

from agos.core.orchestration.registry import OrchestrationRegistry, RegistryResolutionError


def test_missing_backend_lookup_raises_registry_resolution_error():
    registry = OrchestrationRegistry()

    with pytest.raises(RegistryResolutionError, match="missing worker backend: local_worker"):
        registry.resolve_worker("local_worker")
