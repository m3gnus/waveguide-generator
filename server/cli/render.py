"""Stable machine and compact human renderers for CLI validation reports."""

from __future__ import annotations

import json
from typing import Any, TextIO


def _line(stream: TextIO, label: str, value: object) -> None:
    print(f"{label:<13} {value}", file=stream)


def render_json(payload: dict[str, Any], stream: TextIO) -> None:
    json.dump(payload, stream, indent=2, sort_keys=True)
    print(file=stream)


def render_human(payload: dict[str, Any], stream: TextIO) -> None:
    refusals = list(payload.get("refusals") or [])
    _line(stream, "File", payload["file"])
    _line(stream, "Status", "invalid" if refusals else "valid")
    if payload.get("dialect") is not None:
        _line(stream, "Dialect", payload["dialect"])
    _line(stream, "Settings", payload["settingsSource"])

    migrations = list(payload.get("migrationsApplied") or [])
    migration_names = [
        str(item.get("name")) if isinstance(item, dict) else str(item)
        for item in migrations
    ]
    _line(
        stream,
        "Migrations",
        ", ".join(migration_names) if migration_names else "none",
    )

    frequencies = payload.get("frequencies")
    if isinstance(frequencies, dict):
        _line(
            stream,
            "Frequencies",
            f"{frequencies['start']:g}-{frequencies['end']:g} Hz, "
            f"{frequencies['count']} {frequencies['spacing']} points "
            f"({frequencies['source']})",
        )

    engine = payload["engine"]
    resolved = engine.get("resolved") or "unresolved"
    _line(stream, "Engine", f"{engine['requested']} -> {resolved}")
    _line(stream, "Available", "yes" if engine.get("available") else "no")
    if engine.get("reason"):
        _line(stream, "Engine reason", engine["reason"])

    symmetry = payload.get("symmetry")
    if isinstance(symmetry, dict):
        _line(
            stream,
            "Symmetry",
            f"{symmetry['requested']} -> {symmetry['resolved']} "
            f"(quadrants {symmetry['resolvedQuadrants']})",
        )

    solve_path = payload.get("solvePath")
    if isinstance(solve_path, dict):
        _line(stream, "Solve path", solve_path["predicted"])
        for reason in solve_path.get("reasons") or []:
            _line(stream, "Path reason", reason)

    mesh = payload.get("mesh")
    if isinstance(mesh, dict):
        valid = bool((mesh.get("integrity") or {}).get("valid"))
        _line(
            stream,
            "Mesh",
            f"{mesh['triangles']:,} triangles, {mesh['vertices']:,} vertices, "
            f"integrity {'valid' if valid else 'invalid'}",
        )
        for warning in mesh.get("warnings") or []:
            _line(stream, "Warning", warning)
    elif not refusals:
        _line(stream, "Mesh", "skipped")

    for refusal in refusals:
        _line(stream, "Refusal", refusal)


def render_validation(
    payload: dict[str, Any],
    *,
    json_output: bool,
    stream: TextIO,
) -> None:
    if json_output:
        render_json(payload, stream)
    else:
        render_human(payload, stream)


__all__ = ["render_human", "render_json", "render_validation"]
