"""Coupled cardioid matrices and synthesis use completed physical apertures."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from server.cadlink.interpretation.domain_interpretation import source_completion_factors
from server.jobs.models import ImportedGeometrySource
from server.solver import metal
from test_imported_jobs import _coupled_failure_fixture, _native_result_3f


@dataclass(frozen=True)
class _Config:
    progress_callback: Any = None
    on_frequency_result: Any = None
    source_velocity_profiles: Any = None


def _aperture_mesh(path: Path, planes: tuple[str, ...], port_kind: str) -> dict[str, float]:
    """Two rectangular apertures; disconnected patches share a port tag."""
    points = []
    triangles = []
    areas = {"source-mf": 0.0, "PORT_EXIT": 0.0}

    def rectangle(source, tag, x0, x1, y0, y1, z):
        start = len(points) + 1
        points.extend([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)])
        triangles.extend([(tag, start, start + 1, start + 2), (tag, start, start + 2, start + 3)])
        areas[source] += (x1 - x0) * (y1 - y0)

    rectangle("source-mf", 101, 0.0 if "x0" in planes else -0.07, 0.07,
              0.0 if "y0" in planes else -0.075, 0.075, 0.2)
    def ports(x0, x1, y0, y1):
        # Build the actual full model, including every mirrored whole port.
        # A reduced mesh retains the positive side of each active plane.
        for sx in (-1, 1):
            for sy in (-1, 1):
                left, right = sorted((sx * x0, sx * x1))
                bottom, top = sorted((sy * y0, sy * y1))
                if "x0" in planes:
                    left = max(0.0, left)
                if "y0" in planes:
                    bottom = max(0.0, bottom)
                if right > left and top > bottom:
                    rectangle("PORT_EXIT", 10, left, right, bottom, top, 0.0)

    if port_kind in {"cut", "mixed"}:
        ports(0.0, 0.05, 0.0, 0.05)
    if port_kind == "x-only":
        ports(0.0, 0.05, 0.15, 0.25)
    if port_kind == "paired":
        # Exactly two whole exits in full, one whole exit on an x half cut.
        ports(0.15, 0.25, 0.0, 0.05)
    if port_kind in {"whole", "mixed"}:
        ports(0.2, 0.3, 0.2, 0.3)
    path.write_text("\n".join([
        "$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", str(len(points)),
        *(f"{i} {x:.12g} {y:.12g} {z:.12g}" for i, (x, y, z) in enumerate(points, 1)),
        "$EndNodes", "$Elements", str(len(triangles)),
        *(f"{i} 2 2 {tag} {tag} {a} {b} {c}" for i, (tag, a, b, c) in enumerate(triangles, 1)),
        "$EndElements", "",
    ]), encoding="utf-8")
    return areas


def _case(tmp_path, planes, port_kind, *, wg_cut=False, port_area_source="user"):
    mesh = tmp_path / ("mesh-" + str(len(planes)) + ".msh")
    areas = _aperture_mesh(mesh, planes, port_kind)
    tags = {"source-mf": 101, "PORT_EXIT": 10}
    factors = source_completion_factors({"msh_text": mesh.read_text(), "source_tags": tags}, planes)
    request, _ = _coupled_failure_fixture()
    values = request.geometry.model_dump()
    values.update(bem_port_area_m2=areas["PORT_EXIT"], port_area_source=port_area_source)
    if port_area_source == "bem_aperture":
        # Validate the real wire contract: both requested areas describe the
        # measured mesh. The campaign later resolves their physical basis.
        values["model_port_area_m2"] = areas["PORT_EXIT"]
    geometry = ImportedGeometrySource.model_validate(values)
    record = {
        "source_tags": tags,
        "symmetry": {"domain_planes": list(planes), "cut_planes": list(planes) if wg_cut else []},
        "sources": [
            {"id": source, "role": "MF" if source == "source-mf" else "PASSIVE_CARDIOID",
             "observed": {"total_area_mm2": area * (factors.get(source, 1.0) if wg_cut else 1.0) * 1e6}}
            for source, area in areas.items()
        ],
        "domain_decision": {"sources": {"by_id": {
            source: {"completion_factor": 1.0 if wg_cut else factors.get(source, 1.0)}
            for source in areas
        }}},
    }
    return mesh, geometry, record, areas


def _physical_matrix():
    # Unequal mutual entries expose an accidental row scaling or transposition.
    return np.broadcast_to(np.array([[120 + 25j, 10 + 2j], [8 + 3j, 100 + 20j]]), (3, 2, 2)).copy()


@pytest.mark.parametrize("port_area_source", ["user", "bem_aperture"])
@pytest.mark.parametrize("port_kind", ["cut", "whole", "paired", "x-only", "mixed"])
@pytest.mark.parametrize("wg_cut", [False, True], ids=["cad-cut", "wg-cut"])
def test_half_and_quarter_coupled_synthesis_match_full(
    tmp_path, monkeypatch, port_kind, wg_cut, port_area_source,
):
    full_result = None
    full_payload = None
    physical_matrix = _physical_matrix()
    full_port_area = {"cut": 0.01, "whole": 0.04, "paired": 0.02, "x-only": 0.02, "mixed": 0.05}[port_kind]
    for planes in [(), ("x0",), ("x0", "y0")]:
        mesh, geometry, record, areas = _case(
            tmp_path, planes, port_kind, wg_cut=wg_cut, port_area_source=port_area_source,
        )
        # Independent physical areas, not completion helper output: the native
        # matrix divides the same receiver pressure by reduced source flows.
        factors = np.array([0.021 / areas["source-mf"], full_port_area / areas["PORT_EXIT"]])
        raw_matrix = physical_matrix * factors[None, None, :]
        raw = metal.radiation_impedance.RadiationImpedanceResult(
            frequencies_hz=np.array([100., 500., 1000.]),
            aperture_names=["MF", "PORT_EXIT"],
            aperture_area_m2={"MF": areas["source-mf"], "PORT_EXIT": areas["PORT_EXIT"]},
            impedance_matrix=raw_matrix, solver_logs=[],
        )
        monkeypatch.setattr(metal.radiation_impedance, "solve_aperture_matrix", lambda *args: raw)
        campaign = metal._run_passive_cardioid_campaign(
            mesh, _Config(), geometry, record, raw.frequencies_hz,
            stage_callback=None, cancellation_callback=None,
        )
        if not planes:
            assert areas["PORT_EXIT"] == pytest.approx(full_port_area)
        result = campaign["result"]
        np.testing.assert_allclose(result.impedance_matrix, physical_matrix, rtol=1e-14)
        assert result.aperture_area_m2 == pytest.approx({"MF": 0.021, "PORT_EXIT": full_port_area})
        assert campaign["actual_bem_port_area_m2"] == pytest.approx(full_port_area)
        # No native result mutation, and source columns alone were rescaled.
        assert raw.impedance_matrix.tobytes() == raw_matrix.tobytes()
        mf, port = _native_result_3f(), _native_result_3f()
        mf.pressure_complex *= np.array([1 + .2j, 0.3 + .4j, 0.2 - .1j])
        port.pressure_complex *= np.array([.2 - .1j, -.3 + .1j, -.1 + .2j])
        mf.sphere_pressure_complex = mf.pressure_complex[:, 0, :].copy()
        port.sphere_pressure_complex = port.pressure_complex[:, 0, :].copy()
        combined, payload, _ = metal._coupled_cardioid_result(
            campaign, geometry, record, {"mf": mf, "port": port},
        )
        if not planes:
            # Exact legacy full-model campaign, artifact and synthesis.
            legacy = {**campaign, "result": raw, "actual_bem_port_area_m2": areas["PORT_EXIT"]}
            old, old_payload, _ = metal._coupled_cardioid_result(legacy, geometry, record, {"mf": mf, "port": port})
            assert result.impedance_matrix.tobytes() == raw_matrix.tobytes()
            assert combined.pressure_complex.tobytes() == old.pressure_complex.tobytes()
            assert combined.directivity_db.tobytes() == old.directivity_db.tobytes()
            assert payload["cone_volume_velocity"].tobytes() == old_payload["cone_volume_velocity"].tobytes()
            assert campaign["artifact"] == metal._radiation_matrix_npz(
                raw, metal.radiation_impedance.matrix_diagnostics(raw),
                aperture_tags=campaign["aperture_tags"], port_names=campaign["port_names"],
            )
            full_result, full_payload = combined, payload
        else:
            for field in ["pressure_complex", "sphere_pressure_complex"]:
                actual, expected = getattr(combined, field), getattr(full_result, field)
                np.testing.assert_allclose(actual, expected, rtol=1e-13)
                assert np.max(np.abs(20 * np.log10(np.abs(actual / expected)))) < 0.3
            np.testing.assert_allclose(combined.directivity_db, full_result.directivity_db, atol=1e-12)
            for field in ["cone_volume_velocity", "port_volume_velocity", "electrical_input_impedance_ohm"]:
                np.testing.assert_allclose(payload[field], full_payload[field], rtol=1e-13)
        assert payload["model_port_area_m2"] == pytest.approx(
            full_port_area if port_area_source == "bem_aperture" else geometry.model_port_area_m2
        )
        with np.load(BytesIO(campaign["artifact"]), allow_pickle=False) as data:
            np.testing.assert_allclose(data["solver_impedance_matrix"], physical_matrix, rtol=1e-14)
            np.testing.assert_allclose(data["aperture_area_m2"], [0.021, full_port_area])


def test_reduced_campaign_still_checks_meshed_port_identity(tmp_path, monkeypatch):
    mesh, geometry, record, areas = _case(tmp_path, ("x0",), "cut")
    raw = metal.radiation_impedance.RadiationImpedanceResult(
        frequencies_hz=np.array([100., 500., 1000.]), aperture_names=["MF", "PORT_EXIT"],
        aperture_area_m2={"MF": areas["source-mf"], "PORT_EXIT": areas["PORT_EXIT"]},
        impedance_matrix=_physical_matrix(), solver_logs=[],
    )
    monkeypatch.setattr(metal.radiation_impedance, "solve_aperture_matrix", lambda *args: raw)
    geometry = geometry.model_copy(update={"bem_port_area_m2": 0.01})
    with pytest.raises(ValueError, match="does not match the requested face identity"):
        metal._run_passive_cardioid_campaign(
            mesh, _Config(), geometry, record, raw.frequencies_hz,
            stage_callback=None, cancellation_callback=None,
        )


def test_full_bem_aperture_preserves_requested_area_within_measurement_tolerance(tmp_path, monkeypatch):
    mesh, geometry, record, areas = _case(tmp_path, (), "paired", port_area_source="bem_aperture")
    values = geometry.model_dump()
    requested_area = areas["PORT_EXIT"] * (1.0 + 5e-7)
    values.update(bem_port_area_m2=requested_area, model_port_area_m2=requested_area)
    geometry = ImportedGeometrySource.model_validate(values)
    raw = metal.radiation_impedance.RadiationImpedanceResult(
        frequencies_hz=np.array([100., 500., 1000.]), aperture_names=["MF", "PORT_EXIT"],
        aperture_area_m2={"MF": areas["source-mf"], "PORT_EXIT": areas["PORT_EXIT"]},
        impedance_matrix=_physical_matrix(), solver_logs=[],
    )
    monkeypatch.setattr(metal.radiation_impedance, "solve_aperture_matrix", lambda *args: raw)
    campaign = metal._run_passive_cardioid_campaign(
        mesh, _Config(), geometry, record, raw.frequencies_hz,
        stage_callback=None, cancellation_callback=None,
    )
    bases = {"mf": _native_result_3f(), "port": _native_result_3f()}
    actual, payload, _ = metal._coupled_cardioid_result(campaign, geometry, record, bases)
    # Legacy coupling used the requested model area, independent of provenance.
    # The user-area branch still uses that exact value and serves as the oracle.
    values["port_area_source"] = "user"
    legacy_geometry = ImportedGeometrySource.model_validate(values)
    expected, old_payload, _ = metal._coupled_cardioid_result(campaign, legacy_geometry, record, bases)
    assert payload["model_port_area_m2"] == requested_area
    for field in ["pressure_complex", "directivity_db"]:
        assert getattr(actual, field).tobytes() == getattr(expected, field).tobytes()
    for field in ["cone_volume_velocity", "port_volume_velocity", "electrical_input_impedance_ohm"]:
        assert payload[field].tobytes() == old_payload[field].tobytes()
