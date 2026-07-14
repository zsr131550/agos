from __future__ import annotations

import json
import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

from agos.core.adapter import ExecutorRun
from agos.core import trust_anchor
from agos.core import signing
from agos.core.config import TrustedSignerConfig, TrustAnchorConfig
from agos.core.ledger import Ledger
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.task import ExecutorBinding, Task, save_task
from agos.core.trust_anchor import (
    FileTrustAnchorStore,
    GitRefTrustAnchorStore,
    TrustAnchorPayload,
    canonical_json,
    publish_current_anchor,
    store_from_config,
    verify_current_anchor,
)


PRIVATE_KEY_PEM = """-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIJ1hsZ3v/VpguoRK9JLsLMREScVpezJpGXA7rAMcrn9g
-----END PRIVATE KEY-----
"""
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=
-----END PUBLIC KEY-----
"""
OTHER_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAPUAXw+hDiVqStwqnTRt+vJyYLM8uxJaMwM1V8Sr0Zgw=
-----END PUBLIC KEY-----
"""


def _payload(**overrides) -> TrustAnchorPayload:
    payload = {
        "schema_version": 1,
        "task_id": "agos-task-01",
        "ledger_head_hash": "b" * 64,
        "ledger_seq": 2,
        "repo_head": "c" * 40,
        "created_at": "2026-06-24T00:00:00Z",
        "issuer": "CI",
    }
    payload.update(overrides)
    return TrustAnchorPayload.model_validate(payload)


def _write_active_task(tmp_repo: Path) -> tuple[Task, object]:
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    task = Task(
        id="agos-task-01",
        title="Trust anchor task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    ledger = Ledger(paths.ledger)
    first = ledger.append({"type": "task_started", "task_id": task.id, "title": task.title})
    ledger.append({"type": "gates_locked", "task_id": task.id, "gates": []})
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-1", issue_id=None),
            ledger_head_hash=first["hash"],
        ),
        paths,
    )
    return task, paths


def _signer_files(tmp_repo: Path) -> tuple[Path, Path, TrustedSignerConfig]:
    private_key_path = tmp_repo.parent / "ci-private.pem"
    private_key_path.write_text(PRIVATE_KEY_PEM, encoding="ascii")
    trusted_config_path = tmp_repo.parent / "trusted" / ".agos" / "agos.yaml"
    public_key_path = trusted_config_path.parent / "keys" / "ci-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_text(PUBLIC_KEY_PEM, encoding="ascii")
    trusted_config_path.write_text("merge_gate: {}\n", encoding="utf-8")
    signer = TrustedSignerConfig(
        issuer="protected-ci",
        key_id="ci-2026",
        public_key_path="keys/ci-public.pem",
    )
    return private_key_path, trusted_config_path, signer


