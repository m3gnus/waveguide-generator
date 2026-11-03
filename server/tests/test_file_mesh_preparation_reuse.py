"""Reuse evidence only across unchanged preparation stages."""

from unittest.mock import patch

from server.mesh import file_import as f
from test_file_mesh_roles_symmetry import box_mesh


def test_full_domain_preparation_keeps_its_exact_integrity_report():
    mesh = box_mesh()
    expected = f.file_mesh_integrity(mesh, [])
    with patch.object(f, "_symmetry_evidence", wraps=f._symmetry_evidence) as symmetry:
        preparation = f.prepare_file_mesh_input(mesh, "m", "+z", "+y")
        assert symmetry.call_count == 1
    prepared, _ = f.finish_file_mesh_preparation(preparation)
    assert preparation.integrity == expected
    assert mesh.points.tobytes() == prepared.points.tobytes()
    assert mesh.triangles.tobytes() == prepared.triangles.tobytes()


def test_reflection_recomputes_evidence_after_geometry_changes():
    mesh = box_mesh(planes=("x0",), negative=("x0",))
    with patch.object(f, "_symmetry_evidence", wraps=f._symmetry_evidence) as symmetry:
        preparation = f.prepare_file_mesh_input(mesh, "m", "+z", "+y", domain_planes=("x0",))
        # Empty-plane detection, declared-plane validation, reflected validation.
        assert symmetry.call_count == 3
    assert preparation.integrity == f.file_mesh_integrity(preparation.mesh, ("x0",))
