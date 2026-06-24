"""`agos merge-gate` command."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from agos.core.merge_gate import verify_merge_gate
from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.trust_anchor import FileTrustAnchorStore, GitRefTrustAnchorStore, TrustAnchorStore


AnchorBackend = Literal["file", "git-ref"]


def merge_gate_command(
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
    require_anchor: bool = typer.Option(False, "--require-anchor", help="Require trust anchor verification."),
    anchor_backend: AnchorBackend = typer.Option("file", "--anchor-backend", help="Trust anchor backend."),
    anchor_path: Path | None = typer.Option(None, "--anchor-path", help="File anchor path."),
    allow_missing_review: bool = typer.Option(
        False,
        "--allow-missing-review",
        help="Allow candidates with no completed review; stale or blocking completed reviews still block.",
    ),
    base_ref: str | None = typer.Option(None, "--base", help="Base git ref for submitted diff binding."),
    head_ref: str | None = typer.Option(None, "--head", help="Head git ref for submitted diff binding."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        result = verify_merge_gate(
            paths,
            require_anchor=require_anchor,
            anchor_store=_store(anchor_backend, repo_root, anchor_path) if require_anchor else None,
            allow_missing_review=allow_missing_review,
            base_ref=base_ref,
            head_ref=head_ref,
        )
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        typer.echo("merge gate passed" if result.passed else "merge gate blocked", err=not result.passed)
        for check in result.checks:
            suffix = f" ({', '.join(check.details)})" if check.details else ""
            typer.echo(f"{check.name}: {check.state} - {check.message}{suffix}", err=not result.passed)
    if not result.passed:
        raise typer.Exit(code=1)


def _store(backend: AnchorBackend, repo_root: Path, path: Path | None) -> TrustAnchorStore:
    if backend == "file":
        if path is None:
            raise ValueError("--anchor-path is required for --anchor-backend file")
        return FileTrustAnchorStore(path)
    if backend == "git-ref":
        return GitRefTrustAnchorStore(repo_root)
    raise ValueError(f"unsupported anchor backend: {backend}")
