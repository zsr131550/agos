"""Subprocess helpers with bounded execution time."""
from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Any


DEFAULT_COMMAND_TIMEOUT_SECONDS = 30


def run_command(
    args: str | Sequence[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with a default timeout unless the caller overrides it."""

    kwargs.setdefault("timeout", DEFAULT_COMMAND_TIMEOUT_SECONDS)
    try:
        return subprocess.run(args, **kwargs)
    except FileNotFoundError:
        # npm-installed CLIs (codex, claude) ship as ``.CMD`` shims that
        # CreateProcess cannot resolve from a bare name on Windows, raising
        # FileNotFoundError before the process starts. Retry once with the
        # PATH-resolved executable so callers don't need shell=True -- a shell
        # would re-expose command injection on prompts that carry task content.
        # Commands that spawn on the first attempt (git, *.exe, full paths) and
        # genuinely-missing commands are unaffected.
        resolved = _resolve_executable(args)
        if resolved is None:
            raise
        return subprocess.run(resolved, **kwargs)


def _resolve_executable(args: str | Sequence[str]) -> list[str] | None:
    """Return args with a bare command name expanded to its full PATH path.

    Returns None when resolution does not apply (non-Windows, string commands,
    an already-resolved command, or a command not on PATH) so the caller re-raises
    the original FileNotFoundError unchanged.
    """
    if sys.platform != "win32" or isinstance(args, str):
        return None
    resolved = shutil.which(args[0])
    if not resolved or resolved == args[0]:
        return None
    return [resolved, *args[1:]]
