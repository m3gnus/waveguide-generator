"""A body with interior voids is refused by the healing ladder, not repaired.

**These tests assert mass.** Every other gate in this family counts entities --
volumes, surfaces, declared bodies -- and that is precisely why the defect they
were written to catch was invisible to them: sewing a hollow body with
``Geometry.OCCMakeSolids`` returns one volume, from one declared body, with no
orphan surfaces, and passes all of them while having quietly filled the void. A
count cannot see the difference between a body and a body's convex hull. A mass
can.

The two outcomes, measured here rather than asserted from documentation
(gmsh 4.15.2, a 40 mm box minus a fully enclosed r=10 mm sphere, which OCC's own
STEP writer emits as ``BREP_WITH_VOIDS``):

    unhealed              1 volume,  7 surfaces, 59811.209795 mm^3
    sew, no MakeSolids    0 volumes, 7 surfaces, no mass at all
    sew + MakeSolids      1 volume,  6 surfaces, 64000.000000 mm^3

The first sewing result is a manufactured body-count mismatch: loud, and wrong
about why. The second is the solid box -- the void filled and its shell deleted
-- and is silent. Neither is the body that was exported, so the ladder refuses
by name instead of choosing between them.

Nothing is mocked. The fixture is written at test time through OCC's own STEP
writer, so it is genuinely the format this server round-trips, and every reading
below comes from importing that file through the production helpers.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.mesh.imported import (
    OCC_HEALING_OPTIONS,
    ImportedMeshError,
    _import_occ_root_bodies,
    apply_occ_healing_options,
    build_imported_mesh,
    declared_step_bodies,
    scope_body_count,
    step_void_solid_labels,
)

#: The fixture, in millimetres: a 40 mm cube centred on the origin with a
#: fully enclosed sphere of radius 10 mm removed from the middle of it.
BOX_MM = 40.0
VOID_RADIUS_MM = 10.0
SOLID_BOX_MASS = BOX_MM**3
HOLLOW_MASS = SOLID_BOX_MASS - (4.0 / 3.0) * math.pi * VOID_RADIUS_MM**3


def _healing_rungs() -> list[tuple[str, tuple[str, ...]]]:
    from hornlab_mesher.step_import import OCC_HEALING_FALLBACKS

    return [(name, tuple(options)) for name, options in OCC_HEALING_FALLBACKS]


SEWING_RUNGS = [
    item for item in _healing_rungs() if "Geometry.OCCSewFaces" in item[1]
]


def _write_hollow_step(path: Path) -> Path:
    """A real STEP holding one solid with one interior void, written by OCC."""

    import gmsh

    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        # OCC's STEP writer reports through its own channel, which
        # ``General.Terminal`` does not reach.
        verbosity = gmsh.option.getNumber("General.Verbosity")
        gmsh.option.setNumber("General.Verbosity", 0)
        try:
            gmsh.clear()
            gmsh.model.add("hollow-fixture")
            half = BOX_MM / 2.0
            box = gmsh.model.occ.addBox(-half, -half, -half, BOX_MM, BOX_MM, BOX_MM)
            sphere = gmsh.model.occ.addSphere(0.0, 0.0, 0.0, VOID_RADIUS_MM)
            gmsh.model.occ.cut([(3, box)], [(3, sphere)])
            gmsh.model.occ.synchronize()
            gmsh.write(str(path))
            gmsh.clear()
        finally:
            gmsh.option.setNumber("General.Verbosity", verbosity)

    _run_in_gmsh_session(build)
    return path


def _import_through_production(step_path: Path, options: tuple[str, ...]) -> dict[str, object]:
    """Import under one healing option set, exactly as the gate would.

    Returns the masses OCC ended up holding, so a filled void is visible as the
    number it is rather than as an unchanged volume count.
    """

    import gmsh

    declared = declared_step_bodies(step_path)

    def probe() -> dict[str, object]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.add("hollow-probe")
        applied = apply_occ_healing_options(
            gmsh,
            options,
            declared_solids=declared["solid_breps"],
            void_solids=declared["void_solids"],
            assembly_path=step_path,
        )
        roots = _import_occ_root_bodies(gmsh, step_path)
        volumes = [int(tag) for _dim, tag in gmsh.model.getEntities(3)]
        masses = [float(gmsh.model.occ.getMass(3, tag)) for tag in volumes]
        inventory = scope_body_count(gmsh, roots, declared=declared)
        surfaces = len(gmsh.model.getEntities(2))
        gmsh.clear()
        return {
            "applied": applied,
            "masses": masses,
            "surfaces": surfaces,
            "body_count": inventory["count"],
            "solids": inventory["solids"],
        }

    return _run_in_gmsh_session(probe)


def _import_by_hand(step_path: Path, options: tuple[str, ...]) -> dict[str, object]:
    """The same import with the options set directly, bypassing every gate.

    Deliberately not through the production helper: these cases exist to state
    what the OCC kernel does, so that the refusal above them rests on measured
    behaviour rather than on this module's opinion of it. If a future gmsh stops
    filling voids, this fails and the refusal can be reconsidered on evidence.
    """

    import gmsh

    def probe() -> dict[str, object]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.add("hollow-kernel")
        for option_name in OCC_HEALING_OPTIONS:
            gmsh.option.setNumber(option_name, int(option_name in options))
        gmsh.option.setNumber(
            "Geometry.OCCMakeSolids", int("Geometry.OCCMakeSolids" in options)
        )
        _import_occ_root_bodies(gmsh, step_path)
        volumes = [int(tag) for _dim, tag in gmsh.model.getEntities(3)]
        masses = [float(gmsh.model.occ.getMass(3, tag)) for tag in volumes]
        surfaces = len(gmsh.model.getEntities(2))
        gmsh.clear()
        return {"masses": masses, "surfaces": surfaces}

    return _run_in_gmsh_session(probe)


# ---------------------------------------------------------------------------
# the fixture


def test_the_fixture_really_is_one_hollow_solid(tmp_path: Path) -> None:
    """Everything below is only about voids if the file actually holds one."""

    step = _write_hollow_step(tmp_path / "hollow.step")
    text = step.read_text(encoding="ascii", errors="replace")

    assert len(re.findall(r"\bBREP_WITH_VOIDS\s*\(", text)) == 1, "OCC wrote no void body"
    assert not re.findall(r"\bMANIFOLD_SOLID_BREP\s*\(", text), (
        "BREP_WITH_VOIDS is the most specific type, so the plain spelling is "
        "absent -- which is why the old solid rule read this file as no solids"
    )


def test_a_hollow_body_counts_as_one_body(tmp_path: Path) -> None:
    """Counting and healing are different questions; this is the counting one.

    A box with a bubble in it is one body to CAD and one body to the manifest,
    so ``n_bodies_expected: 1`` has to keep matching it. Before ``BREP_WITH_VOIDS``
    was a known spelling the file declared zero solids, and the scope gate got
    its 1 only from OCC having imported a volume.
    """

    step = _write_hollow_step(tmp_path / "hollow.step")
    declared = declared_step_bodies(step)

    assert declared["solid_breps"] == 1, declared
    assert declared["void_solids"] == 1, declared
    assert declared["surface_models"] == 0, declared

    observed = _import_through_production(step, ())
    assert observed["body_count"] == 1, observed
    assert observed["solids"] == 1, observed


# ---------------------------------------------------------------------------
# the mass, which is the whole point


def test_the_unhealed_import_keeps_the_void(tmp_path: Path) -> None:
    """The mass of the body as exported, through the production path, unchanged.

    This is the number every other assertion in this file is measured against.
    """

    step = _write_hollow_step(tmp_path / "hollow.step")
    observed = _import_through_production(step, ())

    assert observed["masses"] == pytest.approx([HOLLOW_MASS], rel=1.0e-9), observed
    assert observed["surfaces"] == 7, "six box faces and the void's sphere"
    assert observed["applied"]["Geometry.OCCMakeSolids"] == 0, observed


@pytest.mark.parametrize("rung, options", SEWING_RUNGS, ids=[name for name, _ in SEWING_RUNGS])
def test_no_sewing_rung_can_change_the_mass_silently(
    tmp_path: Path, rung: str, options: tuple[str, ...]
) -> None:
    """The assertion this gate family was missing.

    Every rung the ladder walks either refuses out loud or hands back the mass
    it was given. There is no third outcome, and "one volume, one body, right
    counts, wrong mass" is exactly the third outcome that used to be reachable.
    """

    step = _write_hollow_step(tmp_path / f"hollow-{rung}.step")

    with pytest.raises(ImportedMeshError) as refusal:
        _import_through_production(step, options)

    assert "interior voids" in str(refusal.value)

    # And the refusal is the only thing standing between the caller and the
    # filled void: with the gate bypassed, this same rung returns a solid box.
    bypassed = _import_by_hand(step, options + ("Geometry.OCCMakeSolids",))
    assert bypassed["masses"] == pytest.approx([SOLID_BOX_MASS], rel=1.0e-9), bypassed
    assert bypassed["masses"] != pytest.approx([HOLLOW_MASS], rel=1.0e-3), (
        "the void was filled, and no entity count can see it"
    )


@pytest.mark.parametrize("rung, options", SEWING_RUNGS, ids=[name for name, _ in SEWING_RUNGS])
def test_sewing_without_make_solids_dissolves_the_hollow_body(
    tmp_path: Path, rung: str, options: tuple[str, ...]
) -> None:
    """The other evil, so the refusal names two measured outcomes and not one."""

    step = _write_hollow_step(tmp_path / f"hollow-{rung}.step")
    bypassed = _import_by_hand(step, options)

    assert bypassed["masses"] == [], "no volume survives a bare sew"
    assert bypassed["surfaces"] == 7, bypassed


def test_a_hollow_body_that_meshes_unhealed_still_works(tmp_path: Path) -> None:
    """The scope of the refusal, proved through the whole production function.

    Only the sewing rungs are unsafe, and they are only ever reached when the
    unhealed mesh has already failed. A hollow export that meshes as it stands
    goes through ``build_imported_mesh`` untouched: the scope gate matches its
    one declared body against ``n_bodies_expected: 1``, the healing report says
    it was never attempted, and a mesh comes out.
    """

    step = _write_hollow_step(tmp_path / "hollow.step")
    manifest = {
        "assembly": {
            "file": "assembly.step",
            "n_bodies_expected": 1,
            "bbox_mm": [0, 0, 0, 1, 1, 1],
        },
        "sources": [],
        "instances": [],
        "coordinate_system": {"solver_anchor_instance_id": None},
        "scope": {"included": [{"file": "assembly.step"}]},
    }
    sizes = {
        "rigid_size_mm": 5.0,
        "transition_mm": 5.0,
        "source_size_mm": {},
        "max_frequency_hz": 1000.0,
    }

    result = _run_in_gmsh_session(
        lambda: build_imported_mesh(step, manifest, sizes, include_viewport_mesh=False)
    )

    assert result["healing"]["attempted"] is False, result["healing"]
    assert result["healing"]["trigger"] == "unhealed-mesh-succeeded", result["healing"]
    assert result["metadata"]["topology"]["triangles"] > 0, result["metadata"]["topology"]


# ---------------------------------------------------------------------------
# the refusal


@pytest.mark.parametrize("rung, options", SEWING_RUNGS, ids=[name for name, _ in SEWING_RUNGS])
def test_the_refusal_names_the_body_the_file_and_the_remedy(
    tmp_path: Path, rung: str, options: tuple[str, ...]
) -> None:
    step = _write_hollow_step(tmp_path / f"named-{rung}.step")

    with pytest.raises(ImportedMeshError) as refusal:
        _import_through_production(step, options)
    message = str(refusal.value)

    assert f"named-{rung}.step" in message, message
    assert "unnamed hollow body 1" in message, message
    assert "dissolves the body" in message, message
    assert "filling the void" in message, message
    assert "Repair the body in CAD" in message, message


def test_the_refusal_uses_the_body_name_when_the_file_has_one(tmp_path: Path) -> None:
    """OCC writes ``BREP_WITH_VOIDS('',...)``; CAD exports usually do not."""

    step = _write_hollow_step(tmp_path / "hollow.step")
    named = tmp_path / "named.step"
    named.write_text(
        step.read_text(encoding="ascii", errors="replace").replace(
            "BREP_WITH_VOIDS('',", "BREP_WITH_VOIDS('rear chamber',", 1
        ),
        encoding="ascii",
    )

    assert step_void_solid_labels(named) == ["'rear chamber'"]
    with pytest.raises(ImportedMeshError, match="rear chamber"):
        _import_through_production(named, ("Geometry.OCCSewFaces",))


def test_a_solid_without_voids_still_sews(tmp_path: Path) -> None:
    """The refusal is about voids, not about solids: the pairing still applies."""

    import gmsh

    step = tmp_path / "solid.step"

    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        verbosity = gmsh.option.getNumber("General.Verbosity")
        gmsh.option.setNumber("General.Verbosity", 0)
        try:
            gmsh.clear()
            gmsh.model.add("solid-fixture")
            gmsh.model.occ.addBox(0.0, 0.0, 0.0, BOX_MM, BOX_MM, BOX_MM)
            gmsh.model.occ.synchronize()
            gmsh.write(str(step))
            gmsh.clear()
        finally:
            gmsh.option.setNumber("General.Verbosity", verbosity)

    _run_in_gmsh_session(build)

    assert declared_step_bodies(step)["void_solids"] == 0
    observed = _import_through_production(step, ("Geometry.OCCSewFaces",))

    assert observed["applied"]["Geometry.OCCMakeSolids"] == 1, observed
    assert observed["masses"] == pytest.approx([SOLID_BOX_MASS], rel=1.0e-9), observed


def test_an_unknown_healing_option_is_refused_rather_than_dropped(tmp_path: Path) -> None:
    """A name this module does not know used to be silently ignored.

    The viewport rebuild replays option names out of a stored bundle, so the
    difference between "applied" and "quietly discarded" is a repair the user
    was told happened and did not.
    """

    import gmsh

    step = _write_hollow_step(tmp_path / "hollow.step")

    def probe() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        apply_occ_healing_options(
            gmsh,
            ("Geometry.OCCFixSmallEdges", "Geometry.OCCFixSmallHoles"),
            declared_solids=1,
            void_solids=0,
            assembly_path=step,
        )

    with pytest.raises(ImportedMeshError, match="Geometry.OCCFixSmallHoles"):
        _run_in_gmsh_session(probe)
