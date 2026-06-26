"""Subprocess helpers with bounded execution time."""
from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


DEFAULT_COMMAND_TIMEOUT_SECONDS = 30


def run_command(
    args: str | Sequence[str],
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with a default timeout unless the caller overrides it."""

    kwargs.setdefault("timeout", DEFAULT_COMMAND_TIMEOUT_SECONDS)
    try:
        return _spawn(args, **kwargs)
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
        return _spawn(resolved, **kwargs)


def _spawn(args: str | Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Spawn a process, routing .CMD/.BAT shims through tree-aware timeout handling."""
    timeout = kwargs.pop("timeout", None)
    if sys.platform == "win32" and _is_cmd_shim(args):
        return _spawn_cmd_shim(args, timeout=timeout, **kwargs)
    return subprocess.run(args, timeout=timeout, **kwargs)


def _is_cmd_shim(args: str | Sequence[str]) -> bool:
    """True when args is a non-empty list whose command is a .CMD/.BAT shim."""
    if isinstance(args, str) or not args:
        return False
    return Path(args[0]).suffix.lower() in {".cmd", ".bat"}


def _spawn_cmd_shim(
    args: Sequence[str],
    *,
    timeout: float | None,
    check: bool = False,
    capture_output: bool = False,
    input: Any = None,
    text: bool | None = None,
    encoding: str | None = None,
    errors: str | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a .CMD/.BAT shim with whole-tree kill on timeout.

    npm CLIs install as .CMD shims: cmd.exe launches the real binary (e.g.
    node.exe) as a grandchild. ``subprocess.run``'s timeout kills only cmd.exe,
    leaving the grandchild orphaned and holding the stdout pipe, so the timeout
    returns tens of seconds late (or hangs). Driving ``Popen`` directly and
    killing the whole tree with ``taskkill /T`` on timeout lets the pipes drain
    promptly. ``run``-only kwargs (check, capture_output, input, text/encoding)
    are translated for ``Popen``.
    """
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    if text is not None:
        kwargs["text"] = text
    if encoding is not None:
        kwargs["encoding"] = encoding
    if errors is not None:
        kwargs["errors"] = errors
    proc = subprocess.Popen(args, **kwargs)
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        out, err = proc.communicate()
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout, output=out, stderr=err)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, args, out, err)
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


def _kill_tree(pid: int) -> None:
    """Force-kill a process and everything it spawned (Windows)."""
    # /T includes child processes; /F forces termination. check=False because the
    # process (or a child) may already be gone by the time we reap the tree.
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(pid)],
        capture_output=True,
        timeout=10,
        check=False,
    )


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
