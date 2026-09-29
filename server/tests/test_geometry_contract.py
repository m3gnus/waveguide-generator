"""The shared vocabulary must agree across mesh, solver and capability readers."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

import pytest

from server.contracts import geometry


DOMAINS = [
    (1, ("x0", "y0"), "yz+xz", "quarter", (0, 1), ("quarter",)),
    (12, ("y0",), "xz", "half_xz", (1,), ("half", "half-xz")),
    (14, ("x0",), "yz", "half_yz", (0,), ("half", "half-yz")),
    (1234, (), None, "full", (), ("full",)),
]


def test_quadrant_tables_are_bijective() -> None:
    assert set(geometry.PLANE_BY_QUADRANTS) == {1, 12, 14, 1234}
    assert set(geometry.CUT_PLANES_BY_QUADRANTS) == {1, 12, 14, 1234}
    assert len(set(geometry.PLANE_BY_QUADRANTS.values())) == 4
    assert len({frozenset(planes) for planes in geometry.CUT_PLANES_BY_QUADRANTS.values()}) == 4
    for quadrants, cuts in geometry.CUT_PLANES_BY_QUADRANTS.items():
        assert len(cuts) == len(set(cuts))
        assert geometry.QUADRANTS_BY_CUT_PLANES[frozenset(cuts)] == quadrants
        native = geometry.PLANE_BY_QUADRANTS[quadrants]
        assert geometry.NATIVE_PLANE_BY_CUT_PLANES[frozenset(cuts)] == native


@pytest.mark.parametrize("quadrants,cuts,native,mode,axes,domains", DOMAINS)
def test_domain_meanings_and_round_trip(quadrants, cuts, native, mode, axes, domains) -> None:
    from server.engines.registry import EngineInfo, engine_supports_symmetry
    from server.mesh import builder

    assert builder._symmetry_plane_axes({"mesh": {"quadrants": quadrants}}) == axes
    assert builder._domain_multiplier_for_quadrants(quadrants) == 2 ** len(cuts)
    for advertised in ("full", "half", "half-xz", "half-yz", "quarter"):
        info = EngineInfo("test", True, "test", "test", symmetry_domains=(advertised,))
        assert engine_supports_symmetry(info, quadrants) == (advertised in domains)
    assert geometry.CUT_PLANES_BY_QUADRANTS[quadrants] == cuts
    assert geometry.native_symmetry_plane_for_quadrants(quadrants) == native
    assert geometry.symmetry_plane_axes_for_quadrants(quadrants) == axes
    assert geometry.MODE_BY_QUADRANTS[quadrants] == mode
    assert geometry.SYMMETRY_MODE_QUADRANTS[mode] == quadrants
    assert geometry.SYMMETRY_DOMAINS_BY_QUADRANTS[quadrants] == domains
    for ordered in (cuts, tuple(reversed(cuts))):
        resolved = geometry.imported_symmetry_from_cut_planes(ordered)
        assert resolved == geometry.ImportedSymmetry(mode, quadrants, native, ordered)
    assert geometry.quadrants_for_symmetry_planes(xz="y0" in cuts, yz="x0" in cuts) == quadrants


@pytest.mark.parametrize("cuts", [("z0",), ("x0", "x0"), ("y0", "y0"), ("x0", "z0"), (None,)])
def test_imported_cuts_keep_strict_validation(cuts) -> None:
    with pytest.raises(
        geometry.ImportedSymmetryUnsupportedError, match="unsupported imported symmetry"
    ):
        geometry.imported_symmetry_from_cut_planes(cuts)


@pytest.mark.parametrize(
    "value,leading,normalized",
    [
        (None, 0, 1),
        (True, 0, 1),
        (False, 0, 1),
        (0, 0, 1),
        (13, 13, 1),
        (12.5, 12, 12),
        ("  +14 trailing", 14, 14),
        ("1234junk", 1234, 1234),
        ("invalid", 0, 1),
        ("-12", -12, 1),
    ],
)
def test_ath_parsing_and_fallback_are_unchanged(value, leading, normalized) -> None:
    assert geometry.quadrants_leading_int(value) == leading
    assert geometry.normalise_quadrants(value) == normalized


def test_legacy_imports_alias_the_contract() -> None:
    from server.mesh import api, imported
    from server.solver import imported as solver_imported, quadrants, symmetry

    assert api.CUT_PLANES_BY_QUADRANTS is geometry.CUT_PLANES_BY_QUADRANTS
    assert quadrants._PLANE_BY_QUADRANTS is geometry.PLANE_BY_QUADRANTS
    assert quadrants.normalise_quadrants is geometry.normalise_quadrants
    assert quadrants.quadrants_leading_int is geometry.quadrants_leading_int
    assert (
        quadrants.native_symmetry_plane_for_quadrants
        is geometry.native_symmetry_plane_for_quadrants
    )
    assert symmetry.SYMMETRY_MODE_QUADRANTS is geometry.SYMMETRY_MODE_QUADRANTS
    assert solver_imported.ImportedSymmetry is geometry.ImportedSymmetry
    assert (
        solver_imported.ImportedSymmetryUnsupportedError
        is geometry.ImportedSymmetryUnsupportedError
    )
    assert (
        solver_imported.imported_symmetry_from_cut_planes
        is geometry.imported_symmetry_from_cut_planes
    )
    assert imported.imported_symmetry_from_cut_planes is geometry.imported_symmetry_from_cut_planes
    assert imported.FIRST_SOURCE_TAG == geometry.FIRST_SOURCE_TAG == 101
    assert imported.SUPPORTED_CUT_PLANES is geometry.SUPPORTED_CUT_PLANES
    assert imported.REFLECTION_AXIS is imported.DOMAIN_PLANE_AXIS is geometry.SYMMETRY_PLANE_AXIS


def test_physical_tags_match_the_pinned_mesher() -> None:
    from hornlab_mesher.tags import PHYSICAL_NAMES, SOURCE_TAGS, PhysicalGroup
    from server.mesh import builder, imported
    from server.solver import beat_imported

    assert builder.CANONICAL_SURFACE_TAGS == {1, 2, 3, 4, 12}
    assert builder.CANONICAL_SURFACE_TAGS <= PHYSICAL_NAMES.keys()
    assert builder.MOUTH_APERTURE_SURFACE_TAG == int(PhysicalGroup.MOUTH_APERTURE) == 12
    assert imported.RIGID_TAG == beat_imported.RIGID_TAG == int(PhysicalGroup.RIGID_WALL) == 1
    assert beat_imported.VELOCITY_TAG == int(PhysicalGroup.PRIMARY_SOURCE) == 2
    assert beat_imported.VELOCITY_TAG in SOURCE_TAGS
    assert beat_imported._MIRROR_AXES == {None: (), "yz": (0,), "yz+xz": (0, 1)}


def test_contract_is_a_standard_library_leaf() -> None:
    import sys

    tree = ast.parse(Path(geometry.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module.split(".")[0] in sys.stdlib_module_names
        elif isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in sys.stdlib_module_names for alias in node.names)


@pytest.mark.parametrize("error", ["ImportError", "OSError"])
def test_tags_do_not_make_the_optional_mesher_required_at_startup(error) -> None:
    script = f"""
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith("hornlab_mesher"):
        raise {error}("mesher unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from server.mesh import builder, imported
from server.solver import beat_imported
assert builder.CANONICAL_SURFACE_TAGS == {{1, 2, 3, 4, 12}}
assert builder.MOUTH_APERTURE_SURFACE_TAG == 12
assert imported.RIGID_TAG == beat_imported.RIGID_TAG == 1
assert beat_imported.VELOCITY_TAG == 2
"""
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
