"""A killed SPA swap must leave the previous interface recoverable."""

import json
from pathlib import Path

from scripts import fetch_spa


def test_installed_restores_previous_directory_after_interrupted_swap(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    previous = frontend / fetch_spa.PREVIOUS_NAME
    previous.mkdir(parents=True)
    (previous / "index.html").write_text("the previous working interface", encoding="utf-8")
    (previous / fetch_spa.STAMP_NAME).write_text(
        json.dumps({"version": "2.0.0"}), encoding="utf-8"
    )

    assert fetch_spa.installed(tmp_path) == {"version": "2.0.0"}
    assert (frontend / "dist" / "index.html").read_text(encoding="utf-8") == (
        "the previous working interface"
    )
    assert not previous.exists()
