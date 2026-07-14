"""agos.yaml config model and gate resolution."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from agos.core.review import ReviewSeverity
from agos.core.task_execution import TaskExecutionConfig

GateType = Literal["secret_scan", "opa", "semgrep", "trufflehog", "codeql"]
TrustAnchorBackend = Literal["file", "git-ref"]
ProvenancePolicy = Literal["required", "optional", "disabled"]


class GateSpec(BaseModel):
    """A gate declaration in agos.yaml. Exactly one of command/argv/type."""

    id: str
    stage: list[str]
    command: str | None = None
    argv: list[str] | None = None
    type: GateType | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)
    options: dict[str, object] = Field(default_factory=dict)

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
    command: str | None = None


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
    health_probe: bool = Field(default=False)
    # codex_cli-only: opt into hermetic execution that ignores local Codex
    # user config/rules. Default off so real local Codex smoke uses the user's
    # configured CLI authentication and preferences.
    ignore_user_config: bool = Field(default=False)
    ignore_rules: bool = Field(default=False)
    # claude_code-only: opt into real async polling via `--bg` + `agents --json`.
    claude_async_poll: bool = Field(default=False)
    # claude_code-only: reserved for follow-up `--resume` turns on completion.
    # Default off because each resumed turn incurs real cost; see P1-2.
    claude_resume_on_complete: bool = Field(default=False)


class ReviewerConfig(BaseModel):
    """One configured review adapter."""

    type: str
    role: str
    required: bool = True
    command: str | None = None
    executor: Literal["codex_cli", "claude_code"] | None = None
    timeout_seconds: int = Field(default=120, ge=1)
    blocking_severity: ReviewSeverity = "high"
    dev_only: bool = False

    @model_validator(mode="after")
    def _mark_fake_as_dev_only(self) -> "ReviewerConfig":
        if self.type == "fake":
            self.dev_only = True
        return self


class PlannerConfig(BaseModel):
    """Planner LLM adapter policy."""

    enabled: bool = False
    executor: Literal["codex_cli", "claude_code"] = "codex_cli"
    command: str | None = None
    timeout_seconds: int = Field(default=60, ge=1)


class OrchestrationConfig(BaseModel):
    """Runtime policy for multi-agent orchestration."""

    backend: str = "native_async"
    max_parallel: int = Field(default=1, ge=1)
    max_retries: int = Field(default=0, ge=0)
    worker_timeout_seconds: int | None = Field(default=None, ge=1)
    retry_backoff_seconds: int = Field(default=0, ge=0)
    max_tick_iterations: int = Field(default=20, ge=1)
    fallback_write_scope: list[str] = Field(
        default_factory=lambda: ["README.md", "src/agos", "tests", "docs"]
    )
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    endpoint: str | None = None
    token: str | None = None
    timeout_seconds: int = Field(default=30, ge=1)


class TrustAnchorConfig(BaseModel):
    """Out-of-band ledger anchor publication policy."""

    backend: TrustAnchorBackend = "git-ref"
    path: str | None = None
    auto_publish_on_checkpoint: bool = False
    issuer: str = "agos"


class TrustedSignerConfig(BaseModel):
    """One allowed offline provenance signer from trusted configuration."""

    issuer: str
    key_id: str
    public_key_path: str

    @model_validator(mode="after")
    def _non_empty_fields(self) -> "TrustedSignerConfig":
        if any(
            not value.strip()
            for value in (self.issuer, self.key_id, self.public_key_path)
        ):
            raise ValueError("trusted signer fields must be non-empty")
        return self


class MergeGateConfig(BaseModel):
    """Trusted merge-gate provenance policy."""

    provenance_policy: ProvenancePolicy = "optional"
    trusted_signers: list[TrustedSignerConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_signer_identities(self) -> "MergeGateConfig":
        identities = [(signer.issuer, signer.key_id) for signer in self.trusted_signers]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate trusted signer issuer/key_id")
        return self


class AGOSConfig(BaseModel):
    """Top-level `.agos/agos.yaml` structure."""

    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    default_workflow: str = "feature"
    workflows: dict[str, WorkflowConfig] = Field(default_factory=dict)
    workers: dict[str, WorkerConfig] = Field(default_factory=dict)
    reviewers: dict[str, ReviewerConfig] = Field(default_factory=dict)
    allow_fake_reviewer: bool = False
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    task_execution: TaskExecutionConfig = Field(default_factory=TaskExecutionConfig)
    trust_anchor: TrustAnchorConfig = Field(default_factory=TrustAnchorConfig)
    merge_gate: MergeGateConfig = Field(default_factory=MergeGateConfig)

    @classmethod
    def default(
        cls,
        *,
        executor: str = "multica",
        agent: str,
        command: str | None = None,
        workers: dict[str, WorkerConfig] | None = None,
    ) -> "AGOSConfig":
        return default_config(executor=executor, agent=agent, command=command, workers=workers)

    @classmethod
    def load(cls, path: Path) -> "AGOSConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(
                self.model_dump(mode="python", exclude_none=True, exclude_unset=True),
                sort_keys=False,
            ),
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
    command: str | None = None,
    workers: dict[str, WorkerConfig] | None = None,
) -> AGOSConfig:
    """The config `agos init` writes."""

    return AGOSConfig.model_validate(
        {
            "executor": {"name": executor, "agent": agent, "command": command},
            "default_workflow": "feature",
            "workers": workers or {},
            "workflows": {
                "feature": {
                    "gates": [
                        {
                            "id": "tests_pass",
                            "stage": ["pre-commit", "pre-push"],
                            "argv": ["pytest", "-q"],
                            "timeout_seconds": 300,
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
