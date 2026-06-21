"""Subprocess helpers with bounded execution time."""
from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any


DEFAULT_COMMAND_TIMEOUT_SECONDS = 30


def run_command(
    args: str | Sequence[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with a default timeout unless the caller overrides it."""

    kwargs.setdefault("timeout", DEFAULT_COMMAND_TIMEOUT_SECONDS)
    return subprocess.run(args, **kwargs)
