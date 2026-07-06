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


def test_dashboard_static_package_data_is_configured() -> None:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    package_data = data["tool"]["setuptools"]["package-data"]

    assert package_data["agos.web"] == ["static/*.html"]
    assert package_data["agos.hooks.templates"] == ["*.sh"]
