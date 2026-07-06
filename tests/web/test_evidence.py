from __future__ import annotations

import pytest

from agos.core.repo import repo_paths
from agos.web.evidence import EvidenceResolutionError, read_evidence_text, resolve_evidence_ref


def test_resolves_task_relative_evidence_ref(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    target = paths.evidence / "gates" / "tests_pass.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("gate ok\n", encoding="utf-8")

    resolved = resolve_evidence_ref(paths, "evidence/gates/tests_pass.log")

    assert resolved == target.resolve()
    assert read_evidence_text(paths, "evidence/gates/tests_pass.log")["text"] == "gate ok\n"


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
        "/tmp/secret.txt",
        "C:/Users/ZR/.ssh/id_rsa",
        "reviews/../task.yaml",
        "",
    ],
)
def test_rejects_unsafe_evidence_refs(tmp_repo, ref: str) -> None:
    paths = repo_paths(tmp_repo)

    with pytest.raises(EvidenceResolutionError):
        resolve_evidence_ref(paths, ref)


def test_rejects_unknown_task_relative_root(tmp_repo) -> None:
    paths = repo_paths(tmp_repo)
    (paths.current_task / "private.txt").parent.mkdir(parents=True, exist_ok=True)
    (paths.current_task / "private.txt").write_text("no\n", encoding="utf-8")

    with pytest.raises(EvidenceResolutionError):
        resolve_evidence_ref(paths, "private.txt")
