"""agos.yaml config model and gate resolution."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class GateSpec(BaseModel):
    """A gate declaration in agos.yaml. Exactly one of command/argv/type."""

    id: str
    stage: list[str]
    command: str | None = None
    argv: list[str] | None = None
    type: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "GateSpec":
        choices = [self.command is not None, self.argv is not None, self.type is not None]
        if sum(choices) != 1:
            raise ValueError(
                f"gate {self.id!r} must have exactly one of 'command', 'argv', or 'type'"
            )
        return self


class WorkflowConfig(BaseModel):
    """Workflow gate bundle."""

    gates: list[GateSpec] = Field(default_factory=list)


class ExecutorConfig(BaseModel):
    """Default executor binding written by `agos init`."""

    name: str = "multica"
    agent: str


class WorkerConfig(BaseModel):
    """One configured execution worker adapter."""

    type: str
    command: str | None = None
    agent: str | None = None
    endpoint: str | None = None
    token: str | None = None
    timeout_seconds: int = Field(default=30, ge=1)
    poll_interval_seconds: int = Field(default=1, ge=1)
    artifact_globs: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class ReviewerConfig(BaseModel):
    """One configured review adapter."""

    type: str
    role: str
    required: bool = True
    command: str | None = None



class OrchestrationConfig(BaseModel):
    """Runtime policy for multi-agent orchestration."""

    backend: str = "native_async"
    max_parallel: int = Field(default=1, ge=1)
    max_retries: int = Field(default=0, ge=0)
    worker_timeout_seconds: int | None = Field(default=None, ge=1)
    retry_backoff_seconds: int = Field(default=0, ge=0)
    endpoint: str | None = None
    token: str | None = None
    timeout_seconds: int = Field(default=30, ge=1)

class AGOSConfig(BaseModel):
    """Top-level `.agos/agos.yaml` structure."""

    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    default_workflow: str = "feature"
    workflows: dict[str, WorkflowConfig] = Field(default_factory=dict)
    workers: dict[str, WorkerConfig] = Field(default_factory=dict)
    reviewers: dict[str, ReviewerConfig] = Field(default_factory=dict)
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)

    @classmethod
    def default(
        cls,
        *,
        executor: str = "multica",
        agent: str,
    ) -> "AGOSConfig":
        return default_config(executor=executor, agent=agent)

    @classmethod
    def load(cls, path: Path) -> "AGOSConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(self.model_dump(mode="python"), sort_keys=False),
            encoding="utf-8",
        )

    def resolve_gates(
        self,
        workflow: str,
        overrides: list[str] | None = None,
    ) -> list[GateSpec]:
        return resolve_gates(self, workflow, override=overrides)


def default_config(
    *,
    executor: str = "multica",
    agent: str,
) -> AGOSConfig:
    """The config `agos init` writes."""

    return AGOSConfig.model_validate(
        {
            "executor": {"name": executor, "agent": agent},
            "default_workflow": "feature",
            "workflows": {
                "feature": {
                    "gates": [
                        {
                            "id": "tests_pass",
                            "stage": ["pre-commit", "pre-push"],
                            "argv": ["pytest", "-q"],
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
    """Read and validate `.agos/agos.yaml` from a repo root."""

    return AGOSConfig.load(repo_root / ".agos" / "agos.yaml")


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
        return [gate.model_copy(deep=True) for gate in wf.gates]
    by_id = {gate.id: gate for gate in wf.gates}
    missing = [gate_id for gate_id in override if gate_id not in by_id]
    if missing:
        raise KeyError(f"override gates not in workflow {workflow!r}: {missing}")
    return [by_id[gate_id].model_copy(deep=True) for gate_id in override]


