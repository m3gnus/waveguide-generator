"""Byte/digest goldens captured before consolidating the serializers.

Inline sites are evaluated at their serialization boundary with representative
values substituted for the already-prepared payload. Their surrounding domain
logic remains covered by the existing caller tests. Expected bytes are frozen
in the fixture, never computed by another serializer during verification.
"""

import ast
import copy
from dataclasses import dataclass
from enum import Enum
import hashlib
import importlib
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).parent / "fixtures" / "canonical-json" / "goldens.json"


@dataclass
class Sample:
    label: str
    values: tuple


class Label(Enum):
    TEXT = "雪"


def values():
    return {
        "nested": {"z": [None, True, False, {"é": "雪/😀\n\"\\"}], "a": {"b": 2, "a": 1}},
        "numbers": {"float": 0.1, "small": 1e-7, "large": 10**40, "negative_zero": -0.0,
                    "whole_float": 1.0, "exponent": 1e20},
        "tuple": ("å", [None, False], {"z": 1, "a": 2}),
        "scalars": [None, True, False, 0.1, 1e-7, 10**40, "é雪😀"],
        "empty": {},
        "nan": {"number": float("nan")},
        "positive_infinity": [float("inf")],
        "negative_infinity": float("-inf"),
        "normalized": {"datum": Sample("é", (0.1, None)), "enum": Label.TEXT,
                       "array": np.array([0.1, 1e-7]), 7: "numeric key"},
        "string_default": {"enum": Label.TEXT, "datum": Sample("é", (0.1, None))},
    }


def serializer_calls(tree):
    """Find compact sorted JSON calls, recording their containing function."""
    found = []

    def visit(node, scope=""):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = f"{scope}.{node.name}".strip(".")
        if isinstance(node, ast.Call):
            name = ast.unparse(node.func)
            kw = {k.arg: ast.unparse(k.value) for k in node.keywords}
            if (name == "json.dumps" and kw.get("sort_keys") == "True"
                    and "separators" in kw) or name in {"canonical_ascii_json", "canonical_ascii_strict_json",
                                 "canonical_utf8_strict_json", "canonical_ascii_string_json"}:
                found.append((scope, node))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree)
    return found


def serialize(site, value):
    if site["kind"] == "helper":
        module = importlib.import_module(site["path"][:-3].replace("/", "."))
        return getattr(module, site["symbol"])(value)
    tree = ast.parse((ROOT / site["path"]).read_text())
    calls = [node for scope, node in serializer_calls(tree) if scope == site["scope"]]
    node = copy.deepcopy(calls[site["ordinal"]])
    node.args = [ast.Name(id="value", ctx=ast.Load())]
    ast.fix_missing_locations(node)
    namespace = {"json": json, "value": value}
    if site["path"] == "scripts/qualify_installer_artifact.py":
        from server.contracts.canonical import canonical_ascii_json
        namespace["canonical_ascii_json"] = canonical_ascii_json
    # Only the leaf owner is needed to execute the live serialization boundary.
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module == "server.contracts.canonical":
            exec(compile(ast.Module(body=[statement], type_ignores=[]), "<imports>", "exec"), namespace)
    return eval(compile(ast.Expression(node), "<serialization boundary>", "eval"), namespace)


GOLDENS = json.loads(FIXTURE.read_text())
CASES = [(site, case) for site in GOLDENS["sites"] for case in site["cases"]]


@pytest.mark.parametrize("site,case", CASES,
                         ids=[f'{s["path"]}:{s.get("symbol", s.get("scope"))}:{s.get("ordinal", 0)}:{c["input"]}'
                              for s, c in CASES])
def test_persisted_canonical_bytes(site, case):
    value = values()[case["input"]]
    if "error" in case:
        with pytest.raises(getattr(__import__("builtins"), case["error"])):
            serialize(site, value)
        return
    result = serialize(site, value)
    assert isinstance(result, str if site["kind"] == "inline" or site["symbol"] == "canonical_json" else bytes)
    encoded = result.encode("utf-8") if isinstance(result, str) else result
    assert encoded == bytes.fromhex(case["bytes_hex"])
    assert hashlib.sha256(encoded).hexdigest() == case["sha256"]


def test_pinned_protocol_is_unchanged():
    protocol = ROOT / "server" / "cadlink" / "wglink_protocol.py"
    assert hashlib.sha256(protocol.read_bytes()).hexdigest() == GOLDENS["pinned_protocol_sha256"]


def test_no_unowned_sorted_compact_serializers():
    # The frozen inventory prevents new inline copies outside the leaf owner.
    for package in ("server", "scripts"):
        for path in (ROOT / package).rglob("*.py"):
            if "tests" in path.parts or path.name in {"canonical.py", "wglink_protocol.py"}:
                continue
            for _, node in serializer_calls(ast.parse(path.read_text())):
                assert ast.unparse(node.func) != "json.dumps", f"{path.relative_to(ROOT)}:{node.lineno}"