def _publish_signed_anchor(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    private_key_path, trusted_config_path, signer = _signer_files(tmp_repo)
    assert getattr(trust_anchor, "SignedFileTrustAnchorStore", None) is not None
    store = trust_anchor.SignedFileTrustAnchorStore(paths.evidence / "signed-anchor.json")
    envelope = trust_anchor.publish_current_signed_anchor(
        paths,
        store,
        issuer=signer.issuer,
        key_id=signer.key_id,
        private_key_path=private_key_path,
    )
    return paths, store, envelope, trusted_config_path, signer


def test_canonical_json_is_sorted_compact():
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_signing_helpers_report_missing_optional_dependency(monkeypatch):
    real_import = builtins.__import__

    def import_without_cryptography(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_cryptography)

    with pytest.raises(RuntimeError, match="requires AGOS with the 'signing' extra"):
        signing._crypto_types()


def test_signing_rejects_missing_private_key(tmp_path: Path):
    with pytest.raises(ValueError, match="private key could not be read"):
        signing.sign_ed25519(b"message", tmp_path / "missing-private.pem")


def test_signing_rejects_invalid_private_key(tmp_path: Path):
    private_key_path = tmp_path / "invalid-private.pem"
    private_key_path.write_text("not a PEM key", encoding="ascii")

    with pytest.raises(ValueError, match="invalid Ed25519 private key"):
        signing.sign_ed25519(b"message", private_key_path)


def test_signing_rejects_invalid_base64_signature(tmp_path: Path):
    with pytest.raises(ValueError, match="signature is not valid base64"):
        signing.verify_ed25519(b"message", "not/base64!", tmp_path / "unused-public.pem")


def test_signing_rejects_missing_public_key(tmp_path: Path):
    with pytest.raises(ValueError, match="public key could not be read"):
        signing.verify_ed25519(b"message", "AA==", tmp_path / "missing-public.pem")


def test_trust_anchor_payload_rejects_empty_fields():
    with pytest.raises(ValueError):
        _payload(task_id="")


def test_file_store_publish_and_verify_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_repo: Path):
    task, paths = _write_active_task(tmp_repo)
    monkeypatch.setattr("agos.core.trust_anchor.utc_now", lambda: "2026-06-24T00:00:00Z")
    monkeypatch.setattr("agos.core.trust_anchor.git_head", lambda _root: "a" * 40)

    store = FileTrustAnchorStore(paths.evidence / "anchors.json")
    payload = publish_current_anchor(paths, store, issuer="CI")

    assert payload.task_id == task.id
    assert payload.ledger_seq == 2
    assert payload.repo_head == "a" * 40
    assert store.read(task.id) == payload
    verification = verify_current_anchor(paths, store)
    assert verification.passed is True
    assert verification.anchor == payload
    assert verification.signed is False


def test_signed_file_store_publish_and_verify_round_trip(tmp_repo: Path):
    paths, store, envelope, trusted_config_path, signer = _publish_signed_anchor(tmp_repo)

    verification = trust_anchor.verify_current_signed_anchor(
        paths,
        store,
        trusted_signers=[signer],
        trusted_config_path=trusted_config_path,
    )

    assert envelope.algorithm == "Ed25519"
    assert envelope.issuer == envelope.payload.issuer == signer.issuer
    assert verification.passed is True
    assert verification.signed is True
    assert verification.signer_issuer == signer.issuer
    assert verification.signer_key_id == signer.key_id


def test_signed_anchor_rejects_tampered_payload(tmp_repo: Path):
    paths, store, envelope, trusted_config_path, signer = _publish_signed_anchor(tmp_repo)
    store.write(
        envelope.model_copy(
            update={
                "payload": envelope.payload.model_copy(
                    update={"ledger_head_hash": "f" * 64}
                )
            }
        )
    )

    verification = trust_anchor.verify_current_signed_anchor(
        paths,
        store,
        trusted_signers=[signer],
        trusted_config_path=trusted_config_path,
    )

    assert verification.passed is False
    assert verification.signed is False
    assert any("signature" in issue for issue in verification.issues)


def test_signed_anchor_rejects_tampered_signature(tmp_repo: Path):
    paths, store, envelope, trusted_config_path, signer = _publish_signed_anchor(tmp_repo)
    replacement = ("A" if envelope.signature[0] != "A" else "B") + envelope.signature[1:]
    store.write(envelope.model_copy(update={"signature": replacement}))

    verification = trust_anchor.verify_current_signed_anchor(
        paths,
        store,
        trusted_signers=[signer],
        trusted_config_path=trusted_config_path,
    )

    assert verification.passed is False
    assert any("signature" in issue for issue in verification.issues)


def test_signed_anchor_rejects_unknown_signer(tmp_repo: Path):
    paths, store, _envelope, trusted_config_path, signer = _publish_signed_anchor(tmp_repo)
    unknown = signer.model_copy(update={"key_id": "unknown-key"})

    verification = trust_anchor.verify_current_signed_anchor(
        paths,
        store,
        trusted_signers=[unknown],
        trusted_config_path=trusted_config_path,
    )

    assert verification.passed is False
    assert any("not trusted" in issue for issue in verification.issues)


