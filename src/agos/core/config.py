"""AGOS repo configuration models."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class GateConfig(BaseModel):
    """Resolved gate definition used by the CLI and ledger."""

    id: str
    stage: list[str] = Field(default_factory=list)
    command: str | None = None
    type: str | None = None


class WorkflowConfig(BaseModel):
    """Named workflow gate bundle."""

    gates: list[GateConfig] = Field(default_factory=list)


class ExecutorConfig(BaseModel):
    """Executor defaults for the repo."""

    name: str = "multica"
    agent: str = "Lambda"


class AGOSConfig(BaseModel):
    """Top-level `.agos/agos.yaml` structure."""

    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    default_workflow: str = "feature"
    workflows: dict[str, WorkflowConfig] = Field(default_factory=dict)

    @classmethod
    def default(cls, *, executor: str = "multica", agent: str = "Lambda") -> "AGOSConfig":
        return cls(
            executor=ExecutorConfig(name=executor, agent=agent),
            default_workflow="feature",
            workflows={
                "feature": WorkflowConfig(
                    gates=[
                        GateConfig(
                            id="tests_pass",
                            stage=["pre-commit", "pre-push"],
                            command="pytest -q",
                        ),
                        GateConfig(
                            id="no_secrets_in_diff",
                            stage=["pre-commit", "pre-push"],
                            type="secret_scan",
                        ),
                    ]
                )
            },
        )

    @classmethod
    def load(cls, path: Path) -> "AGOSConfig":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(self.model_dump(mode="python"), sort_keys=False),
            encoding="utf-8",
        )

    def resolve_gates(self, workflow: str, overrides: list[str] | None = None) -> list[GateConfig]:
        try:
            workflow_config = self.workflows[workflow]
        except KeyError as exc:
            raise ValueError(f"Unknown workflow '{workflow}'") from exc

        gates = workflow_config.gates
        if not overrides:
            return [gate.model_copy(deep=True) for gate in gates]

        gate_lookup = {gate.id: gate for gate in gates}
        resolved: list[GateConfig] = []
        for gate_name in overrides:
            try:
                gate = gate_lookup[gate_name]
            except KeyError as exc:
                raise ValueError(f"Unknown gate '{gate_name}' for workflow '{workflow}'") from exc
            resolved.append(gate.model_copy(deep=True))
        return resolved

