"""Healing a solid STEP must leave it a solid.

``Geometry.OCCSewFaces`` sews faces into a shell and stops. Without
``Geometry.OCCMakeSolids`` alongside it, OCC never re-wraps the shell in a
solid, so a STEP whose only body is one ``MANIFOLD_SOLID_BREP`` imports as zero
volumes and one parentless surface per face -- and the healing ladder that
exists to rescue a failed mesh instead hands the scope gate a body count the
manifest cannot match. The refusal that follows is raised on the healed
attempt, so it also replaces the mesh error that started the ladder.

Nothing here is mocked. Every case writes a real STEP file, imports it through
OCC at the pinned gmsh, and reads the resulting topology. The solid fixture is
written by gmsh's own STEP writer at test time rather than checked in: it is
then genuinely the format OCC round-trips, and no captured file's header rides
into the repository.

The rung definitions come from ``hornlab_mesher.step_import.OCC_HEALING_FALLBACKS``
-- the list the fallback ladder actually walks -- so a rung that gains sewing
later is covered here the day it does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.mesh.gmsh_worker import _run_in_gmsh_session
from server.mesh.imported import (
    OCC_HEALING_OPTIONS,
    _import_occ_root_bodies,
    apply_occ_healing_options,
    declared_step_bodies,
    scope_body_count,
)

from _shell_fixture import write_open_shell, write_touching_bodies


def _healing_rungs() -> list[tuple[str, tuple[str, ...]]]:
    from hornlab_mesher.step_import import OCC_HEALING_FALLBACKS

    return [(name, tuple(options)) for name, options in OCC_HEALING_FALLBACKS]


RUNGS = _healing_rungs()
SEWING_RUNGS = [item for item in RUNGS if "Geometry.OCCSewFaces" in item[1]]


def _write_solid_step(path: Path) -> Path:
    """A real single-solid STEP, written by OCC through gmsh's own writer."""

    import gmsh

    def build() -> None:
        gmsh.option.setNumber("General.Terminal", 0)
        # OCC's STEP writer reports through its own channel, which
        # ``General.Terminal`` does not reach: without this it prints a banner
        # naming the output path into every run of this suite.
        verbosity = gmsh.option.getNumber("General.Verbosity")
        gmsh.option.setNumber("General.Verbosity", 0)
        try:
            gmsh.clear()
            gmsh.model.add("solid-fixture")
            gmsh.model.occ.addBox(0.0, 0.0, 0.0, 10.0, 10.0, 10.0)
            gmsh.model.occ.synchronize()
            gmsh.write(str(path))
            gmsh.clear()
        finally:
            gmsh.option.setNumber("General.Verbosity", verbosity)

    _run_in_gmsh_session(build)
    return path


def _topology(step_path: Path, options: tuple[str, ...]) -> dict[str, object]:
    """Import under one healing rung and report what OCC ended up holding."""

    import gmsh

    declared = declared_step_bodies(step_path)

    def probe() -> dict[str, object]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.add("healing-probe")
        applied = apply_occ_healing_options(
            gmsh,
            options,
            declared_solids=declared["solid_breps"],
            void_solids=declared["void_solids"],
            assembly_path=step_path,
        )
        roots = _import_occ_root_bodies(gmsh, step_path)
        volumes = len(gmsh.model.getEntities(3))
        orphan_surfaces = len(
            [
                tag
                for _dim, tag in gmsh.model.getEntities(2)
                if len(gmsh.model.getAdjacencies(2, int(tag))[0]) == 0
            ]
        )
        inventory = scope_body_count(gmsh, roots, declared=declared)
        gmsh.clear()
        return {
            "applied": applied,
            "volumes": volumes,
            "orphan_surfaces": orphan_surfaces,
            "body_count": inventory["count"],
            "solids": inventory["solids"],
        }

    return _run_in_gmsh_session(probe)


def test_the_fixture_is_one_declared_solid(tmp_path: Path) -> None:
    """Everything below is only about solids if the file actually holds one."""

    step = _write_solid_step(tmp_path / "solid.step")
    declared = declared_step_bodies(step)

    assert declared["solid_breps"] == 1, declared
    assert declared["surface_models"] == 0, declared
    assert _topology(step, ())["volumes"] == 1, "unhealed, OCC sees the solid"


def test_sewing_alone_dissolves_the_solid(tmp_path: Path) -> None:
    """The OCC behaviour this pairing exists to cancel.

    Set by hand, deliberately, so the test states the kernel behaviour rather
    than the helper's opinion of it. If a future gmsh stops dissolving solids
    on a bare sew, this fails and the pairing can be reconsidered on evidence.
    """

    import gmsh

    step = _write_solid_step(tmp_path / "solid.step")

    def probe() -> dict[str, int]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        gmsh.model.add("bare-sew")
        for option_name in OCC_HEALING_OPTIONS:
            gmsh.option.setNumber(option_name, int(option_name == "Geometry.OCCSewFaces"))
        gmsh.option.setNumber("Geometry.OCCMakeSolids", 0)
        _import_occ_root_bodies(gmsh, step)
        volumes = len(gmsh.model.getEntities(3))
        surfaces = len(gmsh.model.getEntities(2))
        gmsh.clear()
        return {"volumes": volumes, "surfaces": surfaces}

    observed = _run_in_gmsh_session(probe)

    assert observed["volumes"] == 0, observed
    assert observed["surfaces"] == 6, "the solid comes apart into its own faces"


