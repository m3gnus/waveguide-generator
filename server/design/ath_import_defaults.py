"""WG defaults and measurement notes for unstamped ATH imports."""

from typing import Any


def apply_ath_import_defaults(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Apply ATH V2026-08c defaults and return plain import-report entries."""

    simulation = payload.setdefault("simulation", {})
    # Enclosure members, including stated zero values, prevent the default
    # baffle. An empty Mesh.Enclosure block has no members and does not.
    if simulation.get("sim_type") is None and not payload.get("enclosure"):
        simulation["sim_type"] = "infinite-baffle"

    blocks = payload.get("extra_blocks", {})
    polars = sorted(
        (name for name in blocks if name.startswith("ABEC.Polars:")),
        key=str.casefold,
    )
    if not polars:
        return []
    from .solve_block import _items, _js_number

    name = polars[0]
    items = _items(blocks[name])
    notes: list[tuple[str, str, str]] = []
    if "Distance" not in items:
        notes.append(
            (
                f"{name}.Distance",
                "",
                "ATH V2026-08c computes these polars in the far field when Distance "
                "is omitted. WG measures them at the measurement distance in "
                "Solve options (2 m unless changed).",
            )
        )
    if "NormAngle" not in items:
        notes.append(
            (
                f"{name}.NormAngle",
                "",
                "ATH V2026-08c leaves these polars unnormalised when NormAngle is "
                "omitted. WG's directivity maps always hold the Solve options "
                "normalisation angle (5 degrees unless changed) at 0 dB.",
            )
        )
    sim_type = simulation.get("sim_type")
    baffle = sim_type == "infinite-baffle" or _js_number(sim_type) == 1
    origin = _items(blocks.get("WG.Solve")).get("ObservationOrigin")
    if origin not in {"mouth", "throat"}:
        origin = "mouth" if baffle else "throat"
    for name in polars:
        items = _items(blocks[name])
        if "Offset" in items and (origin != "throat" or _js_number(items["Offset"]) != 0):
            notes.append(
                (
                    f"{name}.Offset",
                    items["Offset"],
                    "ATH measures about a point Offset mm along the axis from the "
                    f"throat. WG measures these polars about the {origin} and does "
                    "not apply Offset.",
                )
            )
    return notes
