"""Shared transport helpers for worker adapters."""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from agos.core.command import run_command


def worker_env(env: dict[str, str] | None) -> dict[str, str]:
    return {**os.environ, **dict(env or {})}


def run_worker_command(
    args: Sequence[str],
    *,
    action: str,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    runner=run_command,
):
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "timeout": timeout_seconds,
        "env": worker_env(env),
    }
    if cwd is not None:
        kwargs["cwd"] = cwd
    try:
        proc = runner(list(args), **kwargs)
    except subprocess.TimeoutExpired as exc:
        timeout = exc.timeout or timeout_seconds
        raise RuntimeError(f"{action} timed out after {timeout:g} seconds") from exc
    except OSError as exc:
        raise RuntimeError(f"{action} failed: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"{action} failed with exit {proc.returncode}: {_process_detail(proc)}"
        )
    return proc


def load_json_object(stdout: str, *, action: str) -> dict[str, object]:
    payload = load_json_object_or_list(stdout, action=action)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{action} returned non-object JSON")
    return payload


def load_json_object_or_list(stdout: str, *, action: str) -> dict[str, object] | list[object]:
    if not stdout.strip():
        return {}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{action} returned invalid JSON: {exc.msg}") from exc
    if isinstance(payload, dict | list):
        return payload
    raise RuntimeError(f"{action} returned unsupported JSON")


def json_http_request(
    service: str,
    method: str,
    url: str,
    *,
    payload=None,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
    opener=urlopen,
) -> dict[str, object]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with opener(request, timeout=timeout) as response:
            data = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        message = f"HTTP {exc.code} {exc.reason}"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(f"{service} {method} {url} failed: {message}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"{service} {method} {url} failed: timeout") from exc
    except URLError as exc:
        raise RuntimeError(f"{service} {method} {url} failed: {exc.reason}") from exc
    if not data.strip():
        return {}
    try:
        loaded = json.loads(data)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{service} {method} {url} returned invalid JSON: {exc.msg}"
        ) from exc
    if not isinstance(loaded, dict):
        raise RuntimeError(f"{service} {method} {url} returned non-object JSON")
    return loaded


def output_refs_from_payload(payload: dict[str, object] | None) -> list[str]:
    if payload is None:
        return []
    output_refs = payload.get("output_refs", [])
    if not isinstance(output_refs, list):
        return []
    return [str(ref) for ref in output_refs]


def metadata_from_payload(payload: dict[str, object]) -> dict[str, str]:
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    return {str(key): str(value) for key, value in metadata.items()}


def process_detail(proc) -> str:
    return _process_detail(proc)


def _process_detail(proc) -> str:
    stderr = getattr(proc, "stderr", "") or ""
    stdout = getattr(proc, "stdout", "") or ""
    return str(stderr or stdout or "command failed").strip()