@pytest.mark.parametrize("rung, options", SEWING_RUNGS, ids=[name for name, _ in SEWING_RUNGS])
def test_every_sewing_rung_keeps_a_solid_solid(
    tmp_path: Path, rung: str, options: tuple[str, ...]
) -> None:
    """The fix, on the rungs the fallback ladder actually walks."""

    step = _write_solid_step(tmp_path / f"solid-{rung}.step")
    observed = _topology(step, options)

    assert observed["applied"]["Geometry.OCCMakeSolids"] == 1, observed
    assert observed["volumes"] == 1, f"the {rung} rung dissolved the solid: {observed}"
    assert observed["orphan_surfaces"] == 0, observed


@pytest.mark.parametrize("rung, options", SEWING_RUNGS, ids=[name for name, _ in SEWING_RUNGS])
def test_the_scope_gate_still_reads_one_body_after_healing(
    tmp_path: Path, rung: str, options: tuple[str, ...]
) -> None:
    """The user-visible half: healing must not manufacture a count mismatch.

    A manifest declaring one exterior body has to keep matching a file that
    declares one, whether or not the mesh needed healing to succeed.
    """

    step = _write_solid_step(tmp_path / f"solid-{rung}.step")
    unhealed = _topology(step, ())
    healed = _topology(step, options)

    assert unhealed["body_count"] == 1, unhealed
    assert healed["body_count"] == unhealed["body_count"], (unhealed, healed)
    assert healed["solids"] == 1, healed


def test_make_solids_is_written_on_every_call(tmp_path: Path) -> None:
    """Off unless this import both sews and has a solid to restore.

    Written every time, not only when on: gmsh options outlive ``gmsh.clear()``
    and the ladder runs several attempts in one session, so an option left
    unwritten is the previous rung's.
    """

    import gmsh

    step = _write_solid_step(tmp_path / "solid.step")

    def probe() -> list[float]:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.clear()
        readings = []
        for options, declared in (
            (("Geometry.OCCSewFaces",), 1),
            (("Geometry.OCCFixSmallEdges",), 1),
            ((), 1),
            (("Geometry.OCCSewFaces",), 0),
        ):
            apply_occ_healing_options(
                gmsh,
                options,
                declared_solids=declared,
                void_solids=0,
                assembly_path=step,
            )
            readings.append(gmsh.option.getNumber("Geometry.OCCMakeSolids"))
        gmsh.clear()
        return readings

    assert _run_in_gmsh_session(probe) == [1.0, 0.0, 0.0, 0.0]
    assert (
        _topology(step, ("Geometry.OCCFixSmallEdges",))["applied"]["Geometry.OCCMakeSolids"]
        == 0
    )


def test_a_surface_only_export_is_never_solidified(tmp_path: Path) -> None:
    """Rebuilding a solid is restoration, so a file with none is left alone.

    Surface exports are most of this project's traffic. An open shell has no
    closed shell to solidify, so ``OCCMakeSolids`` would be a no-op on this one
    either way -- but the option stays off regardless, because the reason to
    turn it on is a solid the sewing destroyed and there is none here.
    """

    step = write_open_shell(tmp_path / "sheet.step", faces=2)

    sewn = _topology(step, ("Geometry.OCCSewFaces",))

    assert sewn["applied"]["Geometry.OCCMakeSolids"] == 0, sewn
    assert sewn["volumes"] == 0, "no volume may be conjured from an open shell"
    assert sewn["orphan_surfaces"] == 2, sewn
    assert sewn["body_count"] == 1, sewn


def test_coincident_sheets_are_not_solidified_into_a_phantom_body(tmp_path: Path) -> None:
    """Measured, and the reason the pairing is conditional rather than blanket.

    Two exactly coincident surface bodies sew into a closed shell. Told to make
    solids, gmsh 4.15.2 turns that shell into a volume of zero mass -- and the
    scope gate then counts the same two sheets once as declared surface models
    and once as a volume nobody exported, refusing a file that is correct. The
    file declares no solid, so there is nothing to restore and the option
    stays off.
    """

    step = write_touching_bodies(
        tmp_path / "coincident.step", faces=4, names=("a", "b"), share_records=False
    )
    assert declared_step_bodies(step)["solid_breps"] == 0

    sewn = _topology(step, ("Geometry.OCCSewFaces",))

    assert sewn["applied"]["Geometry.OCCMakeSolids"] == 0, sewn
    assert sewn["volumes"] == 0, sewn
    assert sewn["body_count"] == 2, "two declared surface bodies stay two"
