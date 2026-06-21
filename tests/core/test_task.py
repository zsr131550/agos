"""Tests for task.yaml models and round-trip."""
from __future__ import annotations

from pathlib import Path

from agos.core.task import ExecutorBinding, Task, load_task, new_task_id, save_task


def make_task() -> Task:
    return Task(
        id=new_task_id(),
        title="Add login rate limiting",
        intent="Brute-force protection on /login",
        acceptance=["5 failed attempts -> 15min lockout", "tests cover lockout + unlock"],
        workflow="feature",
        gates=["tests_pass", "no_secrets_in_diff"],
        executor=ExecutorBinding(adapter="multica", agent="Lambda"),
    )


def test_new_task_id_is_ulid():
    tid = new_task_id()
    assert len(tid) == 26
    assert tid.isupper()


def test_task_round_trip(tmp_path: Path):
    task = make_task()
    path = tmp_path / "task.yaml"
    save_task(task, path)
    loaded = load_task(path)
    assert loaded == task


def test_task_yaml_has_expected_fields(tmp_path: Path):
    import yaml

    task = make_task()
    path = tmp_path / "task.yaml"
    save_task(task, path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["title"] == "Add login rate limiting"
    assert raw["executor"]["adapter"] == "multica"
    assert raw["gates"] == ["tests_pass", "no_secrets_in_diff"]


def test_task_requires_title():
    import pytest

    with pytest.raises(Exception):
        Task(
            id="x",
            title="",
            intent="i",
            acceptance=[],
            workflow="feature",
            gates=[],
            executor=ExecutorBinding(adapter="multica", agent="a"),
        )
