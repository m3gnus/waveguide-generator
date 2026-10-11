"""Interpretation functions cannot accept persistence or preparation contexts."""

import ast
from pathlib import Path

import pytest


_CADLINK = Path(__file__).resolve().parents[1] / "cadlink"
_INTERPRETATION = _CADLINK / "interpretation"
_PURE_PATHS = sorted(_INTERPRETATION.rglob("*.py"))


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
