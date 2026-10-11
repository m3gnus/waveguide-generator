"""Preparation must not depend on the job admission shims."""

import ast
from pathlib import Path


def test_preparation_does_not_import_job_shims() -> None:
    source = Path(__file__).resolve().parents[1] / "cadlink" / "preparation.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    forbidden: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
            names.extend(alias.name for alias in node.names)
        else:
            continue
        if any("job_shims" in name.split(".") for name in names):
            forbidden.append(node.lineno)
    assert not forbidden, f"Preparation imports job_shims on lines {sorted(forbidden)}"
