from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path


def test_dashboard_static_index_is_packaged() -> None:
    index = resources.files("agos.web").joinpath("static", "index.html")

    text = index.read_text(encoding="utf-8")

    assert '<main id="app">' in text
    assert "AGOS 控制台" in text
    assert "data-agos-dashboard" in text
    assert "任务批次" in text
    assert "Subagent 节点" in text
    assert "证据文件" in text
    assert "自我蒸馏" in text
    assert "fetchJson('/api/runs/current')" in text
    assert "run.task?.title" in text
    assert "run.status?.phase" in text
    assert "row.title" in text
    assert "ledger_verified" in text
    assert "evidence.text" in text
    assert "value.includes('<span')" not in text
    assert "html: statusBadge(phase)" in text
    assert "data?.error?.message" in text
    assert "errorMessage(data" in text
    assert "error.hint" in text
    assert "agos init" in text
    assert "agos start --title" in text
    assert "fetchJson('/api/health')" in text
    assert "任务输入" in text
    assert 'id="new-run-form"' in text
    assert 'id="new-run-title"' in text
    assert 'id="new-run-intent"' in text
    assert 'id="new-run-workflow"' in text
    assert 'id="new-run-gates"' in text
    assert "createRunFromForm" in text
    assert "fetchJson('/api/runs', {" in text
    assert 'method: "POST"' in text
    assert "Promise.all([fetchJson('/api/health'), fetchJson('/api/runs')])" not in text
    assert "evidence.text || JSON.stringify" in text
    assert "暂无任务批次。" in text
    assert "候选 / 节点 / 门禁" in text
    assert "执行 ID" in text
    assert "更新时间" in text
    assert "暂无 Subagent 节点。" in text
    assert "Worker / 输出" in text
    assert "`阶段 ${index + 1}`" in text
    assert "`节点 ${index + 1}`" in text
    assert "???????" not in text
    assert "?? / ?? / ??" not in text
    assert "?? Subagent ???" not in text
    assert "Worker / ??" not in text
    assert "`?? ${index + 1}`" not in text


def test_dashboard_static_package_data_is_configured() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    package_data = data["tool"]["setuptools"]["package-data"]

    assert package_data["agos.web"] == ["static/*.html"]
    assert package_data["agos.hooks.templates"] == ["*.sh"]
