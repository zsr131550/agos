"""agos.yaml config model and gate resolution."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class GateSpec(BaseModel):
    """A gate declaration in agos.yaml. Exactly one of command/type."""

    id: str
    stage: list[str]
    command: str | None = None
    type: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "GateSpec":
        if (self.command is None) == (self.type is None):
            raise ValueError(
                f"gate {self.id!r} must have exactly one of 'command' or 'type'"
            )
        return self


class WorkflowConfig(BaseModel):
    gates: list[GateSpec] = Field(default_factory=list)


class AGOSConfig(BaseModel):
    workflows: dict[str, WorkflowConfig]


def default_config() -> AGOSConfig:
    """The config `agos init` writes."""

    return AGOSConfig.model_validate(
        {
            "workflows": {
                "feature": {
                    "gates": [
                        {
                            "id": "tests_pass",
                            "stage": ["pre-commit", "pre-push"],
                            "command": "pytest -q",
                        },
                        {
                            "id": "no_secrets_in_diff",
                            "stage": ["pre-commit", "pre-push"],
                            "type": "secret_scan",
                        },
                    ],
                },
                "docs_only": {"gates": []},
            },
        }
    )


def load_config(repo_root: Path) -> AGOSConfig:
    """Read and validate .agos/agos.yaml."""

    raw = yaml.safe_load((repo_root / ".agos" / "agos.yaml").read_text(encoding="utf-8"))
    return AGOSConfig.model_validate(raw)


def resolve_gates(
    config: AGOSConfig,
    workflow: str,
    override: list[str] | None = None,
) -> list[GateSpec]:
    """Resolve the gate set for a workflow, optionally restricted to override ids."""

    wf = config.workflows.get(workflow)
    if wf is None:
        raise KeyError(f"unknown workflow: {workflow!r}")
    if override is None:
        return list(wf.gates)
    by_id = {g.id: g for g in wf.gates}
    missing = [gate_id for gate_id in override if gate_id not in by_id]
    if missing:
        raise KeyError(f"override gates not in workflow {workflow!r}: {missing}")
    return [by_id[gate_id] for gate_id in override]