def test_signed_anchor_rejects_wrong_public_key(tmp_repo: Path):
    paths, store, _envelope, trusted_config_path, signer = _publish_signed_anchor(tmp_repo)
    public_key_path = trusted_config_path.parent / signer.public_key_path
    public_key_path.write_text(OTHER_PUBLIC_KEY_PEM, encoding="ascii")

    verification = trust_anchor.verify_current_signed_anchor(
        paths,
        store,
        trusted_signers=[signer],
        trusted_config_path=trusted_config_path,
    )

    assert verification.passed is False
    assert any("signature" in issue for issue in verification.issues)


def test_signed_anchor_rejects_unsupported_algorithm(tmp_repo: Path):
    paths, store, envelope, trusted_config_path, signer = _publish_signed_anchor(tmp_repo)
    payload = envelope.model_dump(mode="python")
    payload["algorithm"] = "RSA"
    store.path.write_text(json.dumps(payload), encoding="utf-8")

    verification = trust_anchor.verify_current_signed_anchor(
        paths,
        store,
        trusted_signers=[signer],
        trusted_config_path=trusted_config_path,
    )

    assert verification.passed is False
    assert any("anchor" in issue.lower() for issue in verification.issues)


def test_signed_anchor_envelope_rejects_unknown_schema_version(tmp_repo: Path):
    _paths, _store, envelope, _trusted_config_path, _signer = _publish_signed_anchor(tmp_repo)
    payload = envelope.model_dump(mode="python")
    payload["schema_version"] = 2

    with pytest.raises(ValueError, match="schema_version"):
        trust_anchor.SignedTrustAnchorEnvelope.model_validate(payload)


def test_signed_anchor_rejects_stale_ledger_head(tmp_repo: Path):
    paths, store, _envelope, trusted_config_path, signer = _publish_signed_anchor(tmp_repo)
    Ledger(paths.ledger).append({"type": "checkpoint", "repo_head": "a" * 40})

    verification = trust_anchor.verify_current_signed_anchor(
        paths,
        store,
        trusted_signers=[signer],
        trusted_config_path=trusted_config_path,
    )

    assert verification.passed is False
    assert verification.signed is True
    assert any("ledger head" in issue for issue in verification.issues)


