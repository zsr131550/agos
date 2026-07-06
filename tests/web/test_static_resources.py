from __future__ import annotations

from importlib import resources


def test_dashboard_static_index_is_packaged() -> None:
    index = resources.files("agos.web").joinpath("static", "index.html")

    text = index.read_text(encoding="utf-8")

    assert '<main id="app">' in text
    assert "AGOS 控制台" in text
    assert "data-agos-dashboard" in text
