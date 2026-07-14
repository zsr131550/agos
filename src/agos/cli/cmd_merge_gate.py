"""`agos merge-gate` command."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from agos.core.config import ProvenancePolicy
from agos.core.merge_gate import verify_merge_gate
from agos.core.repo import find_initialized_repo_root, repo_paths
from agos.core.trust_anchor import (
    FileTrustAnchorStore,
    GitRefTrustAnchorStore,
    SignedFileTrustAnchorStore,
    SignedTrustAnchorStore,
    TrustAnchorStore,
)


AnchorBackend = Literal["file", "git-ref", "signed-file"]


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
    allow_legacy_decisionless: bool = typer.Option(
        False,
        "--allow-legacy-decisionless",
        help="Allow legacy applied candidates that predate decision evidence.",
    ),
    provenance_policy: ProvenancePolicy | None = typer.Option(
        None,
        "--provenance-policy",
        help="Override merge-gate provenance policy.",
    ),
    trusted_config: Path | None = typer.Option(
        None,
        "--trusted-config",
        help="Protected-base agos.yaml used for gates, policy, and signer keys.",
    ),
    base_ref: str | None = typer.Option(None, "--base", help="Base git ref for submitted diff binding."),
    head_ref: str | None = typer.Option(None, "--head", help="Head git ref for submitted diff binding."),
) -> None:
    try:
        repo_root = find_initialized_repo_root()
        paths = repo_paths(repo_root)
        selected_store = (
            _store(anchor_backend, repo_root, anchor_path)
            if require_anchor or anchor_backend == "signed-file"
            else None
        )
        signed_store = (
            selected_store
            if isinstance(selected_store, SignedFileTrustAnchorStore)
            else None
        )
        integrity_store = (
            selected_store
            if selected_store is not None
            and not isinstance(selected_store, SignedFileTrustAnchorStore)
            else None
        )
        result = verify_merge_gate(
            paths,
            require_anchor=require_anchor,
            anchor_store=integrity_store,
            signed_anchor_store=signed_store,
            allow_missing_review=allow_missing_review,
            allow_legacy_decisionless=allow_legacy_decisionless,
            provenance_policy=provenance_policy,
            trusted_config_path=trusted_config,
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


def _store(
    backend: AnchorBackend,
    repo_root: Path,
    path: Path | None,
) -> TrustAnchorStore | SignedTrustAnchorStore:
    if backend in {"file", "signed-file"}:
        if path is None:
            raise ValueError(f"--anchor-path is required for --anchor-backend {backend}")
        return (
            SignedFileTrustAnchorStore(path)
            if backend == "signed-file"
            else FileTrustAnchorStore(path)
        )
    if backend == "git-ref":
        return GitRefTrustAnchorStore(repo_root)
    raise ValueError(f"unsupported anchor backend: {backend}")
