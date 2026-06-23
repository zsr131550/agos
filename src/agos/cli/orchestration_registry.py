"""Register configured orchestration backends at the CLI boundary."""
from __future__ import annotations

from agos.backends.external_backend import ExternalBackend
from agos.backends.langgraph_backend import LangGraphBackend
from agos.backends.native_async import NativeAsyncBackend
from agos.core.config import load_config
from agos.core.execution_service import ExecutionService


def register_configured_orchestration_backends(service: ExecutionService) -> None:
    """Install orchestration backends declared in `.agos/agos.yaml` onto a service."""

    config = load_config(service.paths.root)
    orchestration = config.orchestration
    service.register_orchestration_backend(NativeAsyncBackend())
    service.register_orchestration_backend(
        ExternalBackend(
            endpoint=orchestration.endpoint,
            token=orchestration.token,
            timeout=orchestration.timeout_seconds,
        )
    )
    service.register_orchestration_backend(LangGraphBackend())
