"""Extract smoke-tested viewport payloads from the read-only v1 application."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any


SPIKE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SPIKE_ROOT.parent
V1_ROOT = REPO_ROOT.parent / "Waveguide Generator"
V1_SERVER = V1_ROOT / "server"
V1_DATABASE = V1_SERVER / "data" / "simulations.db"
FAMILIES = ("OSSE", "R-OSSE", "ICW", "FREEFORM")
FILE_NAMES = {
    "OSSE": "osse.json",
    "R-OSSE": "rosse.json",
    "ICW": "icw.json",
    "FREEFORM": "freeform.json",
}


def _load_builder() -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Import only the empty solver package and its mesher adapter."""

    sys.path.insert(0, str(V1_SERVER))
    from solver.mesher_adapter import build_viewport_geometry

    return build_viewport_geometry


def _database_uri() -> str:
    if not V1_DATABASE.is_file():
        raise FileNotFoundError(f"v1 database not found: {V1_DATABASE}")
    return f"{V1_DATABASE.as_uri()}?mode=ro"


def _real_payloads() -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield newest large real payloads first, without ever opening v1 writable."""

    query = """
        SELECT config_json
        FROM simulation_jobs
        ORDER BY length(config_json) DESC, created_at DESC
    """
    with sqlite3.connect(_database_uri(), uri=True) as connection:
        rows = connection.execute(query).fetchall()
    for (raw_config,) in rows:
        config = json.loads(raw_config)
        payload = config.get("options", {}).get("mesh", {}).get("waveguide_params")
        if not isinstance(payload, dict):
            continue
        family = str(payload.get("formula_type") or "").upper()
        if family in FAMILIES:
            yield family, payload


def _icw_defaults() -> dict[str, Any]:
    """Minimal current defaults from v1 src/config/schema.js."""

    return {
        "formula_type": "ICW",
        "r0": 12.7,
        "a0": 15.0,
        "L": 120.0,
        "R": 150.0,
        "termination": "flat_baffle",
        "n_coeff": 6,
        "coverage_angle": 0,
        "hold_start": 0.3,
        "hold_end": 0.7,
        "n_angular": 96,
        "n_length": 48,
        "quadrants": 1234,
        "wall_thickness": 0.0,
        "sim_type": 2,
    }


def _freeform_test_payload() -> dict[str, Any]:
    """Current valid shape from v1 server/tests/test_freeform_server.py."""

    return {
        "formula_type": "FREEFORM",
        "a0": 15.5,
        "profile_h": {
            "points": [[0.0, 12.7], [60.0, 80.0, 25.0, 1.4], [120.0, 160.0]],
            "throat_angle_deg": 15.5,
            "mouth_angle_deg": 70.0,
            "throat_tangent_scale": 1.1,
            "mouth_tangent_scale": 0.9,
        },
        "profile_v": {
            "points": [[0.0, 12.7], [60.0, 60.0, -10.0], [120.0, 110.0]],
            "throat_angle_deg": 15.5,
            "mouth_angle_deg": 60.0,
            "throat_tangent_scale": 1.2,
            "mouth_tangent_scale": 0.8,
        },
        "cross_sections": [
            {"t": 0.0, "shape": "circle"},
            {
                "t": 0.4,
                "shape": "rounded_rectangle",
                "corner_radius_mm": 20.0,
            },
            {
                "t": 1.0,
                "shape": "rounded_rectangle",
                "corner_radius_mm": 35.0,
            },
        ],
        "overshoot_policy": "allow",
        "inflection_policy": "warn",
        "n_angular": 96,
        "n_length": 48,
        "quadrants": 1234,
        "wall_thickness": 0.0,
        "sim_type": 2,
    }


def _smoke(
    builder: Callable[[Mapping[str, Any]], dict[str, Any]],
    family: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    result = builder(payload)
    grid = result.get("grid") or {}
    if result.get("formula") != family:
        raise RuntimeError(f"{family}: viewport returned formula {result.get('formula')!r}")
    if not grid.get("inner_points"):
        raise RuntimeError(f"{family}: viewport returned no inner point grid")
    return result


def _select_payloads(
    builder: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    selected: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    for family, payload in _real_payloads():
        if family in selected:
            continue
        try:
            _smoke(builder, family, payload)
        except Exception as error:  # noqa: BLE001 - keep trying persisted candidates
            print(f"skip invalid real {family} payload: {error}", file=sys.stderr)
            continue
        selected[family] = payload
        sources[family] = "v1 simulation_jobs"

    fallbacks = {
        "ICW": (_icw_defaults(), "v1 src/config/schema.js defaults"),
        "FREEFORM": (
            _freeform_test_payload(),
            "v1 server/tests/test_freeform_server.py",
        ),
    }
    for family, (payload, source) in fallbacks.items():
        if family not in selected:
            _smoke(builder, family, payload)
            selected[family] = payload
            sources[family] = source
    return selected, sources


def main() -> int:
    builder = _load_builder()
    selected, sources = _select_payloads(builder)
    missing = [family for family in FAMILIES if family not in selected]
    if missing:
        raise RuntimeError(f"no valid payload found for: {', '.join(missing)}")

    # All payloads pass again immediately before any file is written. This keeps
    # a failed extraction from leaving a partially refreshed fixture set.
    for family in FAMILIES:
        _smoke(builder, family, selected[family])

    output_dir = Path(__file__).resolve().parent
    for family in FAMILIES:
        destination = output_dir / FILE_NAMES[family]
        destination.write_text(
            json.dumps(selected[family], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {destination.relative_to(SPIKE_ROOT)} ({sources[family]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
