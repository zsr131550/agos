"""Shared offline Ed25519 signing primitives."""
from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path

from agos.core.config import TrustedSignerConfig


SIGNING_EXTRA_ERROR = "Ed25519 support requires AGOS with the 'signing' extra"


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def signature_message(
    *,
    algorithm: str,
    issuer: str,
    key_id: str,
    payload: object,
) -> bytes:
    return canonical_json(
        {
            "algorithm": algorithm,
            "issuer": issuer,
            "key_id": key_id,
            "payload": payload,
        }
    ).encode("utf-8")


def sign_ed25519(message: bytes, private_key_path: Path) -> str:
    serialization, private_type, _public_type, _invalid_signature = _crypto_types()
    try:
        key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    except OSError as exc:
        raise ValueError(f"private key could not be read: {private_key_path}: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid Ed25519 private key: {private_key_path}") from exc
    if not isinstance(key, private_type):
        raise ValueError(f"private key is not Ed25519: {private_key_path}")
    return base64.b64encode(key.sign(message)).decode("ascii")


def verify_ed25519(message: bytes, signature: str, public_key_path: Path) -> None:
    serialization, _private_type, public_type, invalid_signature = _crypto_types()
    try:
        signature_bytes = base64.b64decode(signature.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError("Ed25519 signature is not valid base64") from exc
    try:
        key = serialization.load_pem_public_key(public_key_path.read_bytes())
    except OSError as exc:
        raise ValueError(f"public key could not be read: {public_key_path}: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid Ed25519 public key: {public_key_path}") from exc
    if not isinstance(key, public_type):
        raise ValueError(f"public key is not Ed25519: {public_key_path}")
    try:
        key.verify(signature_bytes, message)
    except invalid_signature as exc:
        raise ValueError("Ed25519 signature verification failed") from exc


def trusted_public_key_path(
    trusted_signers: list[TrustedSignerConfig],
    *,
    issuer: str,
    key_id: str,
    trusted_config_path: Path,
) -> Path:
    signer = next(
        (
            item
            for item in trusted_signers
            if item.issuer == issuer and item.key_id == key_id
        ),
        None,
    )
    if signer is None:
        raise ValueError(f"signer is not trusted: issuer={issuer!r} key_id={key_id!r}")
    relative = Path(signer.public_key_path)
    if relative.is_absolute():
        raise ValueError("trusted signer public_key_path must be relative to trusted config")
    config_dir = trusted_config_path.resolve().parent
    resolved = (config_dir / relative).resolve()
    if not resolved.is_relative_to(config_dir):
        raise ValueError("trusted signer public_key_path escapes trusted config directory")
    return resolved


def _crypto_types():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - covered through isolated import test
        raise RuntimeError(SIGNING_EXTRA_ERROR) from exc
    return serialization, Ed25519PrivateKey, Ed25519PublicKey, InvalidSignature
