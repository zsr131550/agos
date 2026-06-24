"""`agos anchor` commands."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.trust_anchor import (
    FileTrustAnchorStore,
    GitRefTrustAnchorStore,
    TrustAnchorStore,
    publish_current_anchor,
    verify_current_anchor,
)


anchor_app = typer.Typer(help="Publish and verify AGOS trust anchors.")
AnchorBackend = Literal["file", "git-ref"]


@anchor_app.command("publish")
def anchor_publish_command(
    backend: AnchorBackend = typer.Option("file", "--backend", help="Anchor backend."),
    path: Path | None = typer.Option(None, "--path", help="File backend path."),
    issuer: str = typer.Option(..., "--issuer", help="Anchor issuer name."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        store = _store(backend, repo_root, path)
        payload = publish_current_anchor(paths, store, issuer=issuer)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"published trust anchor for {payload.task_id} "
        f"seq={payload.ledger_seq} head={payload.ledger_head_hash}"
    )


@anchor_app.command("verify")
def anchor_verify_command(
    backend: AnchorBackend = typer.Option("file", "--backend", help="Anchor backend."),
    path: Path | None = typer.Option(None, "--path", help="File backend path."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON output."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        store = _store(backend, repo_root, path)
        verification = verify_current_anchor(paths, store)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    if json_output:
        typer.echo(verification.model_dump_json(indent=2))
    elif verification.passed:
        typer.echo(f"trust anchor verified for {verification.task_id}")
    else:
        for issue in verification.issues:
            typer.echo(issue, err=True)
    if not verification.passed:
        raise typer.Exit(code=1)


def _store(backend: AnchorBackend, repo_root: Path, path: Path | None) -> TrustAnchorStore:
    if backend == "file":
        if path is None:
            raise ValueError("--path is required for --backend file")
        return FileTrustAnchorStore(path)
    if backend == "git-ref":
        return GitRefTrustAnchorStore(repo_root)
    raise ValueError(f"unsupported anchor backend: {backend}")
