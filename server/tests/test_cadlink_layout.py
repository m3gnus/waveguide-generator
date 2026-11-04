"""CAD Link keeps its lane boundaries and pure interpretation functions."""

import ast
from pathlib import Path
import subprocess
import sys

import pytest


_CADLINK = Path(__file__).resolve().parents[1] / "cadlink"
_INTERPRETATION = _CADLINK / "interpretation"
_PURE_PATHS = sorted(_INTERPRETATION.rglob("*.py"))


def test_cadlink_lane_import_contract_is_kept() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlinter.cli import lint_imports_command; lint_imports_command()",
            "--contract", "cadlink-lanes", "--no-cache",
        ],
        cwd=_CADLINK.parents[1],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


@pytest.mark.parametrize("path", _PURE_PATHS, ids=lambda path: path.stem)
def test_interpretation_functions_take_no_store_or_context(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        arguments.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None)
        forbidden = {arg.arg for arg in arguments} & {"store", "ctx"}
        assert not forbidden, f"{path.name}:{node.lineno} accepts {sorted(forbidden)}"
