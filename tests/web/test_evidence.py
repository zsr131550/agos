from __future__ import annotations

from dataclasses import replace

import pytest

from agos.core.repo import repo_paths
from agos.web.evidence import EvidenceResolutionError, read_evidence_text, resolve_evidence_ref


def test_resolves_task_relative_evidence_ref(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "tests_pass.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"gate ok\n")

    resolved = resolve_evidence_ref(paths, "evidence/gates/tests_pass.log")

    assert resolved == target.resolve()
    payload = read_evidence_text(paths, "evidence/gates/tests_pass.log")
    assert payload["text"] == "gate ok\n"
    assert payload["path"] == "evidence/gates/tests_pass.log"
    assert ":" not in payload["path"]
    assert not payload["path"].startswith(("/", "\\"))


def test_read_evidence_text_preserves_original_newlines(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "windows.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"a\r\nb\r")

    payload = read_evidence_text(paths, "evidence/gates/windows.log")

    assert payload["text"] == "a\r\nb\r"


def test_read_evidence_text_truncates_after_max_bytes(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "long.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"abcdef")

    payload = read_evidence_text(paths, "evidence/gates/long.log", max_bytes=3)

    assert payload["text"] == "abc"
    assert payload["truncated"] is True
    assert payload["size_bytes"] == 6


def test_read_evidence_text_replaces_invalid_utf8(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "bytes.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"ok\xff")

    payload = read_evidence_text(paths, "evidence/gates/bytes.log")

    assert payload["text"] == "ok�"


def test_read_evidence_text_rejects_unsupported_suffix(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "image.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not text")

    with pytest.raises(EvidenceResolutionError, match="unsupported evidence text suffix"):
        read_evidence_text(paths, "evidence/gates/image.bin")


def test_read_evidence_text_rejects_negative_max_bytes(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "tests_pass.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("gate ok\n", encoding="utf-8")

    with pytest.raises(EvidenceResolutionError, match="max_bytes must be non-negative"):
        read_evidence_text(paths, "evidence/gates/tests_pass.log", max_bytes=-1)


def test_resolves_bare_evidence_ref_inside_evidence_dir(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "tests_pass.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("gate ok\n", encoding="utf-8")

    resolved = resolve_evidence_ref(paths, "gates/tests_pass.log")

    assert resolved == target.resolve()


@pytest.mark.parametrize(
    "ref",
    [
        "../README.md",
        "evidence/../../README.md",
        r"evidence\..\..\README.md",
        "/tmp/secret.txt",
        "C:/Users/ZR/.ssh/id_rsa",
        r"\\server\share\x.txt",
        r"\absolute\rooted\x.txt",
        "reviews/../task.yaml",
        "",
    ],
)
def test_rejects_unsafe_evidence_refs(tmp_repo, ref: str) -> None:
    paths = repo_paths(tmp_repo)

    with pytest.raises(EvidenceResolutionError):
        resolve_evidence_ref(paths, ref)


@pytest.mark.parametrize("ref", ["/absolute/rooted/x.txt", r"\absolute\rooted\x.txt"])
def test_rejects_rooted_refs_even_when_matching_evidence_file_exists(tmp_repo, ref: str) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "absolute" / "rooted" / "x.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("no\n", encoding="utf-8")

    with pytest.raises(EvidenceResolutionError):
        resolve_evidence_ref(paths, ref)


def test_rejects_unknown_task_relative_root(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    (paths.current_task / "private.txt").parent.mkdir(parents=True, exist_ok=True)
    (paths.current_task / "private.txt").write_text("no\n", encoding="utf-8")

    with pytest.raises(EvidenceResolutionError):
        resolve_evidence_ref(paths, "private.txt")


def test_rejects_missing_evidence_file(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)

    with pytest.raises(EvidenceResolutionError, match="evidence reference does not exist"):
        resolve_evidence_ref(paths, "evidence/gates/missing.log")


def test_rejects_symlink_escape_from_evidence_dir(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    outside = tmp_repo / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    link = paths.evidence / "gates" / "escape.txt"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(EvidenceResolutionError):
        resolve_evidence_ref(paths, "gates/escape.txt")


def test_rejects_evidence_root_outside_current_task(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    outside_evidence = tmp_repo / "outside-evidence"
    target = outside_evidence / "gates" / "leak.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret\n", encoding="utf-8")
    unsafe_paths = replace(paths, evidence=outside_evidence)

    with pytest.raises(EvidenceResolutionError):
        resolve_evidence_ref(unsafe_paths, "gates/leak.log")