def test_signed_anchor_refuses_private_key_inside_agos(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    private_key_path = paths.agos_dir / "private.pem"
    private_key_path.write_text(PRIVATE_KEY_PEM, encoding="ascii")
    store_type = getattr(trust_anchor, "SignedFileTrustAnchorStore", None)
    assert store_type is not None

    with pytest.raises(ValueError, match="private key must be outside .agos"):
        trust_anchor.publish_current_signed_anchor(
            paths,
            store_type(paths.evidence / "signed-anchor.json"),
            issuer="protected-ci",
            key_id="ci-2026",
            private_key_path=private_key_path,
        )


def test_signed_anchor_refuses_private_key_symlink_inside_agos(tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    external_private_key = tmp_repo.parent / "external-private.pem"
    external_private_key.write_text(PRIVATE_KEY_PEM, encoding="ascii")
    symlink_path = paths.agos_dir / "private-link.pem"
    symlink_path.symlink_to(external_private_key)

    with pytest.raises(ValueError, match="private key must be outside .agos"):
        trust_anchor.publish_current_signed_anchor(
            paths,
            trust_anchor.SignedFileTrustAnchorStore(paths.evidence / "signed-anchor.json"),
            issuer="protected-ci",
            key_id="ci-2026",
            private_key_path=symlink_path,
        )


def test_verify_current_anchor_rejects_stale_ledger_head(monkeypatch: pytest.MonkeyPatch, tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    monkeypatch.setattr("agos.core.trust_anchor.utc_now", lambda: "2026-06-24T00:00:00Z")
    monkeypatch.setattr("agos.core.trust_anchor.git_head", lambda _root: "a" * 40)
    store = FileTrustAnchorStore(paths.evidence / "anchors.json")
    publish_current_anchor(paths, store, issuer="CI")
    Ledger(paths.ledger).append({"type": "checkpoint", "repo_head": "a" * 40})

    verification = verify_current_anchor(paths, store)

    assert verification.passed is False
    assert any("ledger head" in issue for issue in verification.issues)


def test_file_store_rejects_task_mismatch(tmp_path: Path):
    store = FileTrustAnchorStore(tmp_path / "anchor.json")
    store.write(_payload(task_id="other-task"))

    with pytest.raises(ValueError, match="task mismatch"):
        store.read("agos-task-01")


def test_store_from_config_uses_repo_relative_file_path(tmp_repo: Path):
    paths = repo_paths(tmp_repo)

    store = store_from_config(
        paths,
        TrustAnchorConfig(
            backend="file",
            path=".agos/tasks/current/evidence/anchors.json",
        ),
    )

    assert isinstance(store, FileTrustAnchorStore)
    assert store.path == paths.evidence / "anchors.json"


def test_publish_current_anchor_requires_status(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    task = Task(
        id="agos-task-01",
        title="Trust anchor task",
        workflow="feature",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    Ledger(paths.ledger).append({"type": "task_started", "task_id": task.id})

    with pytest.raises(ValueError, match="status"):
        publish_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"), issuer="CI")


def test_verify_current_anchor_handles_missing_task_and_tampered_ledger(tmp_repo: Path):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    missing = verify_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"))
    assert missing.passed is False

    _task, paths = _write_active_task(tmp_repo)
    records = paths.ledger.read_text(encoding="utf-8").splitlines()
    record = json.loads(records[0])
    record["type"] = "forged"
    records[0] = json.dumps(record)
    paths.ledger.write_text("\n".join(records) + "\n", encoding="utf-8")
    tampered = verify_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"))
    assert tampered.passed is False
    assert "ledger verification failed" in tampered.issues[0]


def test_verify_current_anchor_reports_schema_and_repo_mismatches(monkeypatch, tmp_repo: Path):
    _task, paths = _write_active_task(tmp_repo)
    monkeypatch.setattr("agos.core.trust_anchor.git_head", lambda _root: "a" * 40)
    anchor = publish_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"), issuer="CI")
    changed = anchor.model_copy(update={"schema_version": 2, "repo_head": "d" * 40})
    FileTrustAnchorStore(paths.evidence / "anchor.json").write(changed)

    verification = verify_current_anchor(paths, FileTrustAnchorStore(paths.evidence / "anchor.json"))

    assert verification.passed is False
    assert any("schema version" in issue for issue in verification.issues)
    assert any("repo head" in issue for issue in verification.issues)


def test_git_ref_store_validates_task_id_and_uses_git_commands(monkeypatch: pytest.MonkeyPatch, tmp_repo: Path):
    store = GitRefTrustAnchorStore(tmp_repo)
    payload = TrustAnchorPayload(
        schema_version=1,
        task_id="agos-task-01",
        ledger_head_hash="b" * 64,
        ledger_seq=3,
        repo_head="c" * 40,
        created_at="2026-06-24T00:00:00Z",
        issuer="CI",
    )
    calls: list[list[str]] = []

    def fake_run_command(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ["git", "hash-object"]:
            return SimpleNamespace(returncode=0, stdout="deadbeef\n", stderr="")
        if args[:2] == ["git", "update-ref"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if args[:2] == ["git", "cat-file"]:
            return SimpleNamespace(returncode=0, stdout=payload.canonical_json() + "\n", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr("agos.core.trust_anchor.run_command", fake_run_command)

    store.write(payload)
    loaded = store.read(payload.task_id)

    assert loaded == payload
    assert any(call[:2] == ["git", "update-ref"] for call in calls)
    assert any(call[:2] == ["git", "cat-file"] for call in calls)
    with pytest.raises(ValueError):
        store.ref_name("bad/task")
    with pytest.raises(ValueError):
        store.ref_name(" ")
