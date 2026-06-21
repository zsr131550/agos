"""Filesystem storage for execution orchestration artifacts."""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel

from agos.core.execution import (
    ArbiterDecision,
    CandidatePatch,
    CandidateTestRun,
    ExecutionPlan,
    ExecutionSubtask,
    WorkspaceBinding,
)
from agos.core.repo import AgosPaths


class ExecutionStore:
    def __init__(self, paths: AgosPaths) -> None:
        self.paths = paths
        self.execution_dir = paths.current_task / "execution"
        self.patch_dir = paths.evidence / "candidate_patches"

    @property
    def plan_path(self) -> Path:
        return self.execution_dir / "plan.json"

    def write_plan(self, plan: ExecutionPlan) -> str:
        return self._write_model(self.plan_path, plan, "execution/plan.json")

    def read_plan(self) -> ExecutionPlan:
        return ExecutionPlan.model_validate_json(self.plan_path.read_text(encoding="utf-8"))

    def write_subtask(self, subtask: ExecutionSubtask) -> str:
        return self._write_model(
            self.execution_dir / "subtasks" / f"{subtask.id}.json",
            subtask,
            f"execution/subtasks/{subtask.id}.json",
        )

    def read_subtask(self, subtask_id: str) -> ExecutionSubtask:
        path = self.execution_dir / "subtasks" / f"{subtask_id}.json"
        return ExecutionSubtask.model_validate_json(path.read_text(encoding="utf-8"))

    def write_workspace(self, workspace: WorkspaceBinding) -> str:
        return self._write_model(
            self.execution_dir / "workspaces" / f"{workspace.subtask_id}.json",
            workspace,
            workspace.ref,
        )

    def read_workspace(self, subtask_id: str) -> WorkspaceBinding:
        path = self.execution_dir / "workspaces" / f"{subtask_id}.json"
        return WorkspaceBinding.model_validate_json(path.read_text(encoding="utf-8"))

    def write_candidate(self, candidate: CandidatePatch) -> str:
        return self._write_model(
            self.execution_dir / "candidates" / f"{candidate.id}.json",
            candidate,
            f"execution/candidates/{candidate.id}.json",
        )

    def read_candidate(self, candidate_id: str) -> CandidatePatch:
        path = self.execution_dir / "candidates" / f"{candidate_id}.json"
        return CandidatePatch.model_validate_json(path.read_text(encoding="utf-8"))

    def read_candidates(self) -> list[CandidatePatch]:
        directory = self.execution_dir / "candidates"
        if not directory.exists():
            return []
        return [
            CandidatePatch.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]

    def write_test_run(self, run: CandidateTestRun) -> str:
        return self._write_model(
            self.execution_dir / "tests" / f"{run.id}.json",
            run,
            f"execution/tests/{run.id}.json",
        )

    def read_test_runs(self, candidate_id: str) -> list[CandidateTestRun]:
        directory = self.execution_dir / "tests"
        if not directory.exists():
            return []
        runs = [
            CandidateTestRun.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]
        return [run for run in runs if run.candidate_id == candidate_id]

    def write_decision(self, decision: ArbiterDecision) -> str:
        return self._write_model(
            self.execution_dir / "decisions" / f"{decision.id}.json",
            decision,
            f"execution/decisions/{decision.id}.json",
        )

    def read_decisions(self, candidate_id: str) -> list[ArbiterDecision]:
        directory = self.execution_dir / "decisions"
        if not directory.exists():
            return []
        decisions = [
            ArbiterDecision.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]
        return [decision for decision in decisions if decision.candidate_id == candidate_id]

    def write_candidate_patch(self, candidate_id: str, patch_bytes: bytes) -> tuple[str, str]:
        self.patch_dir.mkdir(parents=True, exist_ok=True)
        path = self.patch_dir / f"{candidate_id}.patch"
        path.write_bytes(patch_bytes)
        return f"evidence/candidate_patches/{candidate_id}.patch", hashlib.sha256(patch_bytes).hexdigest()

    def patch_path(self, patch_ref: str) -> Path:
        prefix = "evidence/"
        if not patch_ref.startswith(prefix):
            raise ValueError(f"unsupported evidence ref: {patch_ref}")
        return self.paths.evidence / patch_ref[len(prefix) :]

    def _write_model(self, path: Path, model: BaseModel, ref: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
        return ref
