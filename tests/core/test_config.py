"""Tests for agos.yaml config loading and gate resolution."""
from __future__ import annotations

from pathlib import Path

import pytest

from agos.core.config import AGOSConfig, default_config, load_config, resolve_gates


def write_config(repo: Path, yaml_text: str) -> None:
    agos = repo / ".agos"
    agos.mkdir(exist_ok=True)
    (agos / "agos.yaml").write_text(yaml_text, encoding="utf-8")


SAMPLE = """
executor:
  name: multica
  agent: Example Agent
workflows:
  feature:
    gates:
      - id: tests_pass
        stage: [pre-commit, pre-push]
        command: "pytest -q"
      - id: no_secrets_in_diff
        stage: [pre-commit, pre-push]
        type: secret_scan
      - id: build_clean
        stage: [pre-push]
        command: "npm run build"
  docs_only:
    gates: []
"""


def test_load_config(tmp_repo: Path):
    write_config(tmp_repo, SAMPLE)
    cfg = load_config(tmp_repo)
    assert isinstance(cfg, AGOSConfig)
    assert set(cfg.workflows) == {"feature", "docs_only"}
    feature = cfg.workflows["feature"]
    assert [g.id for g in feature.gates] == [
        "tests_pass",
        "no_secrets_in_diff",
        "build_clean",
    ]


def test_resolve_gates_workflow(tmp_repo: Path):
    write_config(tmp_repo, SAMPLE)
    cfg = load_config(tmp_repo)
    gates = resolve_gates(cfg, "feature")
    assert [g.id for g in gates] == [
        "tests_pass",
        "no_secrets_in_diff",
        "build_clean",
    ]


def test_resolve_gates_override(tmp_repo: Path):
    write_config(tmp_repo, SAMPLE)
    cfg = load_config(tmp_repo)
    gates = resolve_gates(cfg, "feature", override=["tests_pass"])
    assert [g.id for g in gates] == ["tests_pass"]


def test_resolve_gates_empty_workflow(tmp_repo: Path):
    write_config(tmp_repo, SAMPLE)
    cfg = load_config(tmp_repo)
    assert resolve_gates(cfg, "docs_only") == []


def test_resolve_gates_unknown_workflow(tmp_repo: Path):
    write_config(tmp_repo, SAMPLE)
    cfg = load_config(tmp_repo)
    with pytest.raises(KeyError):
        resolve_gates(cfg, "nope")


def test_gate_spec_command_xor_type():
    from agos.core.config import GateSpec

    GateSpec(id="g", stage=["pre-commit"], command="pytest -q", type=None)
    GateSpec(id="g", stage=["pre-commit"], command=None, type="secret_scan")
    with pytest.raises(Exception):
        GateSpec(id="g", stage=["pre-commit"], command="x", type="secret_scan")
    with pytest.raises(Exception):
        GateSpec(id="g", stage=["pre-commit"], command=None, type=None)


def test_default_config_has_feature_workflow():
    cfg = default_config(agent="Lambda")
    assert "feature" in cfg.workflows
    ids = {g.id for g in cfg.workflows["feature"].gates}
    assert {"tests_pass", "no_secrets_in_diff"} <= ids
