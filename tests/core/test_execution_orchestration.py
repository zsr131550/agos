from __future__ import annotations

import yaml

from agos.core.execution_orchestration import ExecutionOrchestrator
from agos.core.repo import repo_paths
from agos.core.status import TaskStatus, save_status
from agos.core.adapter import ExecutorRun
from agos.core.task import ExecutorBinding, Task, save_task


def _active_task(tmp_repo):
    paths = repo_paths(tmp_repo)
    paths.agos_dir.mkdir(parents=True, exist_ok=True)
    task = Task(
        id="agos-01",
        title="Execution orchestration task",
        intent="Compile execution plans into orchestration specs",
        gates=[],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )
    save_task(task, paths.task_yaml)
    save_status(
        TaskStatus.for_started_task(
            task=task,
            run=ExecutorRun(adapter="multica", run_id="run-01", issue_id="AGO-1"),
            ledger_head_hash="ledger-01",
        ),
        paths,
    )
    return paths


def test_execution_orchestrator_builds_candidate_review_subgraph(tmp_repo):
    _paths = _active_task(tmp_repo)
    plan_path = tmp_repo / "execution-plan.yaml"
    plan_path.write_text(
        yaml.safe_dump(
            {
                "id": "execution-plan-01",
                "task_id": "agos-01",
                "max_parallel": 1,
                "requires_candidate_review": True,
                "subtasks": [
                    {
                        "id": "subtask-readme",
                        "title": "Update README",
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    orchestrator = ExecutionOrchestrator(_paths)
    spec = orchestrator.build_spec(plan_path=plan_path)

    assert spec.kind == "execution_run"
    assert spec.task_id == "agos-01"
    assert any(node.kind == "candidate_review_subgraph" for node in spec.nodes)


def test_execution_orchestrator_gates_root_and_child_subtasks_after_validation(tmp_repo):
    _paths = _active_task(tmp_repo)
    plan_path = tmp_repo / "execution-plan.yaml"
    plan_path.write_text(
        yaml.safe_dump(
            {
                "id": "execution-plan-01",
                "task_id": "agos-01",
                "max_parallel": 1,
                "requires_candidate_review": True,
                "subtasks": [
                    {
                        "id": "subtask-readme",
                        "title": "Update README",
                        "intent": "Change the README heading.",
                        "depends_on": [],
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    },
                    {
                        "id": "subtask-docs",
                        "title": "Update docs",
                        "intent": "Follow the README change.",
                        "depends_on": ["subtask-readme"],
                        "write_scope": ["docs/guide.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    orchestrator = ExecutionOrchestrator(_paths)
    spec = orchestrator.build_spec(plan_path=plan_path)

    nodes = {node.id: node for node in spec.nodes}
    assert "validate_plan" in nodes["prepare-subtask-readme"].depends_on
    assert nodes["worker-subtask-readme"].depends_on == ("prepare-subtask-readme",)
    assert nodes["review-subtask-readme"].depends_on == ("worker-subtask-readme",)
    assert nodes["prepare-subtask-docs"].depends_on == (
        "validate_plan",
        "review-subtask-readme",
    )


def test_execution_orchestrator_skips_review_nodes_when_disabled(tmp_repo):
    _paths = _active_task(tmp_repo)
    plan_path = tmp_repo / "execution-plan.yaml"
    plan_path.write_text(
        yaml.safe_dump(
            {
                "id": "execution-plan-01",
                "task_id": "agos-01",
                "max_parallel": 1,
                "requires_candidate_review": False,
                "subtasks": [
                    {
                        "id": "subtask-readme",
                        "title": "Update README",
                        "write_scope": ["README.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    },
                    {
                        "id": "subtask-docs",
                        "title": "Update docs",
                        "depends_on": ["subtask-readme"],
                        "write_scope": ["docs/guide.md"],
                        "worker": {"adapter": "local_worktree", "role": "worker_agent"},
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    orchestrator = ExecutionOrchestrator(_paths)
    spec = orchestrator.build_spec(plan_path=plan_path)

    nodes = {node.id: node for node in spec.nodes}
    assert not any(node.kind == "candidate_review_subgraph" for node in spec.nodes)
    assert nodes["worker-subtask-readme"].depends_on == ("prepare-subtask-readme",)
    assert nodes["prepare-subtask-docs"].depends_on == (
        "validate_plan",
        "worker-subtask-readme",
    )
    assert nodes["worker-subtask-docs"].depends_on == (
        "prepare-subtask-docs",
        "worker-subtask-readme",
    )
