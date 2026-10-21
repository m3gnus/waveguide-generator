"""Shared driver for the frozen FREEFORM corpus and its opt-in regenerator.

Only STEP's FILE_NAME timestamp and OCC's writer sequence are normalized.
All geometry, topology, ordering, precision and other header bytes are retained.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from server.contracts.canonical import canonical_ascii_strict_json
from server.design.schema import DesignConfig
from server.design.textcfg import parse, serialize
from server.exports.core import build_step_solid
from server.mesh.builder import _solver_mesher_config, build_solver_mesh
from server.preview.translate import design_to_mesher_config

DATA = Path(__file__).parent / "data" / "freeform_oldconfig_corpus"
MANIFEST = json.loads((DATA / "manifest.json").read_text())
CASES = MANIFEST["cases"]


def load_case(case: dict[str, Any]) -> DesignConfig:
    """Use the native file reader, or the typed autosave reader with migrations."""
    path = DATA / case["file"]
    if path.suffix == ".json":
        # DesignConfig's before-validator owns apply_migrations, including mm->t.
        return DesignConfig.model_validate(json.loads(path.read_text()))
    return parse(path.read_text()).design


def freeform_state(design: DesignConfig) -> dict[str, Any]:
    payload = design.model_dump(mode="json")

    def numeric_state(value):
        # Expr.raw records text spelling; preserve exact numeric values, including
        # every t and angle, rather than comparing 12.7 with its source string.
        if isinstance(value, dict):
            if set(value) == {"raw", "value"}:
                return value["value"] if value["value"] is not None else value
            return {key: numeric_state(child) for key, child in value.items()}
        if isinstance(value, list):
            return [numeric_state(child) for child in value]
        return value

    return {
        key: numeric_state(payload[key])
        for key in (
            "length",
            "profile_h",
            "profile_v",
            "cross_sections",
            "inflection_policy",
        )
    }


def resolved_config(design: DesignConfig) -> dict[str, Any]:
    return {
        "translated": design_to_mesher_config(design),
        "solver": _solver_mesher_config(design),
    }


def canonical(value: Any) -> str:
    return canonical_ascii_strict_json(value) + "\n"


def normalize_step(text: str) -> str:
    # OCC writes e.g. FILE_NAME('Open CASCADE Shape Model','2026-10-21T...').
    # Process-global writer counters occur in PRODUCT identifiers and assembly
    # usage labels. Do not remove FILE_NAME or any DATA records.
    text, timestamps = re.subn(
        r"(FILE_NAME\('[^']*',\s*')\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(')",
        r"\g<1>2000-01-01T00:00:00\2",
        text,
    )
    if timestamps != 1:
        raise ValueError("STEP must contain exactly one FILE_NAME timestamp")
    text = re.sub(
        r"(Open CASCADE STEP translator \d+\.\d+(?:\.\d+)? )\d+(?=[.'])",
        r"\g<1>0",
        text,
    )
    # OCC also keeps a process-global assembly-usage writer counter. Preserve
    # its sequence and every reference, subtracting only the initial offset.
    usage = re.compile(r"(NEXT_ASSEMBLY_USAGE_OCCURRENCE\(')(\d+)(')")
    first = usage.search(text)
    if first:
        offset = int(first[2]) - 1
        text = usage.sub(lambda m: f"{m[1]}{int(m[2]) - offset}{m[3]}", text)
    return text


async def artifact(design: DesignConfig, kind: str) -> dict[str, str]:
    """Use public builders, bypassing mesh cache so repeats exercise Gmsh."""
    if kind == "mesh":
        result = await build_solver_mesh(design, {}, force_rebuild=True)
        content = result["msh_text"]
    elif kind == "step":
        content = normalize_step((await build_step_solid(design)).step_text)
    else:
        raise ValueError(kind)
    return {"sha256": sha256(content.encode("utf-8")).hexdigest()}


def roundtrip_state(design: DesignConfig) -> dict[str, Any]:
    return freeform_state(parse(serialize(design)).design)


def build_twice(case: dict[str, Any], kind: str) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        first = await observe_artifact(case, kind)
        second = await observe_artifact(case, kind)
        if first != second:
            raise AssertionError(f"{kind} is not deterministic: {first} != {second}")
        return first

    return asyncio.run(run())


def error_record(exc: Exception) -> dict[str, Any]:
    return {"error": {"type": type(exc).__name__, "message": str(exc)}}


def config_snapshot(case: dict[str, Any]) -> dict[str, Any]:
    try:
        design = load_case(case)
        return {"config": resolved_config(design), "freeform": freeform_state(design)}
    except Exception as exc:
        return error_record(exc)


async def observe_artifact(case: dict[str, Any], kind: str) -> dict[str, Any]:
    from server.mesh.child import MesherCrashError

    try:
        return await artifact(load_case(case), kind)
    except MesherCrashError:
        # A timeout or native crash is infrastructure evidence to investigate,
        # never a geometry refusal to bless as the expected compatibility result.
        raise
    except Exception as exc:
        return error_record(exc)
