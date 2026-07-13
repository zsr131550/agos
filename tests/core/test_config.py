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
    GateSpec(id="g", stage=["pre-commit"], argv=["pytest", "-q"], type=None)
    GateSpec(id="g", stage=["pre-commit"], command=None, type="secret_scan")
    GateSpec(id="g", stage=["pre-commit"], command=None, type="opa", options={"policy": "p.rego"})
    with pytest.raises(Exception):
        GateSpec(id="g", stage=["pre-commit"], command="x", type="secret_scan")
    with pytest.raises(Exception):
        GateSpec(id="g", stage=["pre-commit"], command="x", argv=["pytest"])
    with pytest.raises(Exception):
        GateSpec(id="g", stage=["pre-commit"], command=None, type=None)
    with pytest.raises(Exception):
        GateSpec(id="g", stage=["pre-commit"], command=None, type="unknown")


def test_default_config_has_feature_workflow():
    cfg = default_config(agent="Lambda")
    assert "feature" in cfg.workflows
    ids = {g.id for g in cfg.workflows["feature"].gates}
    assert {"tests_pass", "no_secrets_in_diff"} <= ids
    tests_gate = next(g for g in cfg.workflows["feature"].gates if g.id == "tests_pass")
    assert tests_gate.argv == ["pytest", "-q"]
    assert tests_gate.command is None
    assert tests_gate.timeout_seconds == 300
    assert cfg.trust_anchor.backend == "git-ref"
    assert cfg.trust_anchor.auto_publish_on_checkpoint is False
    assert cfg.merge_gate.provenance_policy == "optional"


def test_merge_gate_config_loads_required_policy_and_relative_trusted_signer():
    cfg = AGOSConfig.model_validate(
        {
            "executor": {"name": "multica", "agent": "Lambda"},
            "merge_gate": {
                "provenance_policy": "required",
                "trusted_signers": [
                    {
                        "issuer": "protected-ci",
                        "key_id": "ci-2026",
                        "public_key_path": "keys/ci-2026.pub.pem",
                    }
                ],
            },
        }
    )

    assert cfg.merge_gate.provenance_policy == "required"
    assert cfg.merge_gate.trusted_signers[0].public_key_path == "keys/ci-2026.pub.pem"


def test_merge_gate_config_rejects_duplicate_signer_identity():
    with pytest.raises(Exception, match="duplicate trusted signer"):
        AGOSConfig.model_validate(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "merge_gate": {
                    "trusted_signers": [
                        {
                            "issuer": "protected-ci",
                            "key_id": "ci-2026",
                            "public_key_path": "keys/first.pem",
                        },
                        {
                            "issuer": "protected-ci",
                            "key_id": "ci-2026",
                            "public_key_path": "keys/second.pem",
                        },
                    ]
                },
            }
        )


def test_merge_gate_config_rejects_empty_signer_fields():
    with pytest.raises(Exception, match="trusted signer fields must be non-empty"):
        AGOSConfig.model_validate(
            {
                "executor": {"name": "multica", "agent": "Lambda"},
                "merge_gate": {
                    "trusted_signers": [
                        {
                            "issuer": " ",
                            "key_id": "ci-2026",
                            "public_key_path": "keys/ci-2026.pub.pem",
                        }
                    ]
                },
            }
        )
