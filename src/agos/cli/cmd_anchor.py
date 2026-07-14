"""`agos anchor` commands."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.config import AGOSConfig
from agos.core.trust_anchor import (
    FileTrustAnchorStore,
    GitRefTrustAnchorStore,
    SignedFileTrustAnchorStore,
    SignedTrustAnchorStore,
    TrustAnchorStore,
    publish_current_anchor,
    publish_current_signed_anchor,
    verify_current_anchor,
    verify_current_signed_anchor,
)


anchor_app = typer.Typer(help="Publish and verify AGOS trust anchors.")
AnchorBackend = Literal["file", "git-ref", "signed-file"]


@anchor_app.command("publish")
def anchor_publish_command(
    backend: AnchorBackend = typer.Option("file", "--backend", help="Anchor backend."),
    path: Path | None = typer.Option(None, "--path", help="File backend path."),
    issuer: str = typer.Option(..., "--issuer", help="Anchor issuer name."),
    key_id: str | None = typer.Option(None, "--key-id", help="Signed anchor key identifier."),
    private_key: Path | None = typer.Option(
        None,
        "--private-key",
        help="Ed25519 private key path outside the governed .agos directory.",
    ),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        store = _store(backend, repo_root, path)
        if backend == "signed-file":
            if key_id is None or private_key is None:
                raise ValueError("--key-id and --private-key are required for signed-file publish")
            assert isinstance(store, SignedFileTrustAnchorStore)
            envelope = publish_current_signed_anchor(
                paths,
                store,
                issuer=issuer,
                key_id=key_id,
                private_key_path=private_key,
            )
            payload = envelope.payload
        else:
            assert not isinstance(store, SignedFileTrustAnchorStore)
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
    trusted_config: Path | None = typer.Option(
        None,
        "--trusted-config",
        help="Trusted agos.yaml used to resolve allowed signed-anchor public keys.",
    ),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        store = _store(backend, repo_root, path)
        if backend == "signed-file":
            if trusted_config is None:
                raise ValueError("--trusted-config is required for signed-file verify")
            assert isinstance(store, SignedFileTrustAnchorStore)
            config = AGOSConfig.load(trusted_config)
            verification = verify_current_signed_anchor(
                paths,
                store,
                trusted_signers=config.merge_gate.trusted_signers,
                trusted_config_path=trusted_config,
            )
        else:
            assert not isinstance(store, SignedFileTrustAnchorStore)
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


def _store(
    backend: AnchorBackend,
    repo_root: Path,
    path: Path | None,
) -> TrustAnchorStore | SignedTrustAnchorStore:
    if backend in {"file", "signed-file"}:
        if path is None:
            raise ValueError(f"--path is required for --backend {backend}")
        return (
            SignedFileTrustAnchorStore(path)
            if backend == "signed-file"
            else FileTrustAnchorStore(path)
        )
    if backend == "git-ref":
        return GitRefTrustAnchorStore(repo_root)
    raise ValueError(f"unsupported anchor backend: {backend}")
